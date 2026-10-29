from __future__ import annotations

from typing import Any

from se_mcp import toolbar
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

SEAT = {"owner": "Control Seat", "type": "Ship", "pages": 9, "currentPage": 1, "slots": [
    {"index": 0, "page": 1, "slot": 1, "text": "Camera - Front - View", "kind": "block", "blockId": "8600001",
     "target": "Camera - Front", "action": "View", "actionName": "View"},
    {"index": 3, "page": 1, "slot": 4, "text": "Turrets - Toggle", "kind": "group", "target": "Turrets",
     "action": "OnOff", "actionName": "Toggle block On/Off"},
    {"index": 9, "page": 2, "slot": 1, "text": "Welder", "kind": "weapon"},
]}


def test_toolbar_text_groups_by_page():
    text = toolbar.toolbar_text(SEAT)
    assert "  1.4  Turrets (group): Toggle block On/Off  [action OnOff]" in text
    assert " Page 2:\n  2.1  Welder (weapon)" in text
    assert toolbar.toolbar_text(SEAT, page=3).endswith("page 3 is empty")


async def _call(handlers: dict[str, Any], tool: str, args: dict[str, Any]) -> str:
    async with FakeGame(handlers) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
        finally:
            await bridge.close()
    return "\n".join(c.text for c in result.content)


async def test_set_player_slot_with_a_block_to_build():
    h = default_handlers()
    h["blockdefs.find"] = lambda p: {"blocks": [{"id": "OreDetector/SmallBlockOreDetector", "name": "Ore Detector", "size": "small"},
                                                {"id": "OreDetector/LargeOreDetector", "name": "Ore Detector", "size": "large"}]}
    sent: list[dict[str, Any]] = []

    def set_slot(p: dict[str, Any]) -> dict[str, Any]:
        sent.append(p)
        return {"owner": "player", "before": None,
                "after": {"index": 26, "page": 3, "slot": 9, "text": "Ore Detector", "kind": "cubeblock"}}

    h["toolbar.set"] = set_slot
    text = await _call(h, "set_toolbar_slot", {"page": 3, "slot": 9, "item": "ore detector"})
    assert sent[0] == {"page": 3, "slot": 9, "definitionId": "OreDetector/LargeOreDetector"}  # large preferred, no blockId = player
    assert text == "your toolbar: empty  ->  3.9  Ore Detector (cubeblock)"


def test_group_text():
    r = {"name": "Lasers", "created": True, "deleted": False, "before": 0, "blocks": [{"id": "1", "name": "Laser Antenna"}]}
    assert toolbar.group_text(r) == "Created group 'Lasers' (0 -> 1 blocks): Laser Antenna"
