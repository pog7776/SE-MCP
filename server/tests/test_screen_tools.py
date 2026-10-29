from __future__ import annotations

from typing import Any

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

SEAT = "7400001"


def surface(index: int, name: str, content: str, script: str | None = None, text: str = "") -> dict[str, Any]:
    return {"index": index, "name": name, "content": content, "script": script, "images": [], "textLength": len(text), "text": text}


def handlers(calls: list) -> dict[str, Any]:
    h = default_handlers()
    blocks_list = h["blocks.list"]
    surfaces = [surface(0, "Large Display", "SCRIPT", "TSS_ArtificialHorizon"), surface(1, "Bottom Right Screen", "NONE")]

    def blocks(p: dict) -> dict:
        if "seat" in (p.get("nameContains") or "").lower():
            return {"total": 1, "offset": 0, "blocks": [{"id": SEAT, "name": "Control Seat", "type": "Cockpit"}]}
        return blocks_list(p)

    def write(p: dict) -> dict:
        calls.append(("write", p))
        before = surfaces[p["surface"]]
        after = {**before, "content": "TEXT_AND_IMAGE", "textLength": len(p["text"]), "text": p["text"]}
        return {"block": "Control Seat", "before": before, "after": after}

    def mode(p: dict) -> dict:
        calls.append(("mode", p))
        return {"block": "Control Seat", "before": surfaces[p["surface"]],
                "after": {**surfaces[p["surface"]], "content": "SCRIPT", "script": p.get("script")}}

    h.update({
        "blocks.list": blocks, "screen.write": write, "screen.mode": mode,
        "screens.list": lambda p: {"blocks": [{"blockId": SEAT, "name": "Control Seat", "type": "Cockpit", "working": True,
                                                "surfaces": surfaces}]},
    })
    return h


async def call(tool: str, args: dict[str, Any], calls: list) -> str:
    async with FakeGame(handlers(calls)) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
            return "\n".join(c.text for c in result.content)
        finally:
            await bridge.close()


async def test_list_screens_describes_each_surface():
    text = await call("list_screens", {"block": "Control Seat"}, [])
    assert "#0 Large Display: script TSS_ArtificialHorizon" in text and "#1 Bottom Right Screen: off" in text


async def test_write_by_screen_name_with_style():
    calls: list = []
    text = await call("write_screen", {"block": "Control Seat", "surface": "bottom right", "text": "hi", "color": "#00FF00"}, calls)
    assert calls == [("write", {"blockId": SEAT, "surface": 1, "text": "hi", "append": False, "fontColor": "#00FF00"})]
    assert text == "Wrote to Control Seat #1 Bottom Right Screen."


async def test_overwriting_a_script_says_how_to_restore_it():
    text = await call("write_screen", {"block": "Control Seat", "text": "hi"}, [])
    assert "was showing the script TSS_ArtificialHorizon" in text
    text = await call("set_screen_mode", {"block": "Control Seat", "mode": "script", "script": "TSS_ArtificialHorizon"}, [])
    assert "now script TSS_ArtificialHorizon" in text
