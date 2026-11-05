"""MCP tools. Naming, formatting and convenience live here so the game side can stay thin."""

from __future__ import annotations

import asyncio
import functools
import math
import re
import time
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import Image, MCPServer

from . import autopilot
from . import build
from . import combat
from . import companion
from . import docking
from . import forecast as forecast_mod
from . import format as fmt
from . import guide as guide_mod
from . import hud as hud_mod
from . import items as items_mod
from . import naming
from . import notes as notes_mod
from . import ore as ore_mod
from . import organizer
from . import overlay as overlay_mod
from . import production as production_mod
from . import radio
from . import surface as surface_mod
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
    "Before writing a programmable block script, read the se://pb-primer resource. "
    "Ships can have notes (an AGENTS.md per ship: custom scripts like cruise control, odd layouts, habits). Read "
    "them with ship_notes before operating a ship, and when you learn something about a ship that the next agent "
    "would otherwise have to rediscover, add it with write_ship_notes. "
    "To be told when something happens later (fuel low, a door opens, docking), add a watch; the in-game AI is "
    "called back when it fires. The in-game AI's personality is in ai_soul / write_ai_soul."
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
    reminders = notes_mod.Reminders()

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
        context = await bridge.call("player.context")
        lines.append(fmt.context(context))
        for g in (context.get("controlled"), context.get("nearestGrid")):
            line = reminders.line((g or {}).get("gridName"))
            if line and line not in lines:
                lines.append(line)
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
        return fmt.block_detail(detail, all_properties) + await _where(detail)

    async def _where(detail: dict[str, Any]) -> str:
        """Where a block is and how it's turned: world position and axes, and its place on its ship."""
        if "position" not in detail:
            return ""
        lines = [f"Position: {fmt.vec(detail['position'])} (world)"]
        try:
            frame = docking.ship_frame(await bridge.call("grid.motion", {"gridId": detail["block"]["gridId"]}))
            seen = f"as seen from {frame['ref']}" if frame["ref"] else "by the grid's axes"
            lines.append(f"On {detail['gridName']}: {docking.placement(detail['position'], detail['forward'], frame)} ({seen})")
        except (BridgeError, KeyError):
            pass
        axes = ", ".join(f"{k} {fmt.vec(detail[k], 3)}" for k in ("forward", "up", "right") if k in detail)
        lines.append(f"Axes (world): {axes}")
        if detail.get("cell"):
            lines.append(f"Grid cell: {tuple(detail['cell'])}, {detail.get('cellSize', 0):g} m cells")
        return "\n" + "\n".join(lines)

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

    # --- HUD overlay: text, boxes, bars and markers drawn on the player's screen. ---

    @tool
    async def hud_draw(elements: list[dict[str, Any]]) -> str:
        """Draw on the player's HUD (only they see it; hidden with the HUD and while a menu or terminal is open).
        Each element: {"id", "kind": "text"|"rect"|"bar"|"line"|"arrow"|"circle"|"icon"|"marker", ...}. Setting an
        existing id changes only the fields given, so you can move, recolour or retext it.
        - Position: x, y = fractions of the screen (0,0 top-left; 1,1 bottom-right); w, h for rect and bar.
        - line/arrow: from (x, y) to (x2, y2), or to a block/grid/position (arrow points at it, from the screen
          edge if it's off screen). thickness in px.
        - circle: centre x, y (or a block/grid/position), radius w (fraction of screen height).
        - icon: icon = battery, energy, hydrogen, oxygen, health, health-low, jetpack, dampeners, handbrake, light,
          mass, radiation, suffocating, temperature, food, gravity, broadcast, power, speed, inventory, chat, lock,
          camera, enemy, friendly, neutral, gps, alert, self, square (or a "Textures\\..." path); size w; tinted by
          color; at x, y or on a block/grid/position.
        - text: may hold {path} placeholders read from the bound data ("{hydrogen.fill}"), {pct} for a bar's
          percentage, {dist} for a marker's distance. scale (0.6 small .. 1.2 big), align left|center|right,
          font "White"|"Red"|"Green"|"Blue"|"Monospace".
        - color "#RRGGBB[AA]"; background for text/bar; rect: fill true|false, thickness (px).
        - bar: value + max, or valuePath/maxPath from the bound data; lowBelow (percent) + lowColor to warn.
        - marker: corner brackets round a block or grid (name/id) as it appears on screen, or a small box on a
          position [x, y, z]; style "point" for the small box on anything. Off screen, an arrow at the screen edge
          points to it (offscreen: false to hide). For boxes round every meteor/enemy/ship use hud_track.
        - bind: {"method": "ship.hud", "params": {"grid": "Red Ship"}, "every": 1} re-reads a read-only method
          every few seconds so the element stays live (ship.hud, grid.get, character.status, inventory.get, ...).
        - ttl: seconds until it disappears on its own.
        For a ready-made fuel/power panel use hud_ship_panel."""
        prepared = []
        for e in elements:
            e = dict(e)
            if isinstance(e.get("bind"), dict):
                bind = dict(e["bind"])
                params = dict(bind.get("params") or {})
                if "grid" in params:
                    params["gridId"] = (await resolve.grid(bridge, params.pop("grid")))["id"]
                if "block" in params:
                    params["blockId"] = (await resolve.block(bridge, params.pop("block")))["id"]
                bind["params"] = params
                e["bind"] = bind
            if e.get("block"):
                e["blockId"] = (await resolve.block(bridge, str(e.pop("block")), e.pop("grid", None)))["id"]
            elif e.get("grid"):
                e["gridId"] = (await resolve.grid(bridge, str(e.pop("grid"))))["id"]
            prepared.append(e)
        r = await bridge.call("overlay.set", {"elements": prepared})
        return f"Drew {', '.join(r['set'])} ({r['total']} element(s) on the HUD)."

    @tool
    async def hud_ship_panel(grid: str | None = None, show: list[str] | None = None, x: float = 0.015, y: float = 0.30,
                             id: str | None = None) -> str:
        """Put a live status panel for a ship on the player's HUD: title plus gauges that update every second.
        show: any of hydrogen, oxygen, battery, uranium, cargo, speed (default hydrogen, battery, uranium).
        Bars turn red when low. x, y: the panel's top-left as screen fractions (default: left edge, above middle).
        Remove with hud_remove(id) (the id is returned) or hud_clear."""
        target = await resolve.grid(bridge, grid)
        prefix = id or f"ship-{target['id']}"
        try:
            elements = overlay_mod.ship_panel(prefix, target["id"], target.get("name") or "Ship", show or overlay_mod.DEFAULT_SHOW, x, y)
        except ValueError as e:
            return str(e)
        await bridge.call("overlay.set", {"elements": elements})
        return f"Panel '{prefix}' for {target.get('name') or 'the ship'} is on the HUD ({len(elements)} elements, live)."

    @tool
    async def hud_track(what: list[str], id: str | None = None, range: float = 3000, incoming: bool = False,
                        color: str | None = None, label: str = "{name} {dist}", style: str = "box",
                        offscreen: bool = True, max_targets: int = 30) -> str:
        """Keep a box round every thing of a kind near the player, live (re-scanned twice a second): new ones get
        boxed, gone ones drop off. what: any of meteors, missiles, enemies, neutrals, friends, unowned, grids (every
        ship), players. "Unfriendlies" = enemies (add neutrals if they mean anyone not on our side).
        incoming=True keeps only things closing in on the player. label placeholders: {name}, {dist}, {speed},
        {eta} (seconds until it reaches us at its closing speed). Off-screen ones get an arrow at the screen edge.
        Range is capped by the plugin's maxRangeMeters. Remove with hud_remove(id) or hud_clear."""
        kinds = [w.strip().lower() for w in what if w.strip()]
        eid = id or "track-" + "-".join(kinds)
        default = "#FF5050" if {"enemies", "missiles"} & set(kinds) else "#FFB74D" if "meteors" in kinds else "#7CFC00"
        r = await bridge.call("overlay.set", {"elements": [{
            "id": eid, "kind": "track", "track": kinds, "range": range, "incoming": incoming, "color": color or default,
            "text": label, "style": style, "offscreen": offscreen, "maxTargets": max_targets, "scale": 0.6}]})
        listed = (await bridge.call("overlay.list", {}))["elements"]
        count = next((e.get("tracking") for e in listed if e["id"] == eid), None)
        now = "" if count is None else f" Right now: {count} in range (first scan may still be running)." if count == 0 else f" Right now: {count}."
        return f"Tracking {', '.join(kinds)}{' (incoming only)' if incoming else ''} within {range:g} m as '{r['set'][0]}'.{now}"

    @tool
    async def hud_remove(ids: list[str]) -> str:
        """Remove HUD overlay elements by id. A panel's id removes all its parts."""
        listed = (await bridge.call("overlay.list", {}))["elements"]
        chosen = [e["id"] for e in listed if any(e["id"] == i or e["id"].startswith(i + "-") for i in ids)]
        r = await bridge.call("overlay.remove", {"ids": chosen})
        return f"Removed {r['removed']} element(s); {r['total']} left."

    @tool
    async def hud_clear() -> str:
        """Remove everything drawn with hud_draw / hud_ship_panel."""
        r = await bridge.call("overlay.clear", {})
        return f"Cleared {r['removed']} HUD element(s)."

    @tool
    async def hud_list() -> str:
        """What's currently drawn on the player's HUD overlay."""
        listed = (await bridge.call("overlay.list", {}))["elements"]
        if not listed:
            return "Nothing drawn on the HUD."
        lines = []
        for e in listed:
            line = f"{e['id']}: {e['kind']} at ({e['x']:.3f}, {e['y']:.3f})"
            if e.get("text"):
                line += f" \"{e['text']}\""
            if e.get("track"):
                line = f"{e['id']}: tracking {', '.join(e['track'])} ({e.get('tracking', 0)} boxed)"
            if e.get("legs"):
                line = f"{e['id']}: route, heading for point {e['leg']} of {e['legs']}"
            if e.get("bind"):
                line += f", live from {e['bind']}"
            if e.get("bindError"):
                line += f" (error: {e['bindError']})"
            if e.get("expiresIn") is not None:
                line += f", gone in {e['expiresIn']:g} s"
            lines.append(line)
        return "\n".join(lines)

    # --- Guiding the player. ---

    async def _guide_target(to: str, grid: str | None) -> dict[str, Any]:
        """Where `to` is: coordinates or a GPS, else a ship by name, else a block (on `grid` or the current one)."""
        if grid is None:
            try:
                name, point = await autopilot.resolve_target(bridge, to)
                return {"name": name, "point": point}
            except ResolveError:
                pass
            try:
                target = await resolve.grid(bridge, to)
                motion = await bridge.call("grid.motion", {"gridId": target["id"]})
                return {"name": target.get("name") or to, "point": motion["center"], "gridId": target["id"], "ship": True}
            except ResolveError as ship_error:
                try:
                    found = await resolve.block(bridge, to)
                except ResolveError as block_error:
                    raise ResolveError(f"Couldn't find '{to}' as a GPS, ship or block. {ship_error} {block_error}") from None
        else:
            found = await resolve.block(bridge, to, grid)
        info = await bridge.call("block.get", {"blockId": found["id"]})
        return {"name": f"{info['block']['name']} on {info['gridName']}", "point": info["position"],
                "gridId": info["block"]["gridId"], "blockId": found["id"]}

    async def _game_path(grid_id: str, start: list[float], end: list[float], radius: float) -> list[list[float]] | None:
        """A flying route around a grid from the game's AI-block pathfinder, thinned to points 3 m+ apart, or None.
        Its map of the grid builds in the background on first use, so it's asked again for a few seconds."""
        for _ in range(12):
            r = await bridge.call("path.find", {"gridId": grid_id, "from": start, "to": end, "radius": radius})
            if not r.get("retry"):
                break
            await asyncio.sleep(0.5)
        if not r.get("found") or not r.get("points"):
            return None
        return guide_mod.thin([guide_mod.as_vec(p) for p in r["points"]], start, 3.0)

    @tool
    async def guide_to(to: str, grid: str | None = None, arrive_m: float = 3, avoid: bool = True,
                       note: str | None = None) -> str:
        """Show the player the way somewhere on their HUD: a marker on the next waypoint with the distance left,
        and an arrow at the screen edge when it's behind them. The player flies or walks there themselves.
        to: a GPS name, "x, y, z", a ship's name, or a block's name (on `grid`, default the current ship).
        avoid: route around ships and asteroids in the way (line-of-sight checks, with detour points beside each
        obstacle). The marker moves on as each waypoint is reached and clears within arrive_m of the end; an
        "arrived" event then goes to the in-game AI. One guide at a time; stop_guide removes it.
        Works while piloting too: the route looks through the player's ship and keeps its radius clear, so set
        arrive_m to what's close enough for it (e.g. 200 to bring a big ship near a station).
        note: what to do when they get there ("open the hangar door", "start the welders"). The arrived event
        wakes the in-game AI with this note, so "go there and I'll do X" needs no polling."""
        target = await _guide_target(to, grid)
        status = await bridge.call("character.status", {})
        start = guide_mod.as_vec(status["position"])
        end = guide_mod.as_vec(target["point"])
        target_grid = target.get("gridId")

        # Piloting: look through our own ship, and make room for its size.
        own = (await bridge.call("player.context", {})).get("controlled") or {}
        own_grid = own.get("gridId")
        radius = 0.0
        if own_grid:
            motion = await bridge.call("grid.motion", {"gridId": own_grid})
            if motion.get("isStatic"):
                own_grid = None  # a seat on a station: they'll get up and go on foot
            else:
                radius = float(motion.get("radius") or 0)
            if own_grid and own_grid == target_grid:
                return f"You're aboard {target['name'].split(' on ')[-1]} already; get out of the seat to be guided round it."
        los: dict[str, Any] = {"ignoreId": own_grid} if own_grid else {}

        async def ray(a: list[float], b: list[float], to_end: bool) -> dict[str, Any] | None:
            r = await bridge.call("los.check", {"from": a, "to": b, **los})
            if r["clear"]:
                return None
            hit = r["hit"]
            # Reaching the target's own ship counts as getting there: a ship anywhere on its hull, a block near it.
            if target_grid and hit.get("entityId") == target_grid and to_end:
                near = guide_mod.norm(guide_mod.sub(guide_mod.as_vec(hit["position"]), end))
                if target.get("ship") or near <= 5 + radius:
                    return None
            return hit

        async def blocked(a: list[float], b: list[float]) -> dict[str, Any] | None:
            hit = await ray(a, b, b == end)
            if hit is not None or radius < 3:
                return hit
            # A ship sweeps a tube, not a line: check its edges too.
            for a2, b2 in guide_mod.beside(a, b, radius):
                hit = await ray(a2, b2, b == end)
                if hit is not None:
                    return hit
            return None

        route: list[list[float]] | None = None
        obstacle: dict[str, Any] | None = None
        checks, by_game = 0, False
        if avoid:
            # A ship or station in the way: the game's own (AI block) pathfinder knows its shape.
            obstacle = await blocked(start, end)
            checks = 1
            if obstacle is None:
                route = [end]
            elif obstacle.get("entityId") and not obstacle.get("voxel"):
                points = await _game_path(obstacle["entityId"], start, end, max(1.0, radius))
                if points:
                    last = points[-1]
                    if await blocked(last, end) is None:
                        route, by_game = points + [end], True
                    else:
                        rest, _, used = await guide_mod.plan_route(last, end, blocked, clearance=radius, max_checks=40)
                        checks += used
                        if rest is not None:
                            route, by_game = points + rest, True
        if route is None:
            route, first, used = await guide_mod.plan_route(start, end, blocked, depth=2 if avoid else 0,
                                                            clearance=radius, max_checks=40 if radius >= 3 else 120)
            obstacle, checks = obstacle or first, checks + used
        warning = ""
        if route is None:
            route = [end]
            warning = (f" No clear way found around {guide_mod.obstacle_name(obstacle)} ({checks} checks), so the marker "
                    "just points at it" + (": it's probably inside, find a door or airlock." if target_grid else "."))
        if target.get("ship"):
            # Stop at the hull facing the player, not the ship's centre.
            before = route[-2] if len(route) > 1 else start
            r = await bridge.call("los.check", {"from": before, "to": end, **los})
            if not r["clear"] and r["hit"].get("entityId") == target_grid:
                hull = guide_mod.as_vec(r["hit"]["position"])
                route[-1] = guide_mod.add(hull, guide_mod.unit(guide_mod.sub(before, hull)), arrive_m)

        if len(route) == 1 and guide_mod.norm(guide_mod.sub(route[0], start)) <= arrive_m:
            return f"You're already within {arrive_m:g} m of {target['name']}."
        element: dict[str, Any] = {
            "id": "guide", "kind": "route", "route": route, "name": target["name"], "arriveRadius": arrive_m,
            "text": "{name} {dist}" + (" ({step})" if len(route) > 1 else ""), "color": "#4FC3F7", "thickness": 2}
        if target.get("blockId"):
            element["blockId"] = target["blockId"]
        element["note"] = note
        await bridge.call("overlay.set", {"elements": [element]})

        length = guide_mod.path_length(start, route)
        if len(route) == 1:
            how = "in a straight line"
        else:
            how = (f"via {len(route) - 1} waypoint(s) around {guide_mod.obstacle_name(obstacle)}"
                   + (" (the game's own pathfinder)" if by_game else ""))
        ship = f" Routed for {own.get('gridName') or 'your ship'} ({radius:.0f} m radius), looking through it." if radius >= 3 else ""
        return (f"Guiding you to {target['name']}: {guide_mod.distance_text(length)} {how}. The HUD marker moves on "
                f"as you reach each point and clears within {arrive_m:g} m of the end." + ship + warning
                + (" On arrival the in-game AI is woken with your note." if note else ""))

    @tool
    async def stop_guide() -> str:
        """Remove the HUD guide that guide_to put up."""
        r = await bridge.call("overlay.remove", {"ids": ["guide"]})
        return "Guide removed." if r["removed"] else "No guide was showing."

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

    # --- Window mirror: desktop windows shown live on screens, on this PC only. ---

    def _mirror_line(m: dict[str, Any]) -> str:
        line = (f"{m['block']} #{m['surface']}: {m['window']!r} ({m['process']}) at {m['fps']} fps, "
                f"{m['resolution'][0]}x{m['resolution'][1]}, {m['uploaded']} frames shown")
        return line + (f", capture error: {m['error']}" if m.get("error") else "")

    @tool
    async def list_windows(match: str | None = None) -> str:
        """Desktop windows that mirror_window can show on a screen, front-most first. match: part of the title or
        the process name."""
        params = {"filter": match} if match else {}
        windows = (await bridge.call("mirror.windows", params))["windows"]
        if not windows:
            return "No matching windows." if match else "No windows."
        return "\n".join(f"- {w['title']!r} ({w['process']}, {w['width']}x{w['height']}, handle {w['handle']})" for w in windows)

    @tool
    async def mirror_window(block: str, window: str, surface: str | int | None = None, fps: int = 15,
                            resolution: int = 1024, grid: str | None = None) -> str:
        """Show a desktop window live on an LCD or one of a cockpit's screens (sets it to the Window Mirror script).

        window: part of its title or the process name (see list_windows), a handle like 0x1A2B, or "testcard".
        fps: 1-30. resolution: the long side of the screen's picture in pixels (64-2048). Only this PC sees the
        picture (other players see the idle card); no sound yet. Stop with stop_mirror, which puts the screen back."""
        target = await resolve.block(bridge, block, grid)
        params: dict[str, Any] = {"blockId": target["id"], "surface": await _surface_index(target["id"], surface),
                                  "fps": fps, "resolution": resolution}
        params["handle" if window.lower().startswith("0x") else "window"] = window
        m = await bridge.call("mirror.start", params)
        return "Mirroring: " + _mirror_line(m)

    @tool
    async def stop_mirror(block: str | None = None, surface: str | int | None = None, grid: str | None = None) -> str:
        """Stop mirroring a window on a screen and put the screen back as it was. No block: stop every mirror.
        Without block or surface it also lists what was running."""
        if block is None:
            running = (await bridge.call("mirror.list", {}))["mirrors"]
            r = await bridge.call("mirror.stop", {})
            if not r["stopped"]:
                return "No windows were being mirrored."
            return f"Stopped {r['stopped']} mirror(s):\n" + "\n".join("- " + _mirror_line(m) for m in running)
        target = await resolve.block(bridge, block, grid)
        await bridge.call("mirror.stop", {"blockId": target["id"], "surface": await _surface_index(target["id"], surface)})
        return f"Stopped the mirror on {target.get('name') or block}; the screen is back as it was."

    @tool
    async def list_mirrors() -> str:
        """Windows currently mirrored onto screens, with frame counts and the game's frame rate."""
        r = await bridge.call("mirror.list", {})
        game = f"Game: {r['gameFps']} fps, {r['frameMs']} ms/frame." if "gameFps" in r else ""
        if not r["mirrors"]:
            return " ".join(filter(None, ["No windows are being mirrored.", game]))
        return "\n".join(["- " + _mirror_line(m) for m in r["mirrors"]] + ([game] if game else []))

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
    async def rename_blocks(
        grid: str | None = None,
        name_contains: str | None = None,
        group: str | None = None,
        type: str | None = None,
        all_blocks: bool = False,
        prefix: str | None = None,
        suffix: str | None = None,
        find: str | None = None,
        replace: str = "",
        strip_prefix: str | None = None,
        template: str | None = None,
        dry_run: bool = False,
    ) -> str:
        """Rename many blocks in one go (use this instead of calling rename_block over and over). Choose blocks by
        name_contains, group and/or type, or all_blocks=True for every terminal block on the ship (subgrids included).
        Then say how names change; steps apply in this order:
        strip_prefix (remove a leading tag, e.g. "[Old]"), find/replace (case-insensitive text), template (a whole new
        name with {name}, {type} e.g. "Ion Thruster", {n} = 1, 2, 3... per type, {i} = 1, 2, 3... overall), then
        prefix and suffix (skipped where already present, so it's safe to repeat).
        Examples: prefix="[Constructor]"; strip_prefix="[Constructor]"; find="Thruster", replace="Thr";
        template="{type} {n}" to renumber. dry_run=True shows what would change without renaming. Renames are by id,
        so similar names can't be mixed up. Undo with the opposite rename (e.g. strip_prefix after a prefix)."""
        if not (name_contains or group or type or all_blocks):
            return "Choose blocks with name_contains, group or type, or pass all_blocks=True for the whole ship."
        if not (prefix or suffix or find or strip_prefix or template):
            return "Say how to change the names: prefix, suffix, find/replace, strip_prefix or template."
        target = await resolve.grid(bridge, grid)
        params: dict[str, Any] = {"gridId": target["id"], "limit": 2000}
        for key, value in (("nameContains", name_contains), ("group", group), ("type", type)):
            if value:
                params[key] = value
        blocks = (await bridge.call("blocks.list", params))["blocks"]
        changes = naming.plan(blocks, prefix=prefix, suffix=suffix, find=find, replace=replace,
                              strip_prefix=strip_prefix, template=template)
        if not changes:
            return f"Nothing to rename: {len(blocks)} block(s) matched and already have those names."
        if dry_run:
            return f"Would rename {len(changes)} of {len(blocks)} block(s):\n{naming.summary(changes)}"
        r = await bridge.call("block.rename_many", {"renames": [{"blockId": b["id"], "name": new} for b, new in changes]})
        done = [(next(b for b, _ in changes if b["id"] == x["id"]), x["after"]) for x in r["renamed"]]
        text = f"Renamed {len(done)} of {len(blocks)} block(s):\n{naming.summary(done)}"
        if r["failed"]:
            text += "\nFailed: " + "; ".join(f"{x['id']}: {x['error']}" for x in r["failed"][:10])
        return text

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
        if stop := reminders.gate(rc["gridName"]):
            return stop
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

    @tool
    async def rotate_ship(yaw_deg: float = 0, pitch_deg: float = 0, roll_deg: float = 0, grid: str | None = None,
                          max_rpm: float = 2.0, force: bool = False) -> str:
        """Turn a ship in place by angles, as its pilot sees it: yaw_deg > 0 turns right, pitch_deg > 0 raises the
        nose, roll_deg > 0 rolls right (180 yaw = turn around). Uses gyro overrides with a short test spin first,
        holds the other axes, then lets go. Doesn't move the ship, but check clearance first near structures (it
        swings its whole length). Refuses in natural gravity unless force=True. To face something, use point_ship."""
        from . import steering

        ship = await resolve.grid(bridge, grid)
        if stop := reminders.gate(await _ship_name(grid)):
            return stop
        return await steering.rotate_ship(bridge, ship["id"], yaw_deg, pitch_deg, roll_deg, max_rpm=max_rpm, force=force)

    @tool
    async def point_ship(target: str, face: str = "forward", grid: str | None = None, max_rpm: float = 2.0,
                         force: bool = False) -> str:
        """Turn a ship in place so one of its sides, or its fixed guns, points at something: e.g. aim the forward
        cannons at an enemy ship. target: a ship or station name, a radio signal, a GPS name or string, or
        "x, y, z". face: forward (default) / back / up / down / left / right as the pilot sees it, "guns" (all fixed
        weapons, averaged), or a fixed weapon's name. Roll is left as is. Doesn't fire (fire_weapons does).
        Check clearance first near structures. Refuses in natural gravity unless force=True."""
        from . import steering

        ship = await resolve.grid(bridge, grid)
        if stop := reminders.gate(await _ship_name(grid)):
            return stop
        try:
            name, point = await _point(target, ship["id"])
        except ResolveError:
            try:
                other = await resolve.grid(bridge, target)
            except ResolveError:
                return f"Couldn't find '{target}' as a ship, radio signal, GPS or coordinates."
            name, point = other.get("name") or target, (await bridge.call("grid.motion", {"gridId": other["id"]}))["center"]

        frame = steering.frame_of(await bridge.call("grid.motion", {"gridId": ship["id"]}))
        sides = {"forward": [0, 0, 1], "back": [0, 0, -1], "up": [0, 1, 0], "down": [0, -1, 0],
                 "right": [1, 0, 0], "left": [-1, 0, 0]}
        side = face.strip().lower()
        origin = [0.0, 0.0, 0.0]
        if side in sides:
            local, what = sides[side], f"its {side}"
        else:
            _, r = await _weapons(grid)
            spec = "all" if side in ("guns", "weapons", "fixed weapons", "cannons") else face
            try:
                guns = combat.pick(r["weapons"], spec, ("fixed",))
            except ResolveError as e:
                return f"{e} (face takes forward/back/up/down/left/right, 'guns', or a fixed weapon's name)"
            local, origin = _gun_frame(guns, frame)
            what = _names(guns)
        result = await steering.point_ship(bridge, ship["id"], point, local, origin, max_rpm=max_rpm, force=force)
        return f"Pointing {what} at {name}: {result}"

    async def _acquire(ship_id: str, turrets: list[dict[str, Any]], aim: list[float], moving: bool,
                       timeout_s: float = 12) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Wait for turrets to swing onto a point: (name, seconds) for those on target, (name, degrees off) for those
        that couldn't get there (out of their arc, blocked, or still slewing when time ran out)."""
        from . import steering

        tolerance = 4.0 if moving else 1.5  # leading a moving target points the barrel ahead of it
        start = time.monotonic()
        on: dict[str, float] = {}
        best: dict[str, tuple[float, float]] = {}  # name -> (degrees off, when it last improved)
        while True:
            now = time.monotonic() - start
            r = await bridge.call("weapons.get", {"gridId": ship_id})
            state = {w["id"]: w for w in r["weapons"]}
            for t in turrets:
                if t["name"] in on:
                    continue
                w = state.get(t["id"], t)
                if not w.get("aimDirection"):
                    continue
                off = math.degrees(steering.angle_between(w["aimDirection"], [aim[i] - w["position"][i] for i in range(3)]))
                if off < tolerance:
                    on[t["name"]] = now
                elif off < best.get(t["name"], (999.0, 0.0))[0] - 0.2:
                    best[t["name"]] = (off, now)
            waiting = [t for t in turrets if t["name"] not in on]
            # Done when all are on target, or the rest have stopped getting closer for a couple of seconds.
            stalled = all(now - best.get(t["name"], (0, now))[1] > 2.5 for t in waiting)
            if not waiting or (stalled and now > 3) or now > timeout_s:
                return sorted(on.items(), key=lambda x: x[1]), [(t["name"], best.get(t["name"], (180.0, 0))[0]) for t in waiting]
            await asyncio.sleep(0.4)

    def _acquisition_lines(on: list[tuple[str, float]], off: list[tuple[str, float]]) -> list[str]:
        lines = []
        if on:
            slowest = max(s for _, s in on)
            lines.append(f"On target: {_names([{'name': n} for n, _ in on], 6)} (within {slowest:.1f} s).")
        for name, degrees in off:
            lines.append(f"! {name} couldn't get on target ({degrees:.0f} deg off: out of its arc or blocked).")
        return lines

    def _block_label(b: dict[str, Any]) -> str | None:
        """A hit block's name; armour has none, so "MyObjectBuilder_CubeBlock/HalfArmorBlock" -> "Half Armor Block"."""
        name = b.get("name") or b.get("type")
        if not name or name.startswith("MyObjectBuilder_") or "/" in name:
            raw = (name or "").split("/")[-1] or (b.get("type") or "").split("/")[-1]
            name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw.removeprefix("MyObjectBuilder_")) or None
        return name

    def _names(items: list[dict[str, Any]], most: int = 4) -> str:
        return ", ".join(x["name"] for x in items[:most]) + (f" (+{len(items) - most})" if len(items) > most else "")

    def _gun_frame(guns: list[dict[str, Any]], frame: dict[str, Any]) -> tuple[list[float], list[float]]:
        """Fixed guns' average facing and mount point, in the ship's right/up/forward frame (for point_ship)."""
        from . import steering

        world = steering.normalize([sum(g["forward"][i] for g in guns) for i in range(3)])
        centre = [sum(g["position"][i] for g in guns) / len(guns) - frame["center"][i] for i in range(3)]
        return steering.to_local(world, frame), steering.to_local(centre, frame)

    @tool
    async def target_block(block: str, target_grid: str, weapons: str = "all", grid: str | None = None,
                           fire: bool = False, miss_by_m: float = 0, offset_m: list[float] | None = None,
                           offset_from: str = "block", max_rpm: float = 1.0, force: bool = False) -> str:
        """Aim at one block on another ship or station (its hydrogen tank, reactor, a turret...), not just the ship.
        weapons: "all", "turrets", "guns" (fixed: the ship turns to aim them) or comma-separated weapon names.
        Turrets wait until they're on target and lead a moving target. Checks the line of fire first and says what a
        shot would hit first (armour in front of the block is normal; another ship in the way stops a shot).
        fire=True fires one shot from each weapon that's on target.
        offset_m = [right, up, forward] meters aims at a point beside the block instead: in the block's own axes
        (offset_from="block": a seat's forward is where its pilot looks) or its ship's (offset_from="ship", as that
        ship's pilot sees it). E.g. 10 m in front of the pilot seat: offset_m=[0, 0, 10]. miss_by_m aims that many
        meters to the side, across your line of fire, for a warning shot. Firing needs allowWeapons in the config."""
        from . import steering

        ship, r = await _weapons(grid)
        try:
            tb = await resolve.block(bridge, block, target_grid)
        except ResolveError as e:
            return str(e)
        info = await bridge.call("block.get", {"blockId": tb["id"]})
        target, velocity = info["position"], info.get("gridVelocity") or [0, 0, 0]
        label = f"{info['block']['name']} on {info['gridName']}"

        spec = weapons.strip().lower()
        kinds = {"turrets": ("turret",), "guns": ("fixed",), "fixed": ("fixed",)}.get(spec, ("turret", "fixed"))
        try:
            chosen = combat.pick(r["weapons"], "all" if spec in ("all", "turrets", "guns", "fixed") else weapons, kinds)
        except ResolveError as e:
            return str(e)
        turrets = [w for w in chosen if w["kind"] == "turret"]
        guns = [w for w in chosen if w["kind"] == "fixed"]

        frame = steering.frame_of(await bridge.call("grid.motion", {"gridId": ship["id"]}))
        aim = list(target)
        where = ""
        if offset_m:
            if len(offset_m) != 3:
                return "offset_m takes three numbers: [right, up, forward] in meters."
            if offset_from == "ship":
                axes = steering.frame_of(await bridge.call("grid.motion", {"gridId": info["block"]["gridId"]}))
            else:
                axes = {"forward": info["forward"], "up": info["up"],
                        "right": info.get("right") or steering.cross(info["forward"], info["up"])}
            shift = steering.to_world(offset_m, axes)
            aim = [aim[i] + shift[i] for i in range(3)]
            parts = [f"{abs(v):g} m {pos if v > 0 else neg}" for v, pos, neg in
                     zip(offset_m, ("right", "up", "forward"), ("left", "down", "back")) if v]
            where = f", aiming {', '.join(parts)} of it ({'its ship' if offset_from == 'ship' else 'its own'} axes)"
        if miss_by_m:
            # Off to the shooter's side, across the line of fire, so the shot passes the point at that distance.
            toward = [aim[i] - frame["center"][i] for i in range(3)]
            side = steering.cross(toward, frame["up"])
            side = steering.normalize(side if steering.length(side) > 1e-6 else steering.perpendicular(toward))
            aim = [aim[i] + side[i] * miss_by_m for i in range(3)]

        lines = []
        if guns:
            local, origin = _gun_frame(guns, frame)
            lines.append(f"{_names(guns)}: " + await steering.point_ship(bridge, ship["id"], aim, local, origin,
                                                                        max_rpm=max_rpm, force=force))
        not_on_target: list[str] = []
        if turrets:
            await bridge.call("turret.aim", {"blockIds": [w["id"] for w in turrets], "position": aim, "velocity": velocity})
            moving = steering.length(velocity)
            if moving > 0.5:
                lines.append(f"Leading it at {moving:.0f} m/s.")
            on, off = await _acquire(ship["id"], turrets, aim, moving > 0.5)
            lines += _acquisition_lines(on, off)
            not_on_target = [name for name, _ in off]

        # What would each shooter hit first?
        r = await bridge.call("weapons.get", {"gridId": ship["id"]})
        now = {w["id"]: w for w in r["weapons"]}
        blocked = []
        cover: dict[str, list[tuple[str, float]]] = {}  # what's in front of the block -> (shooter, meters before it)
        for w in turrets + guns:
            start = now.get(w["id"], w)["position"]
            los = await bridge.call("los.check", {"from": start, "to": aim, "ignoreId": ship["id"], "skip": 3})
            hit = los.get("hit")
            if los.get("clear") or not hit:
                continue
            if str(hit.get("entityId")) == str(info["block"].get("gridId")) or hit.get("entity") == info["gridName"]:
                what = _block_label(hit.get("block") or {})
                if what and what != info["block"]["name"]:
                    cover.setdefault(what, []).append((w["name"], los["distance"] - hit["fromStart"]))
            else:
                blocked.append(w["name"])
                lines.append(f"! {hit.get('entity') or 'something'} is in {w['name']}'s line of fire "
                             f"({hit['fromStart']:.0f} m out).")

        for what, hits in cover.items():
            near, far = min(m for _, m in hits), max(m for _, m in hits)
            gap = f"{near:.0f}" if round(near) == round(far) else f"{near:.0f}-{far:.0f}"
            who = hits[0][0] + "'s shots" if len(hits) == 1 else f"{len(hits)} weapons' shots"
            lines.append(f"{who} meet {what} first, {gap} m before {info['block']['name']}.")

        distance = steering.length([target[i] - frame["center"][i] for i in range(3)])
        head = f"Target: {label}, {distance:.0f} m away{where}" + (f", aiming {miss_by_m:g} m to the side" if miss_by_m else "")
        if fire:
            # Only what's on target and has a clear line: a shot fired mid-swing goes wherever the barrel points.
            shooters = [w for w in turrets + guns if w["name"] not in blocked and w["name"] not in not_on_target]
            if shooters:
                fired = (await bridge.call("weapon.fire", {"blockIds": [w["id"] for w in shooters], "mode": "once"}))["fired"]
                lines.append(f"Fired one shot from {', '.join(fired)}.")
            if blocked:
                lines.append(f"Held fire on {', '.join(blocked)}: something else is in the way.")
            if not_on_target:
                lines.append(f"Held fire on {', '.join(not_on_target)}: not on target.")
        return head + ".\n" + "\n".join(lines)

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
        target, r = await _weapons(grid)
        text = combat.weapons_text(r)
        lock = await bridge.call("target.lock", {"gridId": target["id"]})
        return text + "\n" + combat.lock_text(lock)


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
        text = f"Aimed {', '.join(aimed)} {what}."
        guns = [w for w in chosen if w["kind"] == "turret"]
        if "position" in params and guns:
            # Turrets take a few seconds to swing round; say when they're really on target (fire after that).
            on, off = await _acquire(ship["id"], guns, params["position"], "velocity" in params)
            text += "\n" + "\n".join(_acquisition_lines(on, off))
        return text

    @tool
    async def fire_weapons(weapons: str, mode: str = "once", grid: str | None = None) -> str:
        """Fire weapons: mode "once" (one shot), "on" (keep firing) or "off". weapons: comma-separated names,
        or "all". Needs allowWeapons in the plugin config (off by default). Check what's in the line of fire.
        Fires straight away: after aiming, wait for aim_turrets to report "On target" (or use target_block with
        fire=True, which waits by itself), or the shot goes wherever the barrel points mid-swing."""
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
        text = reminders.prefix(h["name"], hud_mod.hud_text(h))
        if docked_too:
            for d in h["docked"]:
                other = await bridge.call("ship.hud", {"gridId": d["shipId"]})
                text += "\n\n" + reminders.prefix(other["name"], hud_mod.hud_text(other))
        return text

    # --- Ship notes: a per-ship AGENTS.md of scripts and quirks. ---

    async def _ship_name(grid: str | None) -> str:
        target = await resolve.grid(bridge, grid)
        if target.get("name"):
            return target["name"]
        listing = await bridge.call("grids.list", {})
        return next((g["name"] for g in listing["grids"] if str(g["id"]) == target["id"]), target["id"])

    @tool
    async def ship_notes(grid: str | None = None) -> str:
        """A ship's notes (its AGENTS.md): what an agent must know to operate it, such as custom scripts that change
        how it flies (cruise control, auto-docking...), how to switch them on and off, odd block layouts and the
        player's habits with it. Read before flying or reconfiguring a ship. Ships docked to it have their own."""
        name = await _ship_name(grid)
        reminders.seen(name)
        text = notes_mod.read(name)
        if text is None:
            return (f"No notes for {name} yet. If you find something about it the next agent would have to "
                    "rediscover (a script, an oddity), save it with write_ship_notes.")
        return f"{text.rstrip()}\n\n(File: {notes_mod.path_for(name)})"

    @tool
    async def write_ship_notes(text: str, grid: str | None = None, append: bool = True) -> str:
        """Save notes about a ship for future agents (its AGENTS.md, markdown), matched to the ship by name.
        Write what's unique and non-obvious: scripts and how to work them (arguments, how to tell their state, how
        they interfere with autopilot or thrusters), odd layouts, things that went wrong and how to avoid them.
        Not live status, which tools already show. append=True adds a section; append=False replaces everything
        (read ship_notes first). The old version is kept."""
        name = await _ship_name(grid)
        path = notes_mod.write(name, text, append=append)
        reminders.seen(name)
        return f"{'Added to' if append else 'Saved'} the notes for {name} ({path})."

    # --- The in-game companion: its soul, watches (callbacks) and events. ---

    @tool
    async def ai_soul() -> str:
        """The in-game AI's soul (SOUL.md): its name, personality, how it talks, and what it has learned about the
        player. Read it before changing it with write_ai_soul."""
        return f"{companion.read_soul().rstrip()}\n\n(File: {companion.soul_path()})"

    @tool
    async def write_ai_soul(text: str, append: bool = False) -> str:
        """Change the in-game AI's personality (SOUL.md, markdown) when the player asks ("be grumpier", "call me
        Captain", "your name is Jarvis") or tells you a lasting preference. Read ai_soul first and keep what they
        didn't ask to change; a 'Name: ...' line sets the name shown in chat. append=False replaces the file,
        append=True adds a section. The old version is kept. It applies from the next message."""
        path = companion.write_soul(text, append=append)
        return f"Soul {'updated' if append else 'saved'} ({path}). It applies from the next message."

    @tool
    async def ai_soul_presets() -> str:
        """The personalities that ship with the plugin (e.g. the plain helpful default, an in-universe company suit AI,
        an in-universe AI crewmate). Switch with use_ai_soul."""
        lines = [f"- {name}: {about}" for name, about in companion.presets().items()]
        return "Soul presets (use_ai_soul to switch; the current soul is kept in old/):\n" + "\n".join(lines)

    @tool
    async def use_ai_soul(preset: str, keep_notes: bool = True) -> str:
        """Switch the in-game AI to a packaged personality (see ai_soul_presets) when the player asks. The current
        SOUL.md is backed up first. keep_notes carries over what the AI learned about the player (the last section
        of the current soul, if it has notes). It applies from the next message."""
        text = companion.preset_text(preset)
        if text is None:
            return f"No preset '{preset}'. Presets: {', '.join(companion.presets())}."
        if keep_notes:
            text = companion.carry_notes(companion.read_soul(), text)
        path = companion.write_soul(text)
        name = companion.soul_name(text) or "Suit AI"
        return f"Switched to the '{preset}' soul ({name}), saved to {path}. It applies from the next message."

    @tool
    async def watch(id: str, method: str | None = None, path: str | None = None, op: str | None = None,
                    value: Any = None, label: str | None = None, note: str | None = None,
                    params: dict[str, Any] | None = None, every: float | None = None, cooldown: float | None = None,
                    once: bool | None = None) -> str:
        """Ask the game to call you back when something happens: a watch re-reads a read-only plugin method every
        `every` s (default 2), picks `path` from its result and tests it with `op` (< <= > >= == != changed) against
        `value`. When the test becomes true, the in-game AI gets an event (with your `note`: what you meant to do)
        and decides whether to tell the player; it fires again only after the test was false and `cooldown` s
        (default 120) passed. `once` removes it after firing. Same id updates a watch (unset fields kept).
        Methods: character.status (suit: oxygen, hydrogen, energy, health %), ship.hud (params {gridId}:
        hydrogen.fill, batteries.storedMWh, reactors.uraniumKg, cargo.fill, speed, damaged, docked.length...),
        grid.get, grid.motion, player.context, inventory.get, production.get, dock.get, weapons.get, sun.info,
        environment.get, sensors.get, damage.get, radio.antennas, block.get. Look at a method's result first
        (e.g. ship_hud) to pick the path. Watches are saved and survive restarts."""
        p: dict[str, Any] = {"id": id}
        for key, v in (("method", method), ("path", path), ("op", op), ("label", label), ("note", note),
                       ("params", params), ("every", every), ("cooldown", cooldown), ("once", once)):
            if v is not None:
                p[key] = v
        if value is not None:
            p["value"] = value
        r = await bridge.call("watch.set", p)
        current = r.get("current")
        return (f"{'Updated' if r.get('replaced') else 'Added'} watch:\n{companion.watch_line(r['watch'])}"
                + (f"\nCurrent value: {current}" if current is not None else ""))

    @tool
    async def list_watches() -> str:
        """The watches that call the in-game AI back (what they test, current value, whether active, errors)."""
        return companion.watches_text(await bridge.call("watch.list", {}))

    @tool
    async def remove_watch(id: str) -> str:
        """Remove a watch by id ('*' removes all, including the default suit warnings)."""
        r = await bridge.call("watch.remove", {"id": id})
        return f"Removed {r['removed']} watch{'es' if r['removed'] != 1 else ''}."

    @tool
    async def recent_events(limit: int = 20, type: str | None = None) -> str:
        """The last events the game raised for the AI (chat messages, watches firing), newest last."""
        p: dict[str, Any] = {"limit": limit}
        if type:
            p["type"] = type
        return companion.events_text(await bridge.call("events.recent", p))

    # --- Docking: connectors, landing gear, and flying a connector onto another. ---

    @tool
    async def docking_status(grid: str | None = None) -> str:
        """A ship's connectors (unconnected / connectable / connected and to what), landing gear and merge blocks,
        each with the way it faces and where it sits (meters right/up/forward of the ship's center), as seen from
        the cockpit (a station: its grid axes). E.g. "the right connector" is the one that faces right."""
        target = await resolve.grid(bridge, grid)
        r = await bridge.call("dock.get", {"gridId": target["id"]})
        frame = docking.ship_frame(await bridge.call("grid.motion", {"gridId": target["id"]}))
        return docking.status_text(r, frame)

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
        if back_off_m > 0 and (stop := reminders.gate(status["gridName"])):
            return stop
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
                   approach_m: float = 20, max_speed: float = 2.0, park_thrusters: bool = True) -> str:
        """Dock a ship onto another ship's or station's connector by flying it there: lines our connector up
        with theirs (gyros), comes in along their connector's axis from approach_m out (thrusters), and connects
        when it's Connectable. Picks free connectors unless named. Turns thrusters on to fly, and off once docked
        (park_thrusters; a docked ship's thrusters fight its carrier's thrust and dampeners). park_thrusters=False
        puts them back the way they were. Keep the path clear; it doesn't steer around things on the way to the
        approach point."""
        ship = await resolve.grid(bridge, grid)
        other = await resolve.grid(bridge, target)
        mine = await bridge.call("dock.get", {"gridId": ship["id"]})
        if stop := reminders.gate(mine["gridName"]):
            return stop
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
            if any(g["locked"] for g in mine["landingGear"]):
                # Parked on its gear: let go and lift straight off (along their connector's axis) before heading
                # for the approach point, so it doesn't drag across whatever it's sitting on.
                await bridge.call("dock.gear", {"gridId": ship["id"], "action": "unlock"})
                await pilot.start()
                pilot.target = {**t, "position": c["position"]}
                await pilot.hold(6, tolerance=0.5, timeout_s=60)
                pilot.target = t
            else:
                await pilot.start()
            await pilot.hold(approach_m, tolerance=0.5, timeout_s=300)
            await pilot.hold(1.8, tolerance=0.1, timeout_s=180, until=lambda x: x["status"] == "Connectable")
            r = await bridge.call("dock.connector", {"blockId": c["id"], "action": "connect"})
            docked = r["after"] == "Connected"
        except Exception as e:
            return f"Docking stopped: {e}. Overrides released, dampeners on.\n" + "\n".join(log[-4:])
        finally:
            await pilot.stop(docked, park=park_thrusters)
        if not docked:
            return f"Lined up but the connector didn't lock ({r['after']})."
        parked = " Thrusters off (parked)." if park_thrusters else ""
        return f"Docked {mine['gridName']}'s {c['name']} to {t['name']} on {theirs['gridName']}.{parked}"

    # --- Surfaces: height maps and landing on landing gear. ---

    async def _frame(grid_id: str, toward: Vec) -> tuple[Vec, Vec, Vec]:
        """A surface frame on a grid: normal = the grid axis closest to `toward`, plus the two other axes."""
        m = await bridge.call("grid.motion", {"gridId": grid_id})
        axes = [m["forward"], m["up"], m["right"]]
        k = max(range(3), key=lambda i: abs(docking.dot(axes[i], toward)))
        n = axes[k] if docking.dot(axes[k], toward) > 0 else docking.scale(axes[k], -1)
        rest = [axes[i] for i in range(3) if i != k]
        return n, rest[0], rest[1]

    @tool
    async def surface_map(around: str, grid: str | None = None, side: str | None = None, size_m: float = 20,
                          step_m: float = 1.0) -> str:
        """Measure a ship's or station's surface: a height map around a block, as a grid of heights in meters
        (0-9, + for 10 and up, - below the block's level, . for nothing there; @ marks the block). side: which
        face of the grid ("up", "down", "forward", ...); default: the face the block sits on, judged from the
        grid's center. Letters mark blocks it's unsafe to land on or beside: T thruster, X weapon, D/G/W drill,
        grinder, welder, H door, M rotor/hinge/piston. Good for finding flat spots, checking clearances, and seeing
        what sticks out."""
        target = await resolve.grid(bridge, grid)
        block = await resolve.block(bridge, around, grid)
        m = await bridge.call("grid.motion", {"gridId": target["id"]})
        b = await bridge.call("block.position", {"blockId": block["id"]})
        if side:
            axes = {"forward": m["forward"], "backward": docking.scale(m["forward"], -1), "up": m["up"],
                    "down": docking.scale(m["up"], -1), "right": m["right"], "left": docking.scale(m["right"], -1)}
            if side.lower() not in axes:
                return "side must be up, down, forward, backward, left or right (the grid's own axes)."
            toward = axes[side.lower()]
        else:
            toward = docking.sub(b["position"], m["center"])
        n, u, v = await _frame(target["id"], toward)
        half = max(2.0, size_m / 2)
        hm = await surface_mod.scan(bridge, b["position"], n, u, v, half, half, step_m)
        return (f"Height map of {target.get('name')} around {block.get('name') or around}: rows along one axis, "
                f"columns along the other, {step_m:g} m cells, heights relative to the block.\n" + surface_mod.render(hm, (0, 0)))

    @tool
    async def land(on: str, grid: str | None = None, near: str | None = None, max_speed: float = 1.5,
                   search_m: float = 30) -> str:
        """Land a ship on another ship or station with its landing gear / magnetic plates and lock them. Finds a
        spot where the whole ship fits flat (height-map of the surface, nothing taller than the spot under its
        footprint, and no thrusters, weapons, tools, doors or joints under it or within 2 m), flies up over everything on the way, slides over, descends slowly and locks. near: a block
        on the target to land close to (default: below the ship); search_m: how far around it to look (max ~60).
        Undocks first if it's on a connector."""
        ship = await resolve.grid(bridge, grid)
        target = await resolve.grid(bridge, on)
        mine = await bridge.call("dock.get", {"gridId": ship["id"]})
        if stop := reminders.gate(mine["gridName"]):
            return stop
        gears = [g for g in mine["landingGear"] if g["working"]]
        if not gears:
            return f"{mine['gridName']} has no working landing gear or magnetic plates."
        # Gear groups by the way they face; prefer the group facing the ship's belly, then the biggest.
        motion = await bridge.call("grid.motion", {"gridId": ship["id"], "thrusters": True})
        belly = docking.scale(motion["cockpit"]["up"], -1) if motion.get("cockpit") else docking.gear_face(gears[0])
        groups: list[list[dict[str, Any]]] = []
        for g in gears:
            for grp in groups:
                if docking.dot(docking.gear_face(grp[0]), docking.gear_face(g)) > 0.98:
                    grp.append(g)
                    break
            else:
                groups.append([g])
        group = max(groups, key=lambda grp: (docking.dot(docking.gear_face(grp[0]), belly) > 0.9, len(grp)))
        ref = group[0]
        face = docking.gear_face(ref)
        n, u, v = await _frame(target["id"], docking.scale(face, -1))
        if docking.dot(face, n) > -0.98:
            return (f"The {ref['name']} faces {math.degrees(math.acos(max(-1, min(1, -docking.dot(face, n))))):.0f} deg off "
                    f"{target.get('name')}'s nearest face; turn the ship to line them up first (landing keeps orientation).")
        if near:
            nb = await resolve.block(bridge, near, on)
            origin = (await bridge.call("block.position", {"blockId": nb["id"]}))["position"]
        else:
            # From 1 m behind the gear (our own ship is ignored), so a surface it already rests on still counts.
            down = await bridge.call("los.check", {"from": docking.add(ref["position"], face, -1), "to": docking.add(ref["position"], face, 60),
                                                   "skip": 0.0, "ignoreId": ship["id"]})
            if not down.get("hit") or down["hit"].get("entityId") not in (target["id"], None):
                return f"{target.get('name')} isn't below the ship's gear (within 60 m). Fly over it first, or give near=."
            origin = down["hit"]["position"]
        rel = [docking.sub(c, ref["position"]) for c in motion["corners"]]
        box = (min(docking.dot(r, u) for r in rel), max(docking.dot(r, u) for r in rel),
               min(docking.dot(r, v) for r in rel), max(docking.dot(r, v) for r in rel))
        reach = max(5.0, min(search_m, 65.0))
        half_u = max(abs(box[0]), abs(box[1])) + reach
        half_v = max(abs(box[2]), abs(box[3])) + reach
        hm = await surface_mod.scan(bridge, origin, n, u, v, half_u, half_v, 1.0, ignore_id=ship["id"])
        a0, b0, h0 = hm.coords(ref["position"])
        others = [(docking.dot(docking.sub(g["position"], ref["position"]), u), docking.dot(docking.sub(g["position"], ref["position"]), v))
                  for g in group[1:]]
        spots = surface_mod.find_spots(hm, box, others, (a0, b0))
        if not spots:
            return (f"No spot on {target.get('name')} within ~{reach:.0f} m where the ship fits: needs level, hazard-free "
                    f"hull under its {box[1] - box[0]:.0f} x {box[3] - box[2]:.0f} m footprint. Try near= another block or a bigger search_m.")
        spot = spots[0]
        transit = max(surface_mod.route_clearance(hm, box, (a0, b0), (spot["a"], spot["b"])), h0) + 3

        connected = [c for c in mine["connectors"] if c["status"] == "Connected"]
        log: list[str] = []
        pilot = docking.GearPilot(bridge, ship["id"], ref["id"], {"position": hm.point(a0, b0), "forward": n}, log=log.append,
                                  max_speed=max_speed, max_accel=await _safe_accel(ship["id"]))
        landed = False
        try:
            for c in connected:
                await bridge.call("dock.connector", {"blockId": c["id"], "action": "disconnect"})
            if any(g["locked"] for g in gears):
                await bridge.call("dock.gear", {"gridId": ship["id"], "action": "unlock"})
            await pilot.start()
            await pilot.hold(transit, tolerance=0.4, timeout_s=120)
            pilot.target = {"position": hm.point(spot["a"], spot["b"]), "forward": n}
            await pilot.hold(transit, tolerance=0.3, timeout_s=180)
            pilot.target = {"position": hm.point(spot["a"], spot["b"], spot["height"]), "forward": n}
            try:
                await pilot.hold(0.0, tolerance=0.05, timeout_s=180, slow_within=3.0, slow_speed=0.3,
                                 until=lambda g: g["status"] == "ReadyToLock")
            except (docking.StallError, TimeoutError):
                pass
            # Hovering a few cm short is common: press the last bit in pulses (gyros keep holding the attitude).
            await pilot.settle(lambda g: g["status"] == "ReadyToLock")
            if (await pilot.connector())["status"] == "ReadyToLock":
                r = await bridge.call("dock.gear", {"gridId": ship["id"], "action": "lock"})
                landed = any(g["mode"] == "Locked" for g in r["changed"])
        except Exception as e:
            return f"Landing stopped: {e}. Overrides released, dampeners on.\n" + "\n".join(log[-4:])
        finally:
            await pilot.stop(landed)
        if not landed:
            return "Came down on the spot but the gear didn't lock (not ReadyToLock). Overrides released, dampeners on."
        return (f"Landed {mine['gridName']} on {target.get('name')}: {', '.join(g['name'] for g in group)} locked, "
                f"{spot['distance']:.0f} m from where it started, over nothing taller than {transit - 3:.1f} m on the way.")

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
