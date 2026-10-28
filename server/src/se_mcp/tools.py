"""MCP tools. Naming, formatting and convenience live here so the game side can stay thin."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .bridge import Bridge, BridgeError

INSTRUCTIONS = (
    "Tools for assisting a player in Space Engineers 1 through the SEMcp game plugin. "
    "Start with se_status to see whether the game is running and a world is loaded."
)


def create_server(bridge: Bridge | None = None) -> MCPServer:
    bridge = bridge or Bridge()
    server = MCPServer("space-engineers", instructions=INSTRUCTIONS)

    @server.tool()
    async def se_status() -> str:
        """Check the connection to Space Engineers: game and plugin versions, loaded world, player."""
        try:
            info = await bridge.call("ping")
        except BridgeError as e:
            return f"Not connected: {e}"

        lines = [
            f"Connected to Space Engineers {info.get('game')} ({info.get('runtime')})",
            f"Plugin host {info.get('host')}, generation {info.get('generation')}",
        ]
        if info.get("sessionLoaded"):
            lines.append(f"World: {info.get('world')}")
            lines.append(f"Player: {info.get('player') or 'unknown'}")
        else:
            lines.append("No world loaded (main menu or loading).")
        return "\n".join(lines)

    return server
