from __future__ import annotations

import asyncio
import contextlib

from se_mcp.bridge import Bridge
from se_mcp.runner import SILENT, Conversation, Runner, StatusHud, Turn, chat_lines, context_line, event_message

from .fake_bridge import TOKEN, FakeGame, default_handlers

SOUL = "# Soul\n\nName: Suit AI\n"


def chat(text: str, source: str = "chat", **context) -> dict:
    ctx = {"player": "dingus", "persona": "suit", "ship": None, "shipId": None, **context}
    return {"id": 1, "type": "chat", "source": source, "text": text, "context": ctx, "data": {}}


def watch_event(text: str = "Suit oxygen low: oxygen is 12 (< 20)", note: str | None = "Warn the player.") -> dict:
    return {"id": 2, "type": "watch", "source": "character.status", "text": text,
            "context": {"player": "dingus", "persona": "suit"}, "data": {"watch": "suit-oxygen", "note": note}}


class FakeBrain:
    def __init__(self, reply: str = "Hydrogen is at 80%.", fail: Exception | None = None):
        self.reply, self.fail = reply, fail
        self.asked: list[str] = []
        self.resets = 0

    async def ask(self, text: str, progress=None) -> Turn:
        self.asked.append(text)
        if progress:
            progress("ship_hud")
        if self.fail:
            raise self.fail
        return Turn(self.reply, "session-2")

    async def reset(self) -> None:
        self.resets += 1


def make_runner(game: FakeGame, brain: FakeBrain, soul: str = SOUL) -> Runner:
    return Runner(Bridge(port=game.port, token=TOKEN), brain, log=lambda _: None, soul=lambda: soul)


def handlers() -> dict:
    h = default_handlers()
    h["comms.post"] = lambda p: {"posted": True}
    h["overlay.set"] = lambda p: {"set": [e["id"] for e in p["elements"]], "total": 2}
    h["overlay.remove"] = lambda p: {"removed": len(p["ids"]), "total": 0}
    return h


def statuses(game: FakeGame) -> list[tuple[str, str]]:
    """(text, colour) of each status line drawn."""
    return [(e["text"], e["color"]) for m, p in game.calls if m == "overlay.set"
            for e in p["elements"] if e["id"] == StatusHud.TEXT]


def chats(game: FakeGame) -> list[tuple[str, str]]:
    return [(p["sender"], p["text"]) for m, p in game.calls if m == "notify" and p.get("kind") == "chat"]


def comms(game: FakeGame) -> list[tuple[str, str]]:
    return [(p["sender"], p["text"]) for m, p in game.calls if m == "comms.post"]


async def test_chat_gets_an_answer_with_its_context():
    brain = FakeBrain()
    async with FakeGame(handlers()) as game:
        await make_runner(game, brain).handle(chat("how much hydrogen?"))

    assert brain.asked[0].startswith("[Context: dingus is on foot in their suit. Sent from the chat at ")
    assert brain.asked[0].endswith("\nhow much hydrogen?")
    assert chats(game) == [("Suit AI", "Hydrogen is at 80%.")]
    assert statuses(game) == [("Suit AI: thinking... 0s", StatusHud.COLORS["thinking"]),
                              ("Suit AI: listening", StatusHud.COLORS["idle"])]


def test_status_text_shows_the_tool_time_and_queue():
    now = [100.0]
    hud = StatusHud(None, clock=lambda: now[0])
    hud.name, hud.state, hud.started = "SAM-7", "thinking", 100.0
    now[0] = 104.6
    assert hud.text() == "SAM-7: thinking... 4s"
    hud.using("point_ship")
    hud.waiting = 2
    assert hud.text() == "SAM-7: point ship... 4s (+2 waiting)" and hud.color() == StatusHud.COLORS["tool"]
    hud.state, hud.waiting, hud.error_until = "idle", 0, 110
    assert hud.text() == "SAM-7: couldn't answer" and hud.color() == StatusHud.COLORS["error"]
    now[0] = 111
    assert hud.text() == "SAM-7: listening"


