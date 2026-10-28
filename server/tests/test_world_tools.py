from __future__ import annotations

import pytest

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import SHIP_ID, STATION_ID, TOKEN, FakeGame, default_handlers
from .test_tools import call_tool

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def game():
    async with FakeGame() as fake:
        yield fake


@pytest.fixture
def server(game):
    return create_server(Bridge(port=game.port, token=TOKEN))


async def test_se_status_includes_context(server):
    text = await call_tool(server, "se_status")
    assert "Controlling: Cockpit (Cockpit) on Miner" in text
    assert "Looking at: Basic Refinery on Miner" in text


async def test_ship_status_defaults_to_controlled_grid(server, game):
    text = await call_tool(server, "ship_status")
    assert ("grid.get", {"gridId": SHIP_ID}) in game.calls
    assert "Batteries: 1x, 83.3%" in text
    assert "Basic Refinery: 80.5% (needs 4x SteelPlate)" in text


async def test_ship_status_falls_back_to_nearest_grid_on_foot():
    async with FakeGame(default_handlers(controlling=False)) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        await call_tool(server, "ship_status")
    assert ("grid.get", {"gridId": STATION_ID}) in game.calls


async def test_grid_by_exact_name_beats_substring(server, game):
    await call_tool(server, "ship_status", grid="miner")
    assert ("grid.get", {"gridId": SHIP_ID}) in game.calls


async def test_ambiguous_grid_name_lists_options(server):
    text = await call_tool(server, "ship_status", grid="Min")
    assert "matches 2 grids" in text
    assert "'Miner'" in text and "'Miner Drone'" in text


async def test_unknown_block_suggests_close_names(server):
    text = await call_tool(server, "inspect_block", block="Batery")
    assert "Did you mean" in text and "'Battery'" in text


async def test_inspect_block_hides_styling_properties(server):
    text = await call_tool(server, "inspect_block", block="Battery")
    assert "OnOff=True" in text
    assert "Font=" not in text


async def test_list_blocks_flags(server):
    text = await call_tool(server, "list_blocks", type="thrust")
    assert "Hydrogen Thruster (Hydrogen Thruster) [OFF]" in text
    assert "Basic Refinery" not in text


async def test_inventory_filter_and_amounts(server):
    text = await call_tool(server, "inventory", item="iron")
    assert "Ore: Iron 8,171" in text
    assert "Ingot: Iron 120" in text
    assert "Ice" not in text


async def test_suit(server):
    assert "WelderItem 1" in await call_tool(server, "inventory", suit=True)
    assert "Jetpack on" in await call_tool(server, "suit_status")


async def test_no_world_loaded_is_a_clear_error():
    async with FakeGame(default_handlers(session_loaded=False)) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "ship_status")
    assert text == "Error (no_session): No world is loaded"
