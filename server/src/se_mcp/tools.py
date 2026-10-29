"""MCP tools. Naming, formatting and convenience live here so the game side can stay thin."""

from __future__ import annotations

import asyncio
import functools
import math
import re
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import Image, MCPServer

from . import autopilot
from . import build
from . import combat
from . import docking
from . import forecast as forecast_mod
from . import format as fmt
from . import hud as hud_mod
from . import items as items_mod
from . import ore as ore_mod
from . import organizer
from . import production as production_mod
from . import radio
from . import toolbar as toolbar_mod
from . import pb
from . import resolve
from . import vision
from .bridge import Bridge, BridgeError
from .resolve import ResolveError

INSTRUCTIONS = (
    "Tools for assisting a player in Space Engineers 1 through the SEMcp game plugin. "
    "Start with se_status to see whether the game is running and what the player is in or looking at. "
    "Grid and block arguments take a name (fuzzy matched) or an id; leaving `grid` empty means the ship "
    "the player is controlling or standing in. Use screenshot to see what the player sees. "
    "Before writing a programmable block script, read the se://pb-primer resource."
)


def _friendly_errors(tool: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Game and lookup errors become a short message the model can act on, not a stack trace."""

    @functools.wraps(tool)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await tool(*args, **kwargs)
        except ResolveError as e:
            return str(e)
        except BridgeError as e:
            return f"Error ({e.kind}): {e}"

    return wrapper


def create_server(bridge: Bridge | None = None) -> MCPServer:
    bridge = bridge or Bridge()
    server = MCPServer("space-engineers", instructions=INSTRUCTIONS)

    def tool(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        # Text and images only; no structured output schema (it chokes on mixed text + image lists).
        return server.tool(structured_output=False)(_friendly_errors(fn))

    @tool
    async def se_status() -> str:
        """Check the connection to Space Engineers, and what the player is controlling and looking at."""
        try:
            info = await bridge.call("ping")
        except BridgeError as e:
            return f"Not connected: {e}"

        lines = [
            f"Connected to Space Engineers {info.get('game')} ({info.get('runtime')})",
            f"Plugin host {info.get('host')}, generation {info.get('generation')}",
        ]
        if not info.get("sessionLoaded"):
            lines.append("No world loaded (main menu or loading).")
            return "\n".join(lines)

        lines.append(f"World: {info.get('world')} ({info.get('onlineMode')})")
        lines.append(f"Player: {info.get('player') or 'unknown'}")
        lines.append(fmt.context(await bridge.call("player.context")))
        return "\n".join(lines)

    @tool
    async def list_grids(range_m: float = 5000, include_unowned: bool = False) -> str:
        """List ships and stations near the player, closest first.

        include_unowned also lists grids owned by others (name and distance only)."""
        listing = await bridge.call("grids.list", {"range": range_m, "includeAll": include_unowned})
        return fmt.grids(listing)

    @tool
    async def ship_status(grid: str | None = None) -> str:
        """Status of a ship or station: power, batteries, hydrogen/oxygen, cargo fill, mass, speed, damage.

        grid: name or id; empty = the ship the player is in or nearest to."""
        target = await resolve.grid(bridge, grid)
        return fmt.ship(await bridge.call("grid.get", {"gridId": target["id"]}))

    @tool
    async def list_blocks(
        grid: str | None = None,
        type: str | None = None,
        name_contains: str | None = None,
        group: str | None = None,
        limit: int = 100,
    ) -> str:
        """List terminal blocks on a ship (including subgrids), with on/off, damage and access flags.

        type matches the block type or its display name, e.g. "Thrust", "Battery", "Refinery", "Cargo".
        group is a block group name from list_groups."""
        target = await resolve.grid(bridge, grid)
        params: dict[str, Any] = {"gridId": target["id"], "limit": limit}
        if type:
            params["type"] = type
        if name_contains:
            params["nameContains"] = name_contains
        if group:
            params["group"] = group
        return fmt.blocks(await bridge.call("blocks.list", params), target.get("name"))

    @tool
    async def inspect_block(block: str, grid: str | None = None, all_properties: bool = False) -> str:
        """Everything about one block: detailed info, terminal properties and actions, inventory, CustomData.

        block: name or id; grid narrows the name search (default: current ship)."""
        target = await resolve.block(bridge, block, grid)
        detail = await bridge.call("block.get", {"blockId": target["id"]})
        return fmt.block_detail(detail, all_properties)

    @tool
    async def looked_at() -> str:
        """What the player is looking at (crosshair, up to 200 m), with full detail if it's a usable block."""
        context = await bridge.call("player.context")
        look = context.get("lookingAt")
        if not look:
            return "Not looking at anything within 200 m."
        block = look.get("block") or {}
        if look["kind"] == "grid" and block.get("id") and block.get("accessible"):
            detail = await bridge.call("block.get", {"blockId": block["id"]})
            return f"Looking at {look['gridName']} ({look['distance']} m):\n" + fmt.block_detail(detail)
        return fmt.context({"lookingAt": look}).replace("On foot.\n", "")

    @tool
    async def list_groups(grid: str | None = None) -> str:
        """Block groups on a ship (as in the terminal), with their members."""
        target = await resolve.grid(bridge, grid)
        groups = (await bridge.call("groups.list", {"gridId": target["id"]}))["groups"]
        if not groups:
            return f"No block groups on {target.get('name') or target['id']}."
        return "\n".join(f"- {g['name']}: " + ", ".join(b["name"] for b in g["blocks"]) for g in groups)

    @tool
    async def edit_group(name: str, blocks: str | None = None, mode: str = "replace", new_name: str | None = None,
                         grid: str | None = None) -> str:
        """Create, change, rename or delete a block group, as in the terminal. blocks: comma-separated block
        names. mode: "replace" (the group becomes exactly these; creates it if new), "add", "remove", "delete".
        new_name renames it (toolbar slots that used the old name stop working, like in the game)."""
        target = await resolve.grid(bridge, grid)
        ids: list[str] = []
        for b in [x.strip() for x in (blocks or "").split(",") if x.strip()]:
            ids.append((await resolve.block(bridge, b, grid))["id"])
        params: dict[str, Any] = {"gridId": target["id"], "name": name, "mode": mode, "blockIds": ids}
        if new_name:
            params["newName"] = new_name
        return toolbar_mod.group_text(await bridge.call("group.set", params))

    async def _toolbar_owner(block: str | None, grid: str | None) -> dict[str, Any]:
        if not block or block.strip().lower() in ("me", "player", "my", "mine"):
            return {}
        return {"blockId": (await resolve.block(bridge, block, grid))["id"]}

    @tool
    async def toolbar(block: str | None = None, page: int | None = None, grid: str | None = None) -> str:
        """Show a toolbar: a cockpit/seat/remote control, button panel, timer, sensor or event controller (block),
        or the player's own (block empty or "me"). Slots are page.slot, e.g. 1.4 is page 1, key 4."""
        return toolbar_mod.toolbar_text(await bridge.call("toolbar.get", await _toolbar_owner(block, grid)), page)

    @tool
    async def set_toolbar_slot(page: int, slot: int, block: str | None = None, target_block: str | None = None,
                               target_group: str | None = None, action: str | None = None, item: str | None = None,
                               clear: bool = False, grid: str | None = None) -> str:
        """Put something in one toolbar slot (page 1-9, slot 1-9) of a block's toolbar (block) or the player's
        (block empty or "me"). Either target_block or target_group plus an action id (e.g. "OnOff", "OnOff_On",
        "Trigger", "Run", "ShootOnce"; a wrong id lists the valid ones), or item: a tool, weapon or block by its
        in-game name ("Elite Grinder", "Welder", "Ore Detector") or a definition id like
        "CubeBlock/LargeBlockArmorBlock" (player toolbar). clear=True empties the slot."""
        params: dict[str, Any] = {**await _toolbar_owner(block, grid), "page": page, "slot": slot}
        if clear:
            params["clear"] = True
        elif item:
            if "/" in item:
                params["definitionId"] = item
            else:
                # Exact in-game names (tools, weapons, blocks) first; otherwise the closest block.
                params["itemName"] = item
                try:
                    return toolbar_mod.change_text(await bridge.call("toolbar.set", params))
                except BridgeError:
                    del params["itemName"]
                found = (await bridge.call("blockdefs.find", {"query": item, "limit": 5}))["blocks"]
                if not found:
                    return f"Nothing called '{item}': give the in-game name of a tool, weapon or block, or a definition id."
                large = [f for f in found if f["size"] == "large"]
                params["definitionId"] = (large or found)[0]["id"]
        elif target_block or target_group:
            if not action:
                return "Give the action id to run (e.g. OnOff, Trigger, Run)."
            params["action"] = action
            if target_block:
                params["targetBlockId"] = (await resolve.block(bridge, target_block, grid))["id"]
            else:
                params["group"] = target_group
                params["gridId"] = (await resolve.grid(bridge, grid))["id"]
        else:
            return "Give target_block or target_group with an action, an item, or clear=True."
        return toolbar_mod.change_text(await bridge.call("toolbar.set", params))

    @tool
    async def inventory(grid: str | None = None, block: str | None = None, suit: bool = False, item: str | None = None) -> str:
        """Item totals for a whole ship (default: current ship), one block, or the player's suit (suit=True).

        item filters by name, e.g. "iron", "ice", "steel"."""
        if suit:
            inv = await bridge.call("inventory.get", {"character": True})
            return fmt.inventory(inv, "Suit inventory", item)
        if block:
            target = await resolve.block(bridge, block, grid)
            inv = await bridge.call("inventory.get", {"blockId": target["id"]})
            return fmt.inventory(inv, target.get("name") or f"Block {target['id']}", item)
        target = await resolve.grid(bridge, grid)
        inv = await bridge.call("inventory.get", {"gridId": target["id"]})
        return fmt.inventory(inv, f"{target.get('name') or target['id']} (all inventories)", item)

    @tool
    async def suit_status() -> str:
        """The player's suit: health, energy, oxygen, hydrogen, jetpack, helmet, gravity, outside air."""
        return fmt.suit(await bridge.call("character.status"))

    @tool
    async def suit_controls(helmet: bool | None = None, jetpack: bool | None = None, dampeners: bool | None = None,
                            lights: bool | None = None) -> str:
        """Switch the player's helmet, jetpack, inertia dampeners or suit lights on/off, like pressing J/X/Z/L.
        Only the ones given change. Careful: opening the helmet in vacuum or killing the jetpack mid-flight hurts."""
        params = {k: v for k, v in (("helmet", helmet), ("jetpack", jetpack), ("dampeners", dampeners), ("lights", lights)) if v is not None}
        if not params:
            return "Say which to switch: helmet, jetpack, dampeners and/or lights."
        r = await bridge.call("character.switch", params)
        state = ", ".join(f"{k} {'on' if r[k] else 'off'}" for k in ("helmet", "jetpack", "dampeners", "lights"))
        return (f"Switched {', '.join(r['changed'])}. " if r["changed"] else "Already like that. ") + f"Now: {state}."

    @tool
    async def save_world() -> str:
        """Save the world now (like Ctrl+S), as a restore point before something risky. The game keeps earlier
        saves in the world's Backup folder. Single player or host only; takes a few seconds and hitches the game."""
        r = await bridge.call("session.save", timeout=120)
        if not r["saved"]:
            return "The game reported the save failed (see the SE log)."
        return (f"Saved '{r['world']}' in {r['seconds']:g} s. Earlier versions are in its Backup folder "
                f"(keeps {r['backups']}). Path: {r['path']}")

    # --- Environment and forecasts. ---

    @tool
    async def environment(grid: str | None = None, nearby: bool = False) -> str:
        """Where you are: natural and artificial gravity, the nearest planet or moon (altitude, atmosphere, air,
        oxygen, sun height for day/night), weather, and day length. nearby=True also lists planets and asteroids."""
        params: dict[str, Any] = {}
        if grid:
            params["gridId"] = (await resolve.grid(bridge, grid))["id"]
        env = await bridge.call("environment.get", params)
        lines = [f"Gravity: {env['naturalGravityG']:.2f} g natural, {env['artificialGravityG']:.2f} g artificial."]
        p = env.get("planet")
        if p:
            air = (f"in atmosphere (air density {p['airDensity']:.2f}, oxygen {p['oxygen'] * 100:.0f}%)" if p["inAtmosphere"]
                   else "above the atmosphere" if p["hasAtmosphere"] else "no atmosphere")
            daylight = "daytime" if p["sunElevation"] > 0 else "night"
            lines.append(f"Nearest planet: {p['name']} ({p['radiusKm']:.0f} km radius, {p['surfaceGravityG']} g at the surface). "
                         f"Altitude {p['altitude']:,.0f} m, {air}.")
            lines.append(f"Sun {p['sunElevation']:+.0f} deg above the horizon ({daylight}); a day is {env['dayLengthMinutes']:.0f} min.")
            w = env.get("weather")
            if w and w["name"] != "Clear":
                lines.append(f"Weather: {w['name']} (intensity {w['intensity']:.1f}; solar x{w['solar']}, wind x{w['wind']}, oxygen x{w['oxygen']}).")
        else:
            lines.append(f"Deep space: no planet nearby. A day (sun rotation) is {env['dayLengthMinutes']:.0f} min.")
        if nearby:
            bodies = await bridge.call("environment.bodies", {"limit": 10})
            for pl in bodies["planets"]:
                lines.append(f"- planet {pl['name']}: {pl['distanceKm']:,.1f} km to the surface, {pl['radiusKm']:.0f} km radius, "
                             f"{pl['surfaceGravityG']} g{', atmosphere' if pl['hasAtmosphere'] else ''}")
            for a in bodies["asteroids"]:
                lines.append(f"- asteroid {a['name']}: {a['distanceKm'] * 1000:,.0f} m away, ~{a['sizeM']:.0f} m across")
            if not bodies["planets"] and not bodies["asteroids"]:
                lines.append("Nothing nearby.")
        return "\n".join(lines)

    @tool
    async def forecast(grid: str | None = None, sample_seconds: float = 5, include_suit: bool = True) -> str:
        """How long things last at the current rate: batteries, reactor uranium, hydrogen and oxygen tanks, and
        the suit. Samples twice, sample_seconds apart, to measure gas and suit usage."""
        target = await resolve.grid(bridge, grid)
        first = await bridge.call("grid.get", {"gridId": target["id"]})
        suit_first = await bridge.call("character.status") if include_suit else None
        await asyncio.sleep(max(1.0, min(sample_seconds, 30)))
        second = await bridge.call("grid.get", {"gridId": target["id"]})
        suit_second = await bridge.call("character.status") if include_suit else None
        totals = production_mod.ship_totals(await bridge.call("inventory.get", {"gridId": target["id"]}))
        dt = max(1.0, min(sample_seconds, 30))
        gas_before, gas_after = first.get("gas") or {}, second.get("gas") or {}
        lines = [f"{target.get('name') or 'Ship'} (measured over {dt:g} s):"]
        for line in (forecast_mod.batteries(second),
                     forecast_mod.reactors(second, totals.get(("Ingot", "Uranium"), 0.0)),
                     forecast_mod.gas("Hydrogen", gas_before.get("hydrogen"), gas_after.get("hydrogen"), dt),
                     forecast_mod.gas("Oxygen", gas_before.get("oxygen"), gas_after.get("oxygen"), dt)):
            if line:
                lines.append(line)
        if include_suit and suit_first and suit_second and suit_second.get("alive", True):
            lines += forecast_mod.suit(suit_first, suit_second, dt)
        return "\n".join(lines)

    # --- Highlights: make blocks glow for the player. ---

    @tool
    async def highlight_blocks(blocks: str | None = None, type: str | None = None, group: str | None = None,
                               damaged: bool = False, color: str = "#FFD000", pulse: bool = True, grid: str | None = None) -> str:
        """Make blocks glow so the player can find them (only they see it). Pick by blocks (names, comma-separated),
        type ("thruster", "battery"), group, and/or damaged=True (anything not at full integrity). Functional blocks
        only: plain armor can't glow. Clear with clear_highlights."""
        target = await resolve.grid(bridge, grid)
        chosen: dict[str, str] = {}
        if blocks:
            for name in [b.strip() for b in blocks.split(",") if b.strip()]:
                b = await resolve.block(bridge, name, grid)
                chosen[b["id"]] = b.get("name") or name
        if type or group or damaged or not blocks:
            params: dict[str, Any] = {"gridId": target["id"], "limit": 1000}
            if type:
                params["type"] = type
            if group:
                params["group"] = group
            if type or group or damaged:
                for b in (await bridge.call("blocks.list", params))["blocks"]:
                    if not damaged or b.get("integrity", 100) < 100:
                        chosen[b["id"]] = b["name"]
        if not chosen:
            return "Nothing matched (no damaged functional blocks?)." if damaged else "Nothing matched."
        r = await bridge.call("highlight.set", {"blockIds": list(chosen), "color": color, "pulseFrames": 60 if pulse else 0})
        names = r["highlighted"]
        text = f"Highlighted {len(names)} block(s): " + ", ".join(names[:20]) + ("..." if len(names) > 20 else "")
        if r["skipped"]:
            text += f"\nSkipped: {'; '.join(r['skipped'][:5])}"
        return text

    @tool
    async def clear_highlights() -> str:
        """Turn off every highlight made with highlight_blocks."""
        r = await bridge.call("highlight.clear", {})
        return f"Cleared {r['cleared']} highlight(s)."

    @tool
    async def production(grid: str | None = None) -> str:
        """Assemblers, refineries and survival kits on a ship: producing or not, queues, input/output fill."""
        target = await resolve.grid(bridge, grid)
        return fmt.production(await bridge.call("production.get", {"gridId": target["id"]}))

    @tool
    async def recipe(item: str, amount: float = 1, grid: str | None = None) -> str:
        """How to make an item in this world: ingredients for `amount` (with the world's assembler efficiency
        applied), time, which blocks make it, and whether the ship has enough. item: e.g. "steel plate", "iron ingot"."""
        book = await production_mod.recipes(bridge)
        wanted = production_mod.resolve_item(item, book)
        have = None
        try:
            target = await resolve.grid(bridge, grid)
            have = production_mod.ship_totals(await bridge.call("inventory.get", {"gridId": target["id"]}))
        except ResolveError:
            pass
        name = items_mod.display(*wanted)
        lines = []
        for r in production_mod.producing(wanted, book):
            runs = production_mod.runs_for(r, wanted, amount)
            needs = production_mod.inputs_for(r, runs, book)
            ingredient_text, short = production_mod.ingredient_lines(needs, have)
            secs = production_mod.seconds_for(r, runs, book)
            others = [o for o in r["outputs"] if (o["type"], o["subtype"]) != wanted]
            lines.append(f"{items_mod.amount_text(amount, wanted[0])} {name} via '{r['name']}' in {', '.join(r['madeIn'])}, "
                         f"~{production_mod.duration(secs)} on one block at world speed:")
            lines += ingredient_text
            if others:
                lines.append("  also yields: " + ", ".join(f"{items_mod.amount_text(o['amount'] * runs, o['type'])} {items_mod.display(o['type'], o['subtype'])}" for o in others))
            if short:
                lines.append("  short: " + ", ".join(short))
        return "\n".join(lines) if lines else f"Nothing makes {name}."

    @tool
    async def queue_production(item: str, amount: float = 1, assembler: str | None = None, grid: str | None = None) -> str:
        """Queue items in an assembler (or survival kit / food processor), like clicking them in the production
        screen. assembler: block name; empty = the working one on the ship with the shortest queue.
        Reports ingredients the ship is short of (the queue still waits for them)."""
        book = await production_mod.recipes(bridge)
        wanted = production_mod.resolve_item(item, book)
        candidates = [r for r in production_mod.producing(wanted, book) if not production_mod.is_refining(r)]
        if not candidates:
            return f"{items_mod.display(*wanted)} isn't made in an assembler (refineries make it from ore automatically)."
        r = candidates[0]
        target = await resolve.grid(bridge, grid)
        block = await production_mod.pick_producer(bridge, target["id"], r, assembler)
        runs = production_mod.runs_for(r, wanted, amount)
        await bridge.call("production.queue", {"blockId": block["id"], "blueprint": r["id"], "amount": runs})
        have = production_mod.ship_totals(await bridge.call("inventory.get", {"gridId": target["id"]}))
        _, short = production_mod.ingredient_lines(production_mod.inputs_for(r, runs, book), have)
        secs = production_mod.seconds_for(r, runs, book)
        text = (f"Queued {items_mod.amount_text(amount, wanted[0])} {items_mod.display(*wanted)} in {block['name']} "
                f"(~{production_mod.duration(secs)} at world speed, before upgrades or cooperative help).")
        if short:
            text += "\nShort of: " + ", ".join(short) + ". It'll wait for those."
        return text

    @tool
    async def remove_from_queue(assembler: str, position: int | None = None, item: str | None = None,
                                amount: float | None = None, grid: str | None = None) -> str:
        """Remove an entry from a production queue: by position (#0 is first, as shown by production) or by item.
        amount: how many to take off; empty = the whole entry."""
        target = await resolve.grid(bridge, grid)
        blocks = (await bridge.call("production.get", {"gridId": target["id"]}))["blocks"]
        matches = [b for b in blocks if assembler.strip().lower() in b["name"].lower() or b["id"] == assembler.strip()]
        if not matches:
            return f"No production block matching '{assembler}'."
        block = matches[0]
        queue = block["queue"]
        if position is None:
            if not item:
                return "Give a position or an item."
            hits = [q for q in queue if items_mod.match(item, {("Blueprint", q["blueprint"])})]
            if not hits:
                return f"No '{item}' in {block['name']}'s queue: {', '.join(q['blueprint'] for q in queue) or 'empty'}."
            position = hits[0]["index"]
        params: dict[str, Any] = {"blockId": block["id"], "index": position}
        if amount is not None:
            params["amount"] = amount
        r = await bridge.call("production.remove", params)
        return f"Removed {r['amount']:g} {r['removed']} from {r['block']}'s queue."

    @tool
    async def clear_production_queue(assembler: str, grid: str | None = None) -> str:
        """Empty a production block's whole queue."""
        target = await resolve.block(bridge, assembler, grid)
        r = await bridge.call("production.clear", {"blockId": target["id"]})
        return f"Cleared {r['block']}'s queue."

    # --- Build planning. ---

    async def _ship_have(grid: str | None) -> tuple[dict[str, Any] | None, dict[tuple[str, str], float] | None]:
        try:
            target = await resolve.grid(bridge, grid)
        except ResolveError:
            return None, None
        return target, production_mod.ship_totals(await bridge.call("inventory.get", {"gridId": target["id"]}))

    async def _queue_components(target: dict[str, Any], needs: dict[tuple[str, str], float], book: dict[str, Any]) -> list[str]:
        lines = []
        for key, count in sorted(needs.items(), key=lambda kv: -kv[1]):
            recipes = [r for r in production_mod.producing(key, book) if not production_mod.is_refining(r)]
            name = items_mod.display(*key)
            if not recipes:
                lines.append(f"- {name}: no assembler recipe")
                continue
            r = recipes[0]
            try:
                block = await production_mod.pick_producer(bridge, target["id"], r, None)
                runs = production_mod.runs_for(r, key, round(count))
                await bridge.call("production.queue", {"blockId": block["id"], "blueprint": r["id"], "amount": runs})
                lines.append(f"- queued {round(count)} {name} in {block['name']}")
            except (ResolveError, BridgeError) as e:
                lines.append(f"- {name}: {e}")
        return lines

    @tool
    async def block_cost(block: str, count: int = 1, size: str | None = None, depth: str = "ore", grid: str | None = None) -> str:
        """What it takes to build blocks: components, then the ingots to assemble them (world efficiency) and the
        ore to refine those, each compared with what the ship has. E.g. block="gyroscope", or "refinery", count=10.

        size: "large" or "small" grid (default: the best name match, usually large). depth: "components", "ingots" or "ore"."""
        found = (await bridge.call("blockdefs.find", {"query": block, **({"size": size} if size else {}), "limit": 8}))["blocks"]
        if not found:
            return f"No block called '{block}'."
        chosen = found[0]
        book = await production_mod.recipes(bridge)
        _, have = await _ship_have(grid)
        components = build.add({}, chosen["components"])
        components = {k: v * count for k, v in components.items()}
        others = [f"{b['name']} ({b['size']})" for b in found[1:6]]
        head = f"{count}x {chosen['name']} ({chosen['size']} grid, {chosen['pcu'] * count} PCU)"
        text = head + "\n" + build.breakdown(components, book, have, depth)
        if others:
            text += "\nOther matches: " + ", ".join(others)
        return text

    @tool
    async def build_planner(action: str = "show", grid: str | None = None) -> str:
        """The player's build planner (blocks added from the G menu) and the components it needs.

        action: "show" (needs vs. what the ship has), "withdraw" (move the components from the ship into the
        suit, through conveyors from where the player is, like the Withdraw button), or "queue" (queue what's
        missing in the ship's assemblers)."""
        planner = (await bridge.call("buildplanner.get"))["items"]
        if not planner:
            return "Your build planner is empty (add blocks from the G menu)."
        needs: dict[tuple[str, str], float] = {}
        for item in planner:
            build.add(needs, item["components"])
        target, have = await _ship_have(grid)
        blocks = ", ".join(f"{i['block']} ({i['size']})" for i in planner)
        if action == "show":
            book = await production_mod.recipes(bridge)
            return f"Build planner: {blocks}\n" + build.breakdown(needs, book, have, "components")
        if target is None:
            return "Stand in or near a ship so I know whose cargo and assemblers to use."
        if action == "queue":
            book = await production_mod.recipes(bridge)
            missing = build.queue_list(needs, have or {})
            if not missing:
                return "The ship already has every component the build planner needs."
            return "Queued what's missing for the build planner:\n" + "\n".join(await _queue_components(target, missing, book))
        if action == "withdraw":
            suit = await bridge.call("inventory.get", {"character": True})
            in_suit = production_mod.ship_totals(suit)
            layout = await bridge.call("inventory.layout", {"gridId": target["id"]})
            lines = []
            for key, count in needs.items():
                want = count - in_suit.get(key, 0.0)
                name = items_mod.display(*key)
                if want <= 0:
                    lines.append(f"- {name}: already in your suit")
                    continue
                holders = sorted(((i, x["amount"]) for i in layout["inventories"] for x in i["items"]
                                  if (x["type"], x["subtype"]) == key), key=lambda h: -h[1])
                got, problem = 0.0, None
                for inv, amount in holders:
                    if got >= want:
                        break
                    try:
                        r = await bridge.call("inventory.transfer", {
                            "fromBlockId": inv["blockId"], "fromIndex": inv["index"], "toCharacter": True,
                            "type": key[0], "subtype": key[1], "amount": min(amount, want - got)})
                    except BridgeError as e:
                        problem = str(e)
                        continue
                    got += r["moved"]
                    problem = r["reason"] or problem
                status = f"{got:g}/{want:g}" + (f" ({problem})" if got < want and problem else " (not on the ship)" if got < want else "")
                lines.append(f"- {name}: {status}")
            return "Withdrew build planner components into your suit:\n" + "\n".join(lines)
        return "action must be show, withdraw or queue."

    @tool
    async def projector_status(projector: str | None = None, queue_missing: bool = False, grid: str | None = None) -> str:
        """A projector's build progress: blocks left, buildable now, the components still needed (unplaced blocks
        plus unfinished welds on the ship) and what the ship is short of. queue_missing=True queues the shortfall."""
        target = await resolve.grid(bridge, grid)
        if projector:
            pj = await resolve.block(bridge, projector, grid)
        else:
            found = (await bridge.call("blocks.list", {"gridId": target["id"], "type": "Projector"}))["blocks"]
            if not found:
                return f"No projector on {target.get('name') or 'this ship'}."
            pj = found[0]
        info = await bridge.call("projector.get", {"blockId": pj["id"]})
        if not info["projecting"]:
            return f"{info['name']} isn't projecting anything" + ("" if info["working"] else " (and isn't working)") + "."
        needs: dict[tuple[str, str], float] = {}
        for b in info["remaining"]:
            build.add(needs, b.get("components") or [])
        build.add(needs, info["unfinishedComponents"])
        have = production_mod.ship_totals(await bridge.call("inventory.get", {"gridId": target["id"]}))
        book = await production_mod.recipes(bridge)
        done = info["totalBlocks"] - info["remainingBlocks"]
        lines = [f"{info['name']}: {info['projectedGrid'] or 'projection'}, {done}/{info['totalBlocks']} blocks placed, "
                 f"{info['remainingBlocks']} left ({info['remainingArmorBlocks']} armor), {info['buildableNow']} buildable now."]
        top = sorted(info["remaining"], key=lambda b: -b["count"])[:8]
        if top:
            lines.append("Left to place: " + ", ".join(f"{b['count']}x {b['name']}" for b in top) + ("..." if len(info["remaining"]) > 8 else ""))
        if info["unfinishedBlocks"]:
            lines.append(f"Plus {info['unfinishedBlocks']} placed blocks that aren't fully welded.")
        lines.append(build.breakdown(needs, book, have, "components"))
        if queue_missing:
            missing = build.queue_list(needs, have)
            lines.append("Queued:\n" + "\n".join(await _queue_components(target, missing, book)) if missing else "Nothing to queue: the ship has it all.")
        return "\n".join(lines)

    # --- Screens: LCDs, cockpit and PB screens. ---

    def _surface_line(s: dict[str, Any]) -> str:
        if s["content"] == "SCRIPT":
            what = f"script {s['script']}"
        elif s["content"] == "TEXT_AND_IMAGE":
            shown = []
            if s["textLength"]:
                shown.append(f"text ({s['textLength']} chars): {s['text']!r}")
            if s["images"]:
                shown.append("images " + ", ".join(s["images"]))
            what = "; ".join(shown) or "text mode, empty"
        else:
            what = "off"
        return f"  #{s['index']} {s['name']}: {what}"

    async def _surface_index(block_id: str, surface: str | int | None) -> int:
        if surface is None or isinstance(surface, int) or str(surface).isdigit():
            return int(surface or 0)
        listing = (await bridge.call("screens.list", {"blockId": block_id}))["blocks"][0]["surfaces"]
        hits = [s for s in listing if str(surface).lower() in s["name"].lower()]
        if not hits:
            raise ResolveError(f"No screen '{surface}'. Screens: " + ", ".join(f"#{s['index']} {s['name']}" for s in listing))
        return hits[0]["index"]

    @tool
    async def list_screens(grid: str | None = None, block: str | None = None) -> str:
        """Screens on a ship (LCDs, cockpits, programmable blocks...): what each shows (text, images, a script
        like the artificial horizon, or off). With block: that block's screens with their full text."""
        if block:
            target = await resolve.block(bridge, block, grid)
            listing = await bridge.call("screens.list", {"blockId": target["id"]})
        else:
            target = await resolve.grid(bridge, grid)
            listing = await bridge.call("screens.list", {"gridId": target["id"]})
        if not listing["blocks"]:
            return "No screens."
        lines = []
        for b in listing["blocks"]:
            lines.append(f"- {b['name']}" + ("" if b["working"] else " (not working)"))
            lines += [_surface_line(s) for s in b["surfaces"]]
        return "\n".join(lines)

    @tool
    async def write_screen(block: str, text: str, surface: str | int | None = None, append: bool = False,
                           font_size: float | None = None, color: str | None = None, background: str | None = None,
                           align: str | None = None, grid: str | None = None) -> str:
        """Show text on an LCD or one of a cockpit's screens (switches it to text mode, replacing a script there).

        surface: screen number or name ("top left") for blocks with several; default the first. color and
        background: "#RRGGBB". align: left, center, right. Tell the player if you replaced a script; restore it
        with set_screen_mode."""
        target = await resolve.block(bridge, block, grid)
        params: dict[str, Any] = {"blockId": target["id"], "surface": await _surface_index(target["id"], surface),
                                  "text": text, "append": append}
        for key, value in (("fontSize", font_size), ("fontColor", color), ("background", background), ("alignment", align)):
            if value is not None:
                params[key] = value
        r = await bridge.call("screen.write", params)
        before = r["before"]
        note = ""
        if before["content"] == "SCRIPT":
            note = f" It was showing the script {before['script']} (restore with set_screen_mode)."
        elif before["textLength"] and not append:
            note = f" Replaced {before['textLength']} chars of text."
        return f"Wrote to {r['block']} #{r['after']['index']} {r['after']['name']}.{note}"

    @tool
    async def set_screen_mode(block: str, mode: str, script: str | None = None, surface: str | int | None = None,
                              grid: str | None = None) -> str:
        """Switch a screen to "text", "script" (script: e.g. TSS_ArtificialHorizon, TSS_EnergyHydrogen,
        TSS_Gravity, TSS_ClockAnalog, TSS_ClockDigital, TSS_FactionIcon, TSS_TargetingInfo, TSS_Weather), or "none" (off)."""
        target = await resolve.block(bridge, block, grid)
        params: dict[str, Any] = {"blockId": target["id"], "surface": await _surface_index(target["id"], surface), "mode": mode}
        if script:
            params["script"] = script
        r = await bridge.call("screen.mode", params)
        return f"{r['block']} #{r['after']['index']} {r['after']['name']}: now {_surface_line(r['after']).split(': ', 1)[1]}."

    # --- Inventory management. Moves go through conveyors, like dragging items in the terminal. ---

    SUIT_WORDS = {"suit", "me", "my suit", "player", "character", "inventory", "my inventory"}

    async def _layout(grid: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        target = await resolve.grid(bridge, grid)
        return target, await bridge.call("inventory.layout", {"gridId": target["id"]})

    async def _endpoint(name: str, layout: dict[str, Any], grid: str | None) -> dict[str, Any] | None:
        """None = the player's suit; else the block's inventories from the layout."""
        if name.strip().lower() in SUIT_WORDS:
            return None
        block = await resolve.block(bridge, name, grid)
        invs = [i for i in layout["inventories"] if i["blockId"] == block["id"]]
        if not invs:
            raise ResolveError(f"'{block.get('name') or name}' has no inventory I can use.")
        return {"name": invs[0]["name"], "id": block["id"], "inventories": invs}

    @tool
    async def find_items(item: str, grid: str | None = None) -> str:
        """Where an item is on a ship: amount per container/block, biggest first. item: e.g. "steel plates", "ice"."""
        target, layout = await _layout(grid)
        organizer.annotate(layout)
        known = {k for i in layout["inventories"] for k in items_mod.item_keys(i["items"])}
        found = items_mod.match(item, known)
        if not found:
            return f"No '{item}' anywhere on {target.get('name') or 'the ship'}."
        lines = []
        for t, s in found[:6]:
            holders = [(i, x["amount"]) for i in layout["inventories"] for x in i["items"] if (x["type"], x["subtype"]) == (t, s)]
            holders.sort(key=lambda h: -h[1])
            total = sum(a for _, a in holders)
            lines.append(f"{items_mod.display(t, s)}: {items_mod.amount_text(total, t)} total")
            lines += [f"  - {organizer.label(i)}: {items_mod.amount_text(a, t)}" for i, a in holders[:15]]
        return "\n".join(lines)

    @tool
    async def move_items(item: str, from_block: str, to_block: str, amount: float | None = None, grid: str | None = None) -> str:
        """Move items between two inventories, like dragging them in the terminal.

        from_block / to_block: block names or ids, or "suit" for the player's own inventory. amount: count
        (or kg for ore/ingots); empty = all of it. Block-to-block moves need a conveyor connection. Suit moves
        need the player next to the block or seated on that ship."""
        target, layout = await _layout(grid)
        src = await _endpoint(from_block, layout, grid)
        dst = await _endpoint(to_block, layout, grid)
        if src is None:
            suit = await bridge.call("inventory.get", {"character": True})
            known = {(t["type"], t["subtype"]) for t in suit["totals"]}
            where = "your suit"
        else:
            known = {k for i in src["inventories"] for k in items_mod.item_keys(i["items"])}
            where = src["name"]
        t, s = items_mod.resolve_one(item, known, where)

        params: dict[str, Any] = {"type": t, "subtype": s}
        if amount is not None:
            params["amount"] = amount
        if src is None:
            params["fromCharacter"] = True
        else:
            # Take from whichever inventory holds it (production blocks: prefer the output).
            holding = [i for i in src["inventories"] if (t, s) in items_mod.item_keys(i["items"])]
            holding.sort(key=lambda i: i.get("role") != "output")
            params["fromBlockId"], params["fromIndex"] = src["id"], holding[0]["index"]
        if dst is None:
            params["toCharacter"] = True
        else:
            params["toBlockId"], params["toIndex"] = dst["id"], dst["inventories"][0]["index"]

        r = await bridge.call("inventory.transfer", params)
        name = items_mod.display(t, s)
        moved = items_mod.amount_text(r["moved"], t)
        route = f" ({r['route']})" if r.get("route") else ""
        text = f"Moved {moved} {name}: {r['from']} -> {r['to']}{route}."
        if r.get("pending"):
            return f"Asked the server to move {moved} {name}: {r['from']} -> {r['to']}{route}. It shows up in a moment."
        if r["reason"]:
            text = (f"Moved {moved} of {items_mod.amount_text(r['requested'], t)} {name}. " if r["moved"] else "Nothing moved. ") + r["reason"] + "."
        return text + f" {items_mod.amount_text(r['leftInSource'], t)} left in {r['from']}."

    @tool
    async def organize_inventory(grid: str | None = None, apply: bool = False) -> str:
        """Tidy a ship's inventories. First call (apply=False) only shows the plan; call again with
        apply=True to carry it out (show the player the plan and get a yes first).

        Containers tagged in their name or CustomData get their category: [Ore] [Ice] [Ingots] [Components]
        [Ammo] [Tools] [Bottles] [Consumables]; [Ignore] keeps a block out of it. Stray items in connectors
        and production outputs go to storage. Empty reactors get uranium and empty O2/H2 generators get ice."""
        target, layout = await _layout(grid)
        p = organizer.plan(layout)
        text = organizer.describe(p, layout["gridName"])
        if not apply or not p.moves:
            return text + ("\n(Plan only. Run again with apply=True to do it.)" if p.moves else "")
        done, problems = [], []
        for m in p.moves:
            try:
                r = await bridge.call("inventory.transfer", organizer.transfer_params(m))
            except BridgeError as e:
                problems.append(f"{m.describe()}: {e}")
                continue
            if r["moved"]:
                done.append(f"{items_mod.amount_text(r['moved'], m.type)} {items_mod.display(m.type, m.subtype)} -> {organizer.label(m.dest)}")
            if r["reason"] and (m.amount is None or r["moved"] < (m.amount or 0)):
                problems.append(f"{items_mod.display(m.type, m.subtype)} from {organizer.label(m.source)}: {r['reason']}")
        lines = [f"Organized {layout['gridName']}: {len(done)} move(s) done."] + [f"- {d}" for d in done]
        lines += [f"! {x}" for x in problems] + [f"Note: {n}" for n in p.notes]
        return "\n".join(lines)

    # --- Control. These change the game like a player using the terminal would. ---

    @tool
    async def block_action(block: str, action: str, grid: str | None = None) -> str:
        """Run a terminal action on one block, e.g. OnOff_On, OnOff_Off, Lock, Unlock, ShootOnce, Open_On.

        inspect_block lists a block's actions. block: name or id; grid: default current ship."""
        target = await resolve.block(bridge, block, grid)
        result = await bridge.call("block.action", {"blockId": target["id"], "action": action})
        return fmt.change_result(result, action)

    @tool
    async def group_action(group: str, action: str, grid: str | None = None) -> str:
        """Run a terminal action on every block in a block group (see list_groups)."""
        target = await resolve.grid(bridge, grid)
        result = await bridge.call("block.action", {"gridId": target["id"], "group": group, "action": action})
        return fmt.change_result(result, action)

    @tool
    async def set_block_property(block: str, property: str, value: str | float | bool, grid: str | None = None) -> str:
        """Set a terminal property, e.g. Override (thrust %), Velocity (rotor), Radius, Color "#FF8800".

        inspect_block lists properties with their types. Numbers are clamped to the block's limits."""
        target = await resolve.block(bridge, block, grid)
        result = await bridge.call("block.set_property", {"blockId": target["id"], "property": property, "value": value})
        return fmt.change_result(result, f"set {property}")

    @tool
    async def toggle_blocks(
        on: bool,
        grid: str | None = None,
        name_contains: str | None = None,
        group: str | None = None,
        type: str | None = None,
    ) -> str:
        """Turn several blocks on or off at once, chosen by name_contains, group and/or type (at least one)."""
        if not (name_contains or group or type):
            return "Give at least one of name_contains, group or type, so this doesn't switch the whole ship."
        target = await resolve.grid(bridge, grid)
        params: dict[str, Any] = {"gridId": target["id"], "limit": 1000}
        for key, value in (("nameContains", name_contains), ("group", group), ("type", type)):
            if value:
                params[key] = value
        blocks = (await bridge.call("blocks.list", params))["blocks"]

        action = "OnOff_On" if on else "OnOff_Off"
        changed, unchanged, failed = [], [], []
        for b in blocks:
            if b.get("enabled") is None or not b.get("accessible", True):
                failed.append(f"{b['name']} (can't be switched)")
            elif b["enabled"] == on:
                unchanged.append(b["name"])
            else:
                try:
                    await bridge.call("block.action", {"blockId": b["id"], "action": action})
                    changed.append(b["name"])
                except BridgeError as e:
                    failed.append(f"{b['name']} ({e})")
        state = "on" if on else "off"
        lines = [f"Turned {state}: {', '.join(changed) if changed else 'nothing'}"]
        if unchanged:
            lines.append(f"Already {state}: {', '.join(unchanged)}")
        if failed:
            lines.append(f"Skipped: {', '.join(failed)}")
        if not blocks:
            lines = ["No blocks matched."]
        return "\n".join(lines)

    @tool
    async def rename_block(block: str, new_name: str, grid: str | None = None) -> str:
        """Rename a block (its terminal name)."""
        target = await resolve.block(bridge, block, grid)
        result = await bridge.call("block.set_name", {"blockId": target["id"], "value": new_name})
        return f"Renamed '{result['before']}' to '{result['after']}'."

    @tool
    async def set_custom_data(block: str, data: str, grid: str | None = None) -> str:
        """Replace a block's CustomData text (often used to configure scripts). Read it first with inspect_block."""
        target = await resolve.block(bridge, block, grid)
        result = await bridge.call("block.set_custom_data", {"blockId": target["id"], "value": data})
        return f"CustomData of '{result['block']}' set ({result['beforeLength']} -> {result['afterLength']} chars)."

    @tool
    async def say(text: str, chat: bool = False, seconds: float = 5) -> str:
        """Show the player a message in-game: a HUD notification (default) or a chat line (chat=True)."""
        params: dict[str, Any] = {"text": text, "kind": "chat" if chat else "hud", "durationMs": int(seconds * 1000)}
        await bridge.call("notify", params)
        return "Shown in chat." if chat else "Shown on the HUD."

    # --- Programmable blocks. ---

    async def _pb(block: str, grid: str | None) -> dict[str, Any]:
        target = await resolve.block(bridge, block, grid)
        return await bridge.call("pb.get", {"blockId": target["id"]})

    @tool
    async def read_pb_code(block: str, grid: str | None = None) -> str:
        """The script in a programmable block, plus its current output (Echo text, compile or runtime errors)."""
        state = await _pb(block, grid)
        code = state.get("code") or ""
        header = f"{pb.status_line(state)} {len(code)} chars."
        if state.get("terminalArgument"):
            header += f" Default run argument: {state['terminalArgument']!r}."
        body = f"```csharp\n{code}\n```" if code.strip() else "(no script)"
        return f"{header}\n{body}\nOutput:\n{pb.output_text(state)}"

    @tool
    async def write_pb_script(block: str, code: str, grid: str | None = None, run_argument: str | None = None) -> str:
        """Replace a programmable block's script and compile it. Returns the compile result.

        code is the body of the Program class (no namespace/class/usings); read the se://pb-primer resource
        first if unsure. The old script is backed up to a local file first. run_argument, if given, runs the
        script once after a clean compile."""
        state = await _pb(block, grid)
        saved = pb.backup(state, state.get("code") or "")
        state = await bridge.call("pb.set_code", {"blockId": state["id"], "code": code})
        state = await pb.wait_for_output(bridge, state["id"])
        lines = [pb.status_line(state), "Output:", pb.output_text(state)]
        if saved:
            lines.append(f"(Previous script backed up to {saved})")
        if run_argument is not None and state.get("hasCompileErrors") is False and state.get("working"):
            lines.append("")
            lines.append(await run_pb(block=state["id"], argument=run_argument))
        return "\n".join(lines)

    @tool
    async def run_pb(block: str, argument: str = "", grid: str | None = None) -> str:
        """Run a programmable block once with an argument, like pressing Run in the terminal. Returns its output."""
        target = await resolve.block(bridge, block, grid)
        await bridge.call("pb.run", {"blockId": target["id"], "argument": argument})
        state = await pb.wait_for_output(bridge, target["id"])
        return f"Ran {state.get('name')} with argument {argument!r}. Output:\n{pb.output_text(state)}"

    @tool
    async def pb_output(block: str, grid: str | None = None) -> str:
        """A programmable block's current output (its terminal info: Echo text, errors). Scripts that run on a
        timer (UpdateFrequency) refresh this on their own."""
        state = await _pb(block, grid)
        return f"{pb.status_line(state)}\n{pb.output_text(state)}"

    @tool
    async def recompile_pb(block: str, grid: str | None = None) -> str:
        """Recompile a programmable block's current script (restarts it, and clears a crashed script)."""
        target = await resolve.block(bridge, block, grid)
        await bridge.call("pb.recompile", {"blockId": target["id"]})
        state = await pb.wait_for_output(bridge, target["id"])
        return f"{pb.status_line(state)}\nOutput:\n{pb.output_text(state)}"

    # --- Autopilot and GPS. ---

    @tool
    async def fly_to(
        target: str,
        speed_limit: float = 50,
        collision_avoidance: bool = True,
        precision: bool = False,
        remote_control: str | None = None,
        grid: str | None = None,
    ) -> str:
        """Fly a ship to a point with its Remote Control's autopilot. Returns once autopilot is engaged; check
        progress with autopilot_status.

        target: a GPS entry name (see list_gps), a pasted "GPS:Name:x:y:z:" string, or "x, y, z".
        speed_limit in m/s. precision slows down for an exact stop (docking). The ship flies nose-first
        (the RC's leading face is set to match the cockpit). remote_control: needed only if there are several."""
        rc = await autopilot.remote_control(bridge, remote_control, grid)
        name, coords = await autopilot.resolve_target(bridge, target)
        rc = await bridge.call("rc.fly_to", {
            "blockId": rc["id"], "target": coords, "name": name, "speedLimit": speed_limit,
            "collisionAvoidance": collision_avoidance, "precision": precision,
        })
        return "Autopilot engaged.\n" + autopilot.status(rc)

    @tool
    async def autopilot_status(remote_control: str | None = None, grid: str | None = None) -> str:
        """Where the autopilot is heading, distance left, speed and settings."""
        return autopilot.status(await autopilot.remote_control(bridge, remote_control, grid))

    @tool
    async def stop_autopilot(remote_control: str | None = None, grid: str | None = None) -> str:
        """Turn the autopilot off (the ship's dampeners then slow it down, if they're on)."""
        rc = await autopilot.remote_control(bridge, remote_control, grid)
        rc = await bridge.call("rc.stop", {"blockId": rc["id"]})
        return "Autopilot off.\n" + autopilot.status(rc)

    @tool
    async def list_gps(name_contains: str | None = None) -> str:
        """The player's GPS entries, closest first, with distance and a copyable GPS string."""
        entries = (await bridge.call("gps.list"))["gps"]
        if name_contains:
            entries = [g for g in entries if name_contains.lower() in g["name"].lower()]
        if not entries:
            return "No GPS entries" + (f" matching '{name_contains}'." if name_contains else ".")
        return "\n".join(autopilot.gps_line(g) for g in entries)

    @tool
    async def add_gps(name: str, coords: str = "here", description: str = "") -> str:
        """Add a GPS entry for the player. coords: "here" (the player's position), "x, y, z", or a GPS string."""
        if coords.strip().lower() == "here":
            context = await bridge.call("player.context")
            point = context.get("position")
            if not point:
                return "Couldn't read the player's position."
            point = [point["x"], point["y"], point["z"]] if isinstance(point, dict) else point
        else:
            parsed = autopilot.parse_coords(coords)
            if not parsed:
                return "coords must be 'here', 'x, y, z' or a GPS:Name:x:y:z: string."
            point = parsed[1]
        g = await bridge.call("gps.add", {"name": name, "coords": point, "description": description})
        return "Added " + autopilot.gps_line(g).lstrip("- ")

    @tool
    async def remove_gps(name: str) -> str:
        """Delete one of the player's GPS entries by name."""
        g = await autopilot.find_gps(bridge, name)
        await bridge.call("gps.remove", {"hash": g["hash"]})
        return f"Removed GPS '{g['name']}'."

    @server.resource("se://pb-primer", name="pb-primer", description="How to write Space Engineers programmable block scripts")
    def pb_primer() -> str:
        return pb.primer()

    @tool
    async def align_to_sun(grid: str | None = None, max_rpm: float = 1.0, force: bool = False) -> str:
        """Rotate a ship (using gyro overrides) so its solar panels face the sun, then release the gyros.

        Takes a little time: a short test spin, then a slow turn (max_rpm). Refuses in natural gravity unless
        force=True, since turning a landed ship can tip it over. The player in the ship will feel it turn."""
        from .steering import align_to_sun as align

        target = await resolve.grid(bridge, grid)
        return await align(bridge, target["id"], max_rpm=max_rpm, force=force)

    @server.tool(structured_output=False)
    async def screenshot(max_width: int = 1280, hide_hud: bool = False) -> Image | str | list:
        """See what the player sees. Default: a capture of the game window (HUD, menus and all; works while
        it's covered). hide_hud=True renders the player's view in-engine instead: just the world."""
        if hide_hud:
            return await _render({"width": max_width}, "your view")
        from .screenshot import ScreenshotError, screenshot_jpeg

        try:
            data, _ = await asyncio.to_thread(screenshot_jpeg, max_width)
        except ScreenshotError as e:
            return f"Screenshot failed: {e}"
        return Image(data=data, format="jpeg")

    # --- Vision and sensing. ---

    async def _render(params: dict[str, Any], what: str) -> list | str:
        try:
            data, reply, path = await vision.capture(bridge, params, what)
        except BridgeError as e:
            return f"Couldn't render {what}: {e}"
        caption = f"{what}: {reply['width']}x{reply['height']}, FOV {reply['fovDegrees']} deg. Saved to {path}"
        return [caption, Image(data=data, format="jpeg")]

    @tool
    async def list_cameras(grid: str | None = None) -> str:
        """Camera blocks on a ship, with where they point and their raycast charge."""
        target = await resolve.grid(bridge, grid)
        cams = (await bridge.call("camera.list", {"gridId": target["id"]}))["cameras"]
        if not cams:
            return f"No cameras on {target.get('name') or 'this ship'}."
        return "\n".join(
            f"- {c['name']} ({c['id']}): {'working' if c['working'] else 'NOT WORKING'}"
            + (", player is looking through it" if c["active"] else "")
            + (f", raycast charge {c['scanRange']:.0f} m" if c["raycastEnabled"] else "")
            for c in cams)

    @tool
    async def camera_view(camera: str, grid: str | None = None, width: int = 1280, fov_degrees: float | None = None) -> list | str:
        """Look through a camera block: renders its view offscreen (the player's view isn't touched) and
        returns the image. fov_degrees overrides the camera's zoom."""
        target = await resolve.block(bridge, camera, grid)
        params: dict[str, Any] = {"blockId": target["id"], "width": width}
        if fov_degrees:
            params["fovDegrees"] = fov_degrees
        return await _render(params, target.get("name") or "a camera")

    @tool
    async def ship_photo(grid: str | None = None, side: str = "front-left", distance_m: float | None = None,
                         width: int = 1280) -> list | str:
        """A third-person picture of a whole ship from outside, like a drone shot.

        side: front, back, left, right, top, bottom, front-left, front-right, rear-left, rear-right
        (relative to the cockpit). distance_m defaults to fitting the whole ship in view."""
        target = await resolve.grid(bridge, grid)
        motion = await bridge.call("grid.motion", {"gridId": target["id"]})
        try:
            view = vision.ship_viewpoint(motion, side, distance_m)
        except ValueError as e:
            return str(e)
        return await _render({**view, "width": width}, f"{target.get('name') or 'ship'} from the {side}")

    @tool
    async def camera_scan(camera: str, distance_m: float = 2000, pitch_deg: float = 0, yaw_deg: float = 0,
                          grid: str | None = None) -> str:
        """Raycast from a camera block, like a PB scanner: reports the first thing hit (ship, asteroid,
        planet, character) with distance, size and speed. Uses the camera's charge, which builds up at
        ~2 km/s while raycasting is enabled; this enables it on first use. pitch/yaw aim within the camera's cone."""
        target = await resolve.block(bridge, camera, grid)
        result = await bridge.call("camera.raycast", {"blockId": target["id"], "distance": distance_m,
                                                       "pitch": pitch_deg, "yaw": yaw_deg})
        return vision.scan_text(result)

    @tool
    async def sensors(grid: str | None = None) -> str:
        """Sensor blocks on a ship: triggered or not, their field size, what they detect and what's in range now."""
        target = await resolve.grid(bridge, grid)
        return vision.sensors_text(await bridge.call("sensors.get", {"gridId": target["id"]}))

    # --- Combat: turrets, guns, ammo, camera tracking, damage. ---

    async def _point(text: str, grid_id: str) -> tuple[str, list[float]]:
        """Coordinates, a GPS entry, or a radio signal the ship hears (by name)."""
        try:
            return await autopilot.resolve_target(bridge, text)
        except ResolveError:
            s = radio.find_signal(await bridge.call("radio.signals", {"gridId": grid_id}), text)
            return radio.clean(s["name"]), s["position"]

    async def _weapons(grid: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        target = await resolve.grid(bridge, grid)
        return target, await bridge.call("weapons.get", {"gridId": target["id"]})

    @tool
    async def weapons(grid: str | None = None) -> str:
        """Turrets, fixed guns and custom turret controllers on a ship: what each is targeting, its target
        settings and range, the loaded magazine, and the ammo carried on board."""
        _, r = await _weapons(grid)
        return combat.weapons_text(r)

    @tool
    async def set_turrets(turrets: str = "all", enemies: bool | None = None, neutrals: bool | None = None,
                          characters: bool | None = None, large_grids: bool | None = None, small_grids: bool | None = None,
                          stations: bool | None = None, missiles: bool | None = None, meteors: bool | None = None,
                          range_m: float | None = None, targeting_group: str | None = None,
                          idle_rotation: bool | None = None, reset: bool = False, grid: str | None = None) -> str:
        """Change what turrets shoot at: target types (enemies, neutrals, characters, grids, stations, missiles,
        meteors), range, the block group they aim for ("Weapons", "Propulsion", "PowerSystems", or "" for default)
        and idle rotation. turrets: comma-separated names, or "all". Only the options you pass change.
        reset=True first returns turrets to their own targeting after aim_turrets."""
        _, r = await _weapons(grid)
        chosen = combat.pick(r["weapons"], turrets, ("turret", "turret controller"))
        flags = {k: v for k, v in (("enemies", enemies), ("neutrals", neutrals), ("characters", characters),
                                   ("largeGrids", large_grids), ("smallGrids", small_grids), ("stations", stations),
                                   ("missiles", missiles), ("meteors", meteors)) if v is not None}
        params: dict[str, Any] = {"blockIds": [w["id"] for w in chosen], "reset": reset}
        if flags:
            params["targets"] = flags
        if range_m is not None:
            params["range"] = range_m
        if targeting_group is not None:
            params["targetingGroup"] = targeting_group
        if idle_rotation is not None:
            params["idleRotation"] = idle_rotation
        changed = (await bridge.call("turret.set", params))["changed"]
        return f"Updated {len(changed)} turret(s): {', '.join(changed)}."

    @tool
    async def aim_turrets(turrets: str = "all", at: str | None = None, velocity: str | None = None,
                          azimuth_deg: float | None = None, elevation_deg: float | None = None, grid: str | None = None) -> str:
        """Point turrets at something. at: a radio signal name, a GPS name, a GPS string or "x, y, z"; add velocity "vx, vy, vz" (m/s)
        for a moving target so they lead it. Or aim by angle with azimuth_deg/elevation_deg. Doesn't fire.
        Turrets with AI on may switch back to their own targets; set_turrets(reset=True) hands control back."""
        ship, r = await _weapons(grid)
        chosen = combat.pick(r["weapons"], turrets, ("turret", "turret controller"))
        params: dict[str, Any] = {"blockIds": [w["id"] for w in chosen]}
        what = ""
        if at:
            name, point = await _point(at, ship["id"])
            params["position"] = point
            what = f"at {name}"
            if velocity:
                v = autopilot.parse_coords(velocity)
                if not v:
                    return "velocity must be 'vx, vy, vz' in m/s."
                params["velocity"] = v[1]
                what += f", leading it at {math.hypot(*v[1]):.0f} m/s"
        elif azimuth_deg is not None or elevation_deg is not None:
            if azimuth_deg is not None:
                params["azimuth"] = azimuth_deg
            if elevation_deg is not None:
                params["elevation"] = elevation_deg
            what = f"to azimuth {azimuth_deg if azimuth_deg is not None else '-'}, elevation {elevation_deg if elevation_deg is not None else '-'}"
        else:
            return "Give `at` (GPS or coordinates) or azimuth_deg/elevation_deg."
        aimed = (await bridge.call("turret.aim", params))["aimed"]
        return f"Aimed {', '.join(aimed)} {what}."

    @tool
    async def fire_weapons(weapons: str, mode: str = "once", grid: str | None = None) -> str:
        """Fire weapons: mode "once" (one shot), "on" (keep firing) or "off". weapons: comma-separated names,
        or "all". Needs allowWeapons in the plugin config (off by default). Check what's in the line of fire."""
        _, r = await _weapons(grid)
        chosen = combat.pick(r["weapons"], weapons, ("turret", "fixed"))
        result = await bridge.call("weapon.fire", {"blockIds": [w["id"] for w in chosen], "mode": mode})
        verb = {"once": "Fired one shot from", "on": "Firing", "off": "Stopped firing"}.get(mode, mode)
        return f"{verb} {', '.join(result['fired'])}."

    @tool
    async def load_magazine(weapon: str, magazine: str, grid: str | None = None) -> str:
        """Switch which magazine a weapon uses. Only some weapons take several: interior turrets (rifle
        magazines) and flare launchers (flares or fireworks). Single player / host only."""
        _, r = await _weapons(grid)
        w = combat.pick(r["weapons"], weapon, ("turret", "fixed"))
        if len(w) != 1:
            return f"'{weapon}' matches several weapons: {', '.join(x['name'] for x in w)}."
        w = w[0]
        wanted = magazine.strip().lower()
        options = {m: r["ammo"].get(m, {}).get("name", m) for m in w.get("magazines", [])}
        if len(options) < 2:
            return f"{w['name']} only takes {', '.join(options.values()) or 'one kind of ammo'}."
        match = [m for m, n in options.items() if wanted in (m.lower(), n.lower())] or \
                [m for m, n in options.items() if wanted in m.lower() or wanted in n.lower()]
        if len(match) != 1:
            return f"{w['name']} takes: {', '.join(options.values())}." + (" Which one?" if match else "")
        if not r["ammo"].get(match[0], {}).get("carried") and match[0] != w.get("loaded"):
            return f"No {options[match[0]]} on board, so {w['name']} would have nothing to shoot. Load some first."
        result = await bridge.call("weapon.magazine", {"blockId": w["id"], "magazine": match[0]})
        if not result["switched"]:
            return f"{w['name']} didn't switch to {options[match[0]]} (none on board, or this isn't the host)."
        return f"{w['name']} now uses {options[match[0]]} (was {options.get(result['before'], result['before'])})."

    @tool
    async def track_target(camera: str | None = None, at: str | None = None, seconds: float = 10,
                           interval_s: float = 1.0, turrets: str | None = None, mark_gps: bool = False,
                           grid: str | None = None) -> str:
        """Follow a ship or object with the ship's cameras, like a lidar script: re-scans where the target
        should be every interval_s (from its last position and velocity), using whichever camera can see it.
        Start from `camera` (scan straight ahead to acquire) or `at` (a radio signal name, GPS, or "x, y, z").
        turrets: also keep these turrets aimed at the track (no firing). mark_gps leaves a GPS at its last fix."""
        target = await resolve.grid(bridge, grid)
        if camera:
            cam = await resolve.block(bridge, camera, grid)
            first = await bridge.call("camera.raycast", {"blockId": cam["id"], "distance": 5000})
            hit = first.get("hit")
            if not hit:
                return "Couldn't acquire: " + vision.scan_text(first)
        elif at:
            _, point = await _point(at, target["id"])
            first = await bridge.call("camera.track", {"gridId": target["id"], "position": point})
            hit = first.get("hit")
            if not hit:
                return combat.no_fix_text(first)
        else:
            return "Give `camera` to acquire by scanning ahead, or `at` with the target's GPS or coordinates."

        aim_ids: list[str] = []
        if turrets:
            _, r = await _weapons(grid)
            aim_ids = [w["id"] for w in combat.pick(r["weapons"], turrets, ("turret",))]

        loop = asyncio.get_running_loop()
        start = loop.time()
        fix_time = start
        own = await bridge.call("grid.motion", {"gridId": target["id"]})
        first_distance = math.dist(own["center"], hit["position"])
        entity, pos, vel = hit["id"], hit["position"], hit["velocity"]
        fixes, misses, cameras_used = 1, 0, set()
        interval = max(0.25, interval_s)
        while loop.time() - start < min(seconds, 120):
            if aim_ids:
                await bridge.call("turret.aim", {"blockIds": aim_ids, "position": pos, "velocity": vel})
            await asyncio.sleep(interval)
            guess = combat.predict(pos, vel, loop.time() - fix_time)
            r = await bridge.call("camera.track", {"gridId": target["id"], "position": guess, "entityId": entity})
            if not r.get("camera"):
                misses += 1
            elif r["lost"]:
                misses += 1
                cameras_used.add(r["camera"])
            else:
                hit, pos, vel, fix_time = r["hit"], r["hit"]["position"], r["hit"]["velocity"], loop.time()
                fixes += 1
                misses = 0
                cameras_used.add(r["camera"])
            if misses >= 3:
                break
        own = await bridge.call("grid.motion", {"gridId": target["id"]})
        last_distance = math.dist(own["center"], pos)
        elapsed = max(0.1, fix_time - start)
        closing = (first_distance - last_distance) / elapsed if fixes > 1 else 0.0
        lines = [f"Tracked {vision.entity_line(hit)}",
                 f"{fixes} fix(es) over {loop.time() - start:.0f} s with {', '.join(sorted(cameras_used)) or 'the first camera'}."]
        if fixes > 1:
            lines.append(f"Range {first_distance:,.0f} m -> {last_distance:,.0f} m "
                         + (f"(closing at {closing:.1f} m/s)." if closing > 0.5 else
                            f"(opening at {-closing:.1f} m/s)." if closing < -0.5 else "(holding range)."))
        if misses >= 3:
            lines.append("LOST the target (3 scans missed).")
        lines.append(f"Last fix: {', '.join(f'{c:.0f}' for c in pos)}, velocity {', '.join(f'{c:.1f}' for c in vel)} m/s.")
        if aim_ids:
            lines.append(f"Turrets kept on it: {len(aim_ids)}.")
        if mark_gps:
            name = re.sub("[-]", "", hit.get("name") or "").strip() or hit["type"]
            await bridge.call("gps.add", {"name": f"Track: {name}", "coords": pos, "description": "Last camera fix"})
            lines.append(f"GPS 'Track: {name}' added.")
        return "\n".join(lines)

    @tool
    async def damage_report(grid: str | None = None) -> str:
        """Damaged, broken or unfinished blocks on a ship (armor included), worst first, with the components
        each needs. Use highlight_blocks(damaged=True) to show the functional ones to the player."""
        target = await resolve.grid(bridge, grid)
        return combat.damage_text(await bridge.call("damage.get", {"gridId": target["id"]}))

    @tool
    async def contacts(grid: str | None = None, include_friendly: bool = True, measure_s: float = 1.0) -> str:
        """Everything the ship knows is out there, from fair sources only: radio signals it hears (antennas,
        beacons), what its turrets are targeting, and what its sensors detect. Merged per ship/object, nearest
        first, with speeds measured over measure_s. Ships with antennas and beacons off only show up on
        turrets and sensors; use track_target or camera_scan to find those."""
        target = await resolve.grid(bridge, grid)
        heard = await radio.signals(bridge, target["id"], measure_s)
        _, w = await _weapons(grid)
        sensed = await bridge.call("sensors.get", {"gridId": target["id"]})
        return combat.contacts_text(target.get("name") or "Ship", heard, w, sensed, include_friendly)

    @tool
    async def ship_hud(grid: str | None = None, docked_too: bool = False) -> str:
        """Quick look at one ship or station, like a HUD: flight systems (thrusters/gyros on, dampeners, pilot,
        autopilot), power, fuel, gas, cargo, weapons, radio, docking and damage, with warnings for anything that
        would trip up flying (thrusters off, batteries on recharge, no fuel...). Only that ship, not ships docked to
        it; docked_too adds a line for each of those."""
        target = await resolve.grid(bridge, grid)
        h = await bridge.call("ship.hud", {"gridId": target["id"]})
        text = hud_mod.hud_text(h)
        if docked_too:
            for d in h["docked"]:
                other = await bridge.call("ship.hud", {"gridId": d["shipId"]})
                text += "\n\n" + hud_mod.hud_text(other)
        return text

    # --- Docking: connectors, landing gear, and flying a connector onto another. ---

    @tool
    async def docking_status(grid: str | None = None) -> str:
        """A ship's connectors (unconnected / connectable / connected and to what), landing gear and merge blocks."""
        target = await resolve.grid(bridge, grid)
        return docking.status_text(await bridge.call("dock.get", {"gridId": target["id"]}))

    @tool
    async def landing_gear(action: str = "lock", block: str | None = None, grid: str | None = None) -> str:
        """Lock or unlock landing gear / magnetic plates: action lock, unlock, autolock_on, autolock_off. One
        block, or every gear on the ship."""
        params: dict[str, Any] = {"action": action}
        if block:
            params["blockId"] = (await resolve.block(bridge, block, grid))["id"]
        else:
            params["gridId"] = (await resolve.grid(bridge, grid))["id"]
        r = await bridge.call("dock.gear", params)
        return "\n".join(f"- {g['name']}: {g['mode']}{', auto-lock' if g['autoLock'] else ''}" for g in r["changed"]) or "No landing gear."

    @tool
    async def undock(grid: str | None = None, connector: str | None = None, back_off_m: float = 0) -> str:
        """Disconnect a ship's connector(s) (default: every connected one on the ship). back_off_m > 0 then flies
        the ship that far straight out from the other connector, holding its orientation, and stops."""
        target = await resolve.grid(bridge, grid)
        status = await bridge.call("dock.get", {"gridId": target["id"]})
        connected = [c for c in status["connectors"] if c["status"] == "Connected"
                     and (not connector or connector.lower() in c["name"].lower())]
        if not connected:
            return f"{status['gridName']} isn't docked" + (f" by '{connector}'" if connector else "") + "."
        lines = []
        m = await bridge.call("grid.motion", {"gridId": target["id"], "thrusters": True})
        if m["thrusters"] and not any(t["working"] for t in m["thrusters"]):
            docking.PARKED_WITH_THRUSTERS_OFF.add(target["id"])
            lines.append("Note: its thrusters are off (parked)" + ("; turning them on to back off." if back_off_m > 0 else
                         ". Turn them on before flying it."))
        for c in connected:
            await bridge.call("dock.connector", {"blockId": c["id"], "action": "disconnect"})
            lines.append(f"Disconnected {c['name']} from {c['other']['name']} on {c['other']['gridName']}.")
        if back_off_m > 0 and len(connected) == 1:
            c = connected[0]
            other = await bridge.call("dock.get", {"gridId": c["other"]["gridId"]})
            far = next(x for x in other["connectors"] if x["id"] == c["other"]["id"])
            pilot = docking.ConnectorPilot(bridge, target["id"], c["id"], far, max_accel=await _safe_accel(target["id"]))
            try:
                await pilot.start()
                await pilot.hold(back_off_m + 1.8, tolerance=0.5, timeout_s=120)
                lines.append(f"Backed off {back_off_m:g} m, holding position.")
            except Exception as e:
                lines.append(f"Back-off stopped: {e}")
            finally:
                await pilot.stop()
        return "\n".join(lines)

    async def _safe_accel(grid_id: str) -> float:
        """Gentle acceleration: a third of the weakest direction's thrust over mass, at most 2 m/s^2."""
        m = await bridge.call("grid.motion", {"gridId": grid_id, "thrusters": True})
        per_dir: dict[tuple[int, ...], float] = {}
        for t in m["thrusters"]:
            key = tuple(round(x) for x in t["push"])
            per_dir[key] = per_dir.get(key, 0) + t["maxThrust"]
        weakest = min(per_dir.values()) if len(per_dir) >= 6 else 0
        return max(0.2, min(2.0, weakest / max(m["mass"], 1) / 3)) if weakest else 0.5

    @tool
    async def dock(target: str, grid: str | None = None, connector: str | None = None, target_connector: str | None = None,
                   approach_m: float = 20, max_speed: float = 2.0) -> str:
        """Dock a ship onto another ship's or station's connector by flying it there: lines our connector up
        with theirs (gyros), comes in along their connector's axis from approach_m out (thrusters), and connects
        when it's Connectable. Picks free connectors unless named. Turns thrusters on if the
        ship was parked with them off, and back off once docked. Keep the path clear; it doesn't steer around
        things on the way to the approach point."""
        ship = await resolve.grid(bridge, grid)
        other = await resolve.grid(bridge, target)
        mine = await bridge.call("dock.get", {"gridId": ship["id"]})
        theirs = await bridge.call("dock.get", {"gridId": other["id"]})
        try:
            c = docking.pick([x for x in mine["connectors"] if x["status"] != "Connected"], connector, True, mine["gridName"])
            # No size filter: a small-grid ship's connector can lock onto a large grid's; "Connectable" decides.
            t = docking.pick(theirs["connectors"], target_connector, True, theirs["gridName"])
        except ValueError as e:
            return str(e)
        log: list[str] = []
        pilot = docking.ConnectorPilot(bridge, ship["id"], c["id"], t, log=log.append, max_speed=max_speed,
                                       max_accel=await _safe_accel(ship["id"]))
        docked = False
        try:
            await pilot.start()
            await pilot.hold(approach_m, tolerance=0.5, timeout_s=300)
            await pilot.hold(1.8, tolerance=0.1, timeout_s=180, until=lambda x: x["status"] == "Connectable")
            r = await bridge.call("dock.connector", {"blockId": c["id"], "action": "connect"})
            docked = r["after"] == "Connected"
        except Exception as e:
            return f"Docking stopped: {e}. Overrides released, dampeners on.\n" + "\n".join(log[-4:])
        finally:
            await pilot.stop(docked)
        if not docked:
            return f"Lined up but the connector didn't lock ({r['after']})."
        return f"Docked {mine['gridName']}'s {c['name']} to {t['name']} on {theirs['gridName']}."

    # --- Ore detection. ---

    @tool
    async def ore_scan(grid: str | None = None, ore: str | None = None, mark_gps: bool = False) -> str:
        """What the ship's ore detectors have found: the ore markers on the HUD, per ore with the nearest one,
        and the detectors' range. ore filters (e.g. "iron", "ice") and lists the nearest markers. mark_gps adds a
        GPS at the nearest marker of each ore shown. Only reads what detectors see (no map scanning), so it needs a
        working detector and someone in control or 'broadcast using antennas' on."""
        target = await resolve.grid(bridge, grid)
        r = await bridge.call("ore.markers", {"gridId": target["id"]})
        text = ore_mod.scan_text(r, ore)
        if mark_gps:
            shown = [m for m in r["markers"] if not ore or ore.lower() in m["ore"].lower()]
            added = []
            for name, g in ore_mod.by_ore(shown).items():
                await bridge.call("gps.add", {"name": f"Ore: {name}", "coords": g["nearest"]["position"],
                                              "description": f"Nearest {name} seen by {target.get('name') or 'the ship'}"})
                added.append(name)
            if added:
                text += "\nGPS added: " + ", ".join(f"Ore: {a}" for a in added)
        return text

    # --- Radio: what the ship or suit hears, its own antennas and beacons, laser antenna links. ---

    @tool
    async def radio_signals(grid: str | None = None, from_suit: bool = False, measure_s: float = 0,
                            include_own: bool = False) -> str:
        """What the radio picks up: antenna, beacon and laser antenna signals (what the HUD shows), including
        ones relayed through your own antennas. Listens from a ship (default: the current one) or from_suit.
        measure_s > 0 samples twice to give each signal's speed."""
        grid_id = None if from_suit else (await resolve.grid(bridge, grid))["id"]
        return radio.signals_text(await radio.signals(bridge, grid_id, measure_s), include_own)

    @tool
    async def radio_equipment(grid: str | None = None) -> str:
        """A ship's antennas, beacons and laser antennas: on/off, broadcast radius, HUD text, and laser links."""
        target = await resolve.grid(bridge, grid)
        return radio.equipment_text(await bridge.call("radio.antennas", {"gridId": target["id"]}))

    @tool
    async def set_radio(block: str, radius_m: float | None = None, broadcasting: bool | None = None,
                        hud_text: str | None = None, show_ship_name: bool | None = None, enabled: bool | None = None,
                        grid: str | None = None) -> str:
        """Change an antenna or beacon: broadcast radius, broadcasting on/off (off = receive only, the ship goes
        quiet on radio), HUD text, showing the ship name, or power. A bigger radius uses more power."""
        b = await resolve.block(bridge, block, grid)
        params: dict[str, Any] = {"blockId": b["id"]}
        for key, value in (("radius", radius_m), ("broadcasting", broadcasting), ("hudText", hud_text),
                           ("showShipName", show_ship_name), ("enabled", enabled)):
            if value is not None:
                params[key] = value
        r = await bridge.call("radio.set", params)
        mine = [e for e in r["equipment"] if e["id"] == b["id"]]
        return radio.equipment_text({**r, "equipment": mine}).split("\n", 1)[-1]

    @tool
    async def laser_link(laser: str, target: str, target_laser: str | None = None, permanent: bool | None = None,
                         turn_ship: bool = False, grid: str | None = None) -> str:
        """Point a laser antenna at another laser antenna and connect. target: a GPS name, "x, y, z", a radio
        signal or ship name (uses its laser antenna if it has one), or "idle" to drop the link. Both ends must
        aim at each other and have line of sight; permanent keeps the link after a reconnect. If the ship's own
        hull blocks the view, turn_ship=True first rotates the ship (gyros) so the laser's base faces the target.
        target_laser picks one laser antenna by name when the target ship has several."""
        ship = await resolve.grid(bridge, grid)
        b = await resolve.block(bridge, laser, grid)
        if target.strip().lower() == "idle":
            r = await bridge.call("laser.idle", {"blockId": b["id"]})
            return f"{r['block']}: {r['status']}."
        here = next(e for e in (await bridge.call("radio.antennas", {"gridId": ship["id"]}))["equipment"] if e["id"] == b["id"])
        far = None
        # One of our own ships or stations with a laser antenna: aim both ends at each other.
        try:
            other = await resolve.grid(bridge, target)
            lasers = [e for e in (await bridge.call("radio.antennas", {"gridId": other["id"]}))["equipment"]
                      if e["kind"] == "laser" and e["id"] != b["id"] and e["working"]]
            if target_laser:
                wanted = target_laser.strip().lower()
                lasers = [e for e in lasers if e["name"].lower() == wanted] or [e for e in lasers if wanted in e["name"].lower()]
                if not lasers:
                    return f"No working laser antenna named '{target_laser}' on {other.get('name') or target}."
            far = lasers[0] if lasers else None
        except (ResolveError, BridgeError):
            pass
        if far:
            name, point = f"{far['name']} on {far['gridName']}", far["position"]
            back: dict[str, Any] = {"blockId": far["id"], "target": here["position"]}
            if permanent is not None:
                back["permanent"] = permanent
            await bridge.call("laser.connect", back)
        else:
            name, point = await _point(target, ship["id"])
        turned = ""
        if turn_ship:
            from .steering import turn_to

            async def laser_facing() -> tuple[list[float], list[float]]:
                eq = await bridge.call("radio.antennas", {"gridId": ship["id"]})
                me = next(e for e in eq["equipment"] if e["id"] == b["id"])
                return me["up"], [t - s for t, s in zip(point, me["position"])]

            turned = await turn_to(bridge, ship["id"], laser_facing) + " "
        params: dict[str, Any] = {"blockId": b["id"], "target": point}
        if permanent is not None:
            params["permanent"] = permanent
        r = await bridge.call("laser.connect", params)
        for _ in range(10):
            await asyncio.sleep(1)
            st = next(e for e in (await bridge.call("radio.antennas", {"gridId": ship["id"]}))["equipment"] if e["id"] == b["id"])
            if st["status"] == "Connected":
                break
        eq = await bridge.call("radio.antennas", {"gridId": ship["id"]})
        mine = [e for e in eq["equipment"] if e["id"] == b["id"]]
        return f"{turned}Aimed {r['block']} at {name}. " + radio.equipment_text({**eq, "equipment": mine}).split("\n", 1)[-1]

    return server
