from __future__ import annotations

from typing import Any

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

SEAT = "7400001"
SURFACES = [{"index": 0, "name": "Large Display", "content": "NONE", "script": None, "images": [], "textLength": 0, "text": ""},
            {"index": 1, "name": "Top Left Screen", "content": "NONE", "script": None, "images": [], "textLength": 0, "text": ""}]


def mirror(surface: int = 1, **extra: Any) -> dict[str, Any]:
    return {"blockId": SEAT, "block": "Control Seat", "surface": surface, "window": "Spotify Premium",
            "process": "Spotify", "handle": "0x1A2B", "fps": 15, "resolution": [512, 512], "captured": 40,
            "uploaded": 38, "error": None, **extra}


def handlers(calls: list) -> dict[str, Any]:
    h = default_handlers()
    blocks_list = h["blocks.list"]
    running = [mirror()]

    def blocks(p: dict) -> dict:
        if "seat" in (p.get("nameContains") or "").lower():
            return {"total": 1, "offset": 0, "blocks": [{"id": SEAT, "name": "Control Seat", "type": "Cockpit"}]}
        return blocks_list(p)

    def record(name: str, result: Any):
        def handler(p: dict) -> Any:
            calls.append((name, p))
            return result(p) if callable(result) else result
        return handler

    h.update({
        "blocks.list": blocks,
        "screens.list": lambda p: {"blocks": [{"blockId": SEAT, "name": "Control Seat", "type": "Cockpit", "working": True,
                                                "surfaces": SURFACES}]},
        "mirror.windows": record("windows", lambda p: {"windows": [] if p.get("filter") == "nothing" else [
            {"handle": "0x1A2B", "title": "Spotify Premium", "process": "Spotify", "width": 1280, "height": 720}]}),
        "mirror.start": record("start", lambda p: mirror(p["surface"], fps=p["fps"])),
        "mirror.stop": record("stop", lambda p: {"stopped": 1 if p else len(running)}),
        "mirror.list": record("list", {"mirrors": running, "gameFps": 60, "frameMs": 16.7}),
        "browser.open": record("browser", lambda p: mirror(p["surface"], kind="browser", window="Browser",
                                                              process="https://" + p["url"], fps=p["fps"])),
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


async def test_list_windows_shows_title_process_and_handle():
    calls: list = []
    text = await call("list_windows", {"match": "spot"}, calls)
    assert "'Spotify Premium' (Spotify, 1280x720, handle 0x1A2B)" in text
    assert calls == [("windows", {"filter": "spot"})]


async def test_list_windows_empty():
    assert await call("list_windows", {"match": "nothing"}, []) == "No matching windows."


async def test_mirror_window_resolves_screen_name_and_passes_title():
    calls: list = []
    text = await call("mirror_window", {"block": "Control Seat", "window": "spotify", "surface": "top left", "fps": 20}, calls)
    assert calls == [("start", {"blockId": SEAT, "surface": 1, "fps": 20, "resolution": 1024, "window": "spotify"})]
    assert text.startswith("Mirroring: Control Seat #1: 'Spotify Premium' (Spotify) at 20 fps, 512x512")


async def test_mirror_window_takes_a_handle():
    calls: list = []
    await call("mirror_window", {"block": "Control Seat", "window": "0x1A2B"}, calls)
    assert calls[0][1]["handle"] == "0x1A2B" and "window" not in calls[0][1]


async def test_stop_mirror_on_one_screen():
    calls: list = []
    text = await call("stop_mirror", {"block": "Control Seat", "surface": 1}, calls)
    assert calls == [("stop", {"blockId": SEAT, "surface": 1})]
    assert "back as it was" in text


async def test_stop_all_mirrors_lists_what_ran():
    calls: list = []
    text = await call("stop_mirror", {}, calls)
    assert [c[0] for c in calls] == ["list", "stop"]
    assert text.startswith("Stopped 1 mirror(s):") and "Spotify Premium" in text


async def test_list_mirrors():
    text = await call("list_mirrors", {}, [])
    assert text.startswith("- Control Seat #1: 'Spotify Premium' (Spotify) at 15 fps, 512x512, 38 frames shown")


async def test_list_mirrors_reports_game_frame_rate():
    text = await call("list_mirrors", {}, [])
    assert text.endswith("Game: 60 fps, 16.7 ms/frame.")


async def test_show_webpage_passes_url_and_describes_page():
    calls: list = []
    text = await call("show_webpage", {"block": "Control Seat", "url": "youtube.com", "surface": 1}, calls)
    assert calls == [("browser", {"blockId": SEAT, "surface": 1, "url": "youtube.com", "fps": 30, "resolution": 1024})]
    assert text.startswith("Showing: Control Seat #1: web page https://youtube.com at 30 fps")
