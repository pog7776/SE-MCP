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


async def test_every_tool_declares_fair_play_metadata():
    from se_mcp.fair import TOOLS

    names = {t.name for t in await create_server(Bridge(port=1, token="x")).list_tools()}
    assert names - TOOLS.keys() == set(), "add these tools to se_mcp/fair.py"
    assert TOOLS.keys() - names == set(), "fair.py lists tools that no longer exist"


async def test_inspect_block_shows_where_it_is_and_how_its_turned():
    handlers = default_handlers()
    get = handlers["block.get"]
    handlers["block.get"] = lambda p: {**get(p), "position": [10.0, 2.0, -5.0], "forward": [0.0, 0.0, -1.0],
                                       "up": [0.0, 1.0, 0.0], "right": [1.0, 0.0, 0.0], "cell": [4, 0, -2], "cellSize": 2.5}
    handlers["grid.motion"] = lambda p: {"center": [0.0, 0.0, 0.0], "forward": [0.0, 0.0, -1.0], "up": [0.0, 1.0, 0.0],
                                         "right": [1.0, 0.0, 0.0], "cockpit": None}
    async with FakeGame(handlers) as game:
        text = await call_tool(create_server(Bridge(port=game.port, token=TOKEN)), "inspect_block", block="Battery")
    assert "Position: [10.0, 2.0, -5.0] (world)" in text
    assert "On Miner: faces forward, 10 m right, 2 m up, 5 m forward of center (by the grid's axes)" in text
    assert "Axes (world): forward [0.000, 0.000, -1.000], up [0.000, 1.000, 0.000], right [1.000, 0.000, 0.000]" in text
    assert "Grid cell: (4, 0, -2), 2.5 m cells" in text
