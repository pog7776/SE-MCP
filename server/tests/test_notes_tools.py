from __future__ import annotations

import pytest

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame
from .test_tools import call_tool

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def notes_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_MCP_SHIP_NOTES", str(tmp_path))
    return tmp_path


async def test_no_notes_yet():
    async with FakeGame() as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "ship_notes", grid="Miner")
    assert "No notes for Miner yet" in text


async def test_write_then_read_and_append(notes_folder):
    async with FakeGame() as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        await call_tool(server, "write_ship_notes", grid="Miner", text="Cruise script on the PB.", append=False)
        await call_tool(server, "write_ship_notes", grid="Miner", text="## Drills\nGroup 'Drills'.")
        text = await call_tool(server, "ship_notes", grid="Miner")
    assert text.startswith("# Miner\n\nCruise script on the PB.\n\n## Drills")
    assert (notes_folder / "Miner.md").exists()
    assert len(list((notes_folder / "old").iterdir())) == 1


async def test_status_and_hud_point_at_unread_notes(notes_folder):
    (notes_folder / "home base.md").write_text("# Home Base\nQuirks.", encoding="utf-8")
    async with FakeGame() as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        before = await call_tool(server, "se_status")
        await call_tool(server, "ship_notes", grid="Home Base")
        after = await call_tool(server, "se_status")
    assert "Home Base has ship notes" in before
    assert "ship notes" not in after


async def test_flying_stops_once_for_unread_notes(notes_folder):
    (notes_folder / "Miner.md").write_text("# Miner\nTurn cruise off first.", encoding="utf-8")
    rc = {"id": "50", "name": "Remote Control", "gridName": "Miner", "autopilot": True, "speed": 0, "speedLimit": 50,
          "flightMode": "OneWay", "direction": "Forward", "waypoints": []}
    async with FakeGame() as game:
        game.handlers["blocks.list"] = lambda p: {"total": 1, "offset": 0, "blocks": [{"id": "50", "name": "Remote Control"}]}
        game.handlers["rc.get"] = lambda p: rc
        game.handlers["rc.fly_to"] = lambda p: rc
        server = create_server(Bridge(port=game.port, token=TOKEN))
        first = await call_tool(server, "fly_to", target="1, 2, 3")
        second = await call_tool(server, "fly_to", target="1, 2, 3")
        flew = [m for m, _ in game.calls if m == "rc.fly_to"]
    assert first.startswith("Not moving it yet") and "ship_notes('Miner')" in first
    assert second.startswith("Autopilot engaged")
    assert len(flew) == 1
