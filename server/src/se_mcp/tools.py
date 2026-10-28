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
