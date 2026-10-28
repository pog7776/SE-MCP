"""MCP tools. Naming, formatting and convenience live here so the game side can stay thin."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import Image, MCPServer

from . import format as fmt
from . import resolve
from .bridge import Bridge, BridgeError
from .resolve import ResolveError

INSTRUCTIONS = (
    "Tools for assisting a player in Space Engineers 1 through the SEMcp game plugin. "
    "Start with se_status to see whether the game is running and what the player is in or looking at. "
    "Grid and block arguments take a name (fuzzy matched) or an id; leaving `grid` empty means the ship "
    "the player is controlling or standing in. Use screenshot to see what the player sees."
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
        return server.tool()(_friendly_errors(fn))

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

    @tool
    async def align_to_sun(grid: str | None = None, max_rpm: float = 1.0, force: bool = False) -> str:
        """Rotate a ship (using gyro overrides) so its solar panels face the sun, then release the gyros.

        Takes a little time: a short test spin, then a slow turn (max_rpm). Refuses in natural gravity unless
        force=True, since turning a landed ship can tip it over. The player in the ship will feel it turn."""
        from .steering import align_to_sun as align

        target = await resolve.grid(bridge, grid)
        return await align(bridge, target["id"], max_rpm=max_rpm, force=force)

    @server.tool()
    async def screenshot(max_width: int = 1280) -> Image | str:
        """See what the player sees: a screenshot of the Space Engineers window (works while it's covered)."""
        from .screenshot import ScreenshotError, screenshot_jpeg

        try:
            data, _ = await asyncio.to_thread(screenshot_jpeg, max_width)
        except ScreenshotError as e:
            return f"Screenshot failed: {e}"
        return Image(data=data, format="jpeg")

    return server
