"""HUD overlay: drawing, the ship panel and removal."""

from __future__ import annotations

from typing import Any

import pytest

from se_mcp import overlay
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import SHIP_ID, TOKEN, FakeGame, default_handlers


def handlers(calls: list) -> dict[str, Any]:
    h = default_handlers()
    drawn: dict[str, dict] = {}

    def set_(p: dict) -> dict:
        calls.append(p)
        for e in p["elements"]:
            drawn[e["id"]] = e
        return {"set": [e["id"] for e in p["elements"]], "total": len(drawn)}

    def remove(p: dict) -> dict:
        removed = sum(drawn.pop(i, None) is not None for i in p["ids"])
        return {"removed": removed, "total": len(drawn)}

    h.update({
        "overlay.set": set_,
        "overlay.remove": remove,
        "overlay.list": lambda p: {"elements": [{"id": k, "kind": e.get("kind"), "x": e.get("x", 0), "y": e.get("y", 0),
                                                "text": e.get("text"), "bind": (e.get("bind") or {}).get("method")}
                                               for k, e in drawn.items()]},
        "overlay.clear": lambda p: {"removed": len(drawn)},
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


def test_ship_panel_layout():
    elements = overlay.ship_panel("p", "42", "Red Ship", ["hydrogen", "uranium"], 0.1, 0.2)
    ids = [e["id"] for e in elements]
    assert ids == ["p-panel", "p-title", "p-hydrogen", "p-uranium"]
    h2 = elements[2]
    assert h2["kind"] == "bar" and h2["valuePath"] == "hydrogen.fill" and h2["lowColor"] == "#FF5050"
    assert h2["bind"] == {"method": "ship.hud", "params": {"gridId": "42"}, "every": 1}
    assert elements[1]["text"] == "RED SHIP"
    with pytest.raises(ValueError, match="Unknown gauge"):
        overlay.ship_panel("p", "42", "x", ["warp"], 0, 0)


async def test_hud_draw_resolves_bound_grid_names():
    calls: list = []
    text = await call("hud_draw", {"elements": [
        {"id": "t", "kind": "text", "x": 0.5, "y": 0.1, "text": "H2 {hydrogen.fill}%",
         "bind": {"method": "ship.hud", "params": {"grid": "current"}}}]}, calls)
    assert calls[0]["elements"][0]["bind"]["params"] == {"gridId": SHIP_ID}
    assert text.startswith("Drew t")


async def test_ship_panel_tool_and_clear():
    calls: list = []
    text = await call("hud_ship_panel", {"show": ["battery"]}, calls)
    assert f"Panel 'ship-{SHIP_ID}'" in text and "3 elements" in text
    assert [e["id"] for e in calls[0]["elements"]][-1] == f"ship-{SHIP_ID}-battery"
    assert "Unknown gauge" in await call("hud_ship_panel", {"show": ["warp"]}, [])
    assert await call("hud_clear", {}, []) == "Cleared 0 HUD element(s)."
    assert await call("hud_list", {}, []) == "Nothing drawn on the HUD."
