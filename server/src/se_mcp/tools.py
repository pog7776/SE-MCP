"""MCP tools. Naming, formatting and convenience live here so the game side can stay thin."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import Image, MCPServer

from . import autopilot
from . import format as fmt
from . import items as items_mod
from . import organizer
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
    async def production(grid: str | None = None) -> str:
        """Assemblers, refineries and survival kits on a ship: producing or not, queues, input/output fill."""
        target = await resolve.grid(bridge, grid)
        return fmt.production(await bridge.call("production.get", {"gridId": target["id"]}))

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
        text = f"Moved {moved} {name}: {r['from']} -> {r['to']}."
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

    return server
