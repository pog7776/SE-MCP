"""Suit controls, world save and highlights."""

from __future__ import annotations

from typing import Any

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers


def handlers(calls: list) -> dict[str, Any]:
    h = default_handlers()
    suit = {"helmet": True, "jetpack": False, "dampeners": True, "lights": False}
    blocks_list = h["blocks.list"]

    def switch(p: dict) -> dict:
        calls.append(("switch", p))
        changed = [f"{k} {'on' if v else 'off'}" for k, v in p.items() if suit[k] != v]
        suit.update(p)
        return {"changed": changed, **suit}

    def blocks(p: dict) -> dict:
        if p.get("type") == "thrust":
            return {"total": 2, "offset": 0, "blocks": [
                {"id": "81", "name": "Thruster A", "integrity": 100}, {"id": "82", "name": "Thruster B", "integrity": 40}]}
        return blocks_list(p)

    def highlight(p: dict) -> dict:
        calls.append(("highlight", p))
        return {"highlighted": [f"block {i}" for i in p["blockIds"]], "skipped": []}

    h.update({
        "character.switch": switch, "blocks.list": blocks, "highlight.set": highlight,
        "highlight.clear": lambda p: {"cleared": 2},
        "session.save": lambda p: {"saved": True, "world": "Dev", "path": "C:/saves/Dev", "backups": 5, "seconds": 1.2},
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


async def test_suit_controls_only_send_what_was_asked():
    calls: list = []
    text = await call("suit_controls", {"lights": True}, calls)
    assert calls == [("switch", {"lights": True})]
    assert text == "Switched lights on. Now: helmet on, jetpack off, dampeners on, lights on."
    assert "Say which" in await call("suit_controls", {}, [])


async def test_highlight_damaged_only():
    calls: list = []
    text = await call("highlight_blocks", {"type": "thrust", "damaged": True}, calls)
    assert calls == [("highlight", {"blockIds": ["82"], "color": "#FFD000", "pulseFrames": 60})]
    assert text.startswith("Highlighted 1 block(s)")
    assert await call("clear_highlights", {}, []) == "Cleared 2 highlight(s)."


async def test_save_world():
    text = await call("save_world", {}, [])
    assert text.startswith("Saved 'Dev' in 1.2 s") and "keeps 5" in text
