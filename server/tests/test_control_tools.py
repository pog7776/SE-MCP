from __future__ import annotations

import pytest

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import SHIP_ID, TOKEN, FakeGame, FakeGameError, default_handlers
from .test_tools import call_tool

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def game():
    async with FakeGame() as fake:
        yield fake


@pytest.fixture
def server(game):
    return create_server(Bridge(port=game.port, token=TOKEN))


async def test_block_action_by_name(server, game):
    text = await call_tool(server, "block_action", block="Battery", action="OnOff_Off")
    assert ("block.action", {"blockId": "11", "action": "OnOff_Off"}) in game.calls
    assert text == "OnOff_Off on 1 block(s): Battery"


async def test_group_action(server, game):
    text = await call_tool(server, "group_action", group="Thrusters", action="OnOff_On")
    assert ("block.action", {"gridId": SHIP_ID, "group": "Thrusters", "action": "OnOff_On"}) in game.calls
    assert "2 block(s)" in text


async def test_set_property_reports_before_and_after(server):
    text = await call_tool(server, "set_block_property", block="Battery", property="Radius", value=12.5)
    assert text == "Battery: Radius 0 -> 12.5"


async def test_toggle_only_switches_blocks_that_need_it(server, game):
    text = await call_tool(server, "toggle_blocks", on=True, type="thrust")
    actions = [p for m, p in game.calls if m == "block.action"]
    # "Hydrogen Thruster" is off in the fake; "Hydrogen Thruster 2" is already on.
    assert actions == [{"blockId": "12", "action": "OnOff_On"}]
    assert "Turned on: Hydrogen Thruster" in text
    assert "Already on: Hydrogen Thruster 2" in text


async def test_toggle_requires_a_filter(server, game):
    text = await call_tool(server, "toggle_blocks", on=False)
    assert "at least one" in text
    assert not any(m == "block.action" for m, _ in game.calls)


async def test_rename_and_say(server):
    assert await call_tool(server, "rename_block", block="Cargo Main", new_name="Ore Box") == "Renamed 'Cargo Main' to 'Ore Box'."
    assert await call_tool(server, "say", text="Low hydrogen!") == "Shown on the HUD."


async def test_production_flags_blocked_assembler(server):
    text = await call_tool(server, "production")
    assert "Basic Refinery (Basic Refinery): producing" in text
    assert "IronOreToIngot x5,280" in text
    assert "Assembler (Assembler, assembly): NOT producing" in text


async def test_read_only_mode_is_reported():
    handlers = default_handlers()

    def read_only(_):
        raise FakeGameError("read_only", "The plugin is in read-only mode (readOnly in config.json)")

    handlers["block.action"] = read_only
    async with FakeGame(handlers) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "block_action", block="Battery", action="OnOff_Off")
    assert text.startswith("Error (read_only)")
