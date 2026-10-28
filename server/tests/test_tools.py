from __future__ import annotations

import socket

import pytest

from se_mcp.bridge import Bridge, BridgeError
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, FakeGameError, default_handlers

pytestmark = pytest.mark.asyncio


async def call_tool(server, name: str, **arguments) -> str:
    result = await server.call_tool(name, arguments)
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_se_status_with_world_loaded():
    async with FakeGame() as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "se_status")
    assert "Space Engineers 1.210.014" in text
    assert "World: Test World" in text
    assert "Player: Engineer" in text


async def test_se_status_in_main_menu():
    async with FakeGame(default_handlers(session_loaded=False)) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "se_status")
    assert "No world loaded" in text


async def test_se_status_when_game_not_running():
    server = create_server(Bridge(port=free_port(), token=TOKEN))
    text = await call_tool(server, "se_status")
    assert text.startswith("Not connected:")
    assert "isn't running" in text


async def test_bad_token_is_rejected():
    async with FakeGame() as game:
        bridge = Bridge(port=game.port, token="wrong")
        with pytest.raises(BridgeError) as error:
            await bridge.call("ping")
    assert error.value.kind == "access_denied"


async def test_game_errors_keep_their_kind():
    def no_session(_):
        raise FakeGameError("no_session", "No world is loaded")

    async with FakeGame({"grids.list": no_session}) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        with pytest.raises(BridgeError) as error:
            await bridge.call("grids.list")
        await bridge.close()
    assert error.value.kind == "no_session"


async def test_reconnects_after_game_restart():
    async with FakeGame() as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        assert (await bridge.call("ping"))["world"] == "Test World"

    with pytest.raises(BridgeError) as error:
        await bridge.call("ping")
    assert error.value.kind in ("disconnected", "not_running")

    async with FakeGame() as game:
        bridge._port = game.port  # the "restarted game" listens on a new port in the test
        assert (await bridge.call("ping"))["world"] == "Test World"
        await bridge.close()