async def test_messages_sent_mid_turn_are_counted_and_answered_in_order():
    sent = [chat("first"), chat("second"), chat("third")]

    class SlowBrain(FakeBrain):
        async def ask(self, text, progress=None):
            await asyncio.sleep(1.3)  # long enough for the status ticker to draw once
            return await super().ask(text, progress)

    h = handlers()
    h["events.poll"] = lambda p: {"events": [sent.pop(0)] if sent else []}
    brain = SlowBrain("ok")
    async with FakeGame(h) as game:
        runner = make_runner(game, brain)
        task = asyncio.create_task(runner.run())
        while len(brain.asked) < 3:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert [a.rsplit("\n", 1)[1] for a in brain.asked] == ["first", "second", "third"]
    assert any("waiting)" in text for text, _ in statuses(game))
    assert ("overlay.remove", {"ids": [StatusHud.ICON, StatusHud.TEXT]}) in game.calls


async def test_comms_messages_are_answered_in_the_comms_tab():
    brain = FakeBrain("On it.")
    async with FakeGame(handlers()) as game:
        await make_runner(game, brain).handle(chat("open the hangar", source="comms", persona="ship", ship="Red Ship", shipId="7"))

    assert "seated in the ship 'Red Ship' (id 7). Sent from the Comms tab" in brain.asked[0]
    assert comms(game) == [("Ship AI", "On it.")]
    assert chats(game) == []


async def test_a_name_in_the_soul_is_used_in_chat():
    async with FakeGame(handlers()) as game:
        await make_runner(game, FakeBrain("Aye."), soul="Name: Jarvis\n").handle(chat("hi"))
    assert chats(game) == [("Jarvis", "Aye.")]


async def test_reset_starts_a_fresh_conversation_without_asking():
    brain = FakeBrain()
    async with FakeGame(handlers()) as game:
        await make_runner(game, brain).handle(chat("Reset"))

    assert brain.asked == []
    assert brain.resets == 1
    assert chats(game) == [("Suit AI", "Fresh start. What do you need?")]


async def test_failure_is_told_to_the_player():
    brain = FakeBrain(fail=RuntimeError("Claude stopped (not logged in)"))
    async with FakeGame(handlers()) as game:
        await make_runner(game, brain).handle(chat("hello"))

    assert chats(game) == [("Suit AI", "I couldn't answer that: Claude stopped (not logged in)")]


async def test_watch_event_is_passed_on_with_its_note():
    brain = FakeBrain("Oxygen's at 12%, top up soon.")
    async with FakeGame(handlers()) as game:
        await make_runner(game, brain).handle(watch_event())

    assert "[Event] Suit oxygen low: oxygen is 12 (< 20)" in brain.asked[0]
    assert "Note from when the watch was set: Warn the player." in brain.asked[0]
    assert chats(game) == [("Suit AI", "Oxygen's at 12%, top up soon.")]
    assert not any(p.get("kind") == "hud" for m, p in game.calls if m == "notify")


async def test_the_ai_can_stay_quiet_about_an_event():
    async with FakeGame(handlers()) as game:
        await make_runner(game, FakeBrain(SILENT + ".")).handle(watch_event())
    assert chats(game) == []


async def test_poll_returns_queued_events():
    h = handlers()
    h["events.poll"] = lambda p: {"events": [chat("hi")] if p["waitMs"] > 0 else []}
    async with FakeGame(h) as game:
        assert [e["text"] for e in await make_runner(game, FakeBrain()).poll_once()] == ["hi"]


def test_idle_conversation_expires(tmp_path):
    now = [10_000.0]
    conversation = Conversation(tmp_path / "c.json", session_id="old", last_at=0, idle_reset_seconds=3600,
                                clock=lambda: now[0])
    assert conversation.current() is None
    conversation.update("new")
    now[0] += 60
    assert conversation.current() == "new"
    assert Conversation(tmp_path / "c.json").load().session_id == "new"


def test_context_lines():
    assert "on foot in their suit. Sent from the chat" in context_line(chat("x"))
    assert "Event at" in context_line(watch_event())
    assert "in their suit (creative mode). Sent" in context_line(chat("x", creative=True))
    assert "Note from" not in event_message(watch_event(note=None))


def test_long_replies_split_between_words():
    lines = chat_lines("word " * 100, width=50)
    assert all(len(line) <= 50 for line in lines)
    assert " ".join(lines).split() == ["word"] * 100
    assert chat_lines("one\n\ntwo") == ["one", "two"]
    assert chat_lines("") == ["(no answer)"]
