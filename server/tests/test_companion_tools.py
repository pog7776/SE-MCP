from __future__ import annotations

import pytest

from se_mcp import companion
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, FakeGameError, default_handlers
from .test_tools import call_tool


@pytest.fixture(autouse=True)
def soul_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_MCP_RUNNER_DIR", str(tmp_path))


async def test_soul_starts_from_the_default_and_can_be_rewritten(tmp_path):
    async with FakeGame() as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "ai_soul")
        assert "## Personality" in text
        assert (tmp_path / "SOUL.md").exists()

        await call_tool(server, "write_ai_soul", text="# Soul\n\nName: Jarvis\n\nGrumpy but loyal.")
    assert companion.soul_name(companion.read_soul()) == "Jarvis"
    assert len(list((tmp_path / "old").glob("*SOUL.md"))) == 1


def test_default_names_dont_count():
    assert companion.soul_name(companion.DEFAULT_SOUL) is None
    assert companion.soul_name("no name here") is None
    assert companion.soul_name("Name:   Friday  ") == "Friday"


async def test_watch_sends_only_given_fields_and_shows_the_current_value():
    handlers = default_handlers()

    def watch_set(p):
        if p.get("method") == "nope":
            raise FakeGameError("invalid_params", "method must be one of: character.status, ship.hud")
        w = {"every": 2, "cooldown": 120, "op": "<", **p}
        return {"watch": w, "current": 55.5, "replaced": False}

    handlers["watch.set"] = watch_set
    async with FakeGame(handlers) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        text = await call_tool(server, "watch", id="red-h2", method="ship.hud", params={"gridId": "1"},
                               path="hydrogen.fill", op="<", value=20, label="Red Ship hydrogen low",
                               note="Suggest refuelling at base.")
        bad = await call_tool(server, "watch", id="x", method="nope", path="a")

    sent = next(p for m, p in game.calls if m == "watch.set" and p["id"] == "red-h2")
    assert sent == {"id": "red-h2", "method": "ship.hud", "params": {"gridId": "1"}, "path": "hydrogen.fill", "op": "<",
                    "value": 20, "label": "Red Ship hydrogen low", "note": "Suggest refuelling at base."}
    assert "Added watch:\n- red-h2: Red Ship hydrogen low | ship.hud {'gridId': '1'}: hydrogen.fill < 20" in text
    assert "note: Suggest refuelling at base." in text
    assert "Current value: 55.5" in text
    assert bad.startswith("Error (invalid_params): method must be one of")


async def test_list_and_events_text():
    handlers = default_handlers()
    handlers["watch.list"] = lambda p: {"file": "w.json", "watches": [
        {"id": "suit-oxygen", "label": "Suit oxygen low", "method": "character.status", "path": "oxygen", "op": "<",
         "value": 20, "every": 2, "cooldown": 120, "current": 12.0, "active": True, "fired": 1},
        {"id": "dock", "method": "ship.hud", "path": "docked.length", "op": "changed", "every": 2, "cooldown": 0,
         "error": "No world is loaded"}]}
    handlers["events.recent"] = lambda p: {"events": [
        {"id": 7, "type": "chat", "source": "comms", "at": "2026-10-30T17:20:01.0Z", "text": "hello"},
        {"id": 8, "type": "watch", "source": "character.status", "at": "2026-10-30T17:21:00.0Z", "text": "Suit oxygen low"}]}
    async with FakeGame(handlers) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        watches = await call_tool(server, "list_watches")
        events = await call_tool(server, "recent_events")

    assert "2 watches (saved in w.json)" in watches
    assert ("- suit-oxygen: Suit oxygen low | character.status: oxygen < 20 (every 2 s, cooldown 120 s) "
            "[now 12, ACTIVE, fired 1x]") in watches
    assert "dock | ship.hud: docked.length changed" in watches and "error: No world is loaded" in watches
    assert events == "- #7 17:20:01 chat via comms: hello\n- #8 17:21:00 watch via character.status: Suit oxygen low"
