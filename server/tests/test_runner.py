from __future__ import annotations

from se_mcp.bridge import Bridge
from se_mcp.runner import SILENT, Conversation, Runner, Turn, chat_lines, context_line, event_message

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

    async def ask(self, text: str) -> Turn:
        self.asked.append(text)
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
    return h


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
    assert ("notify", {"text": "Suit AI is thinking...", "kind": "hud", "durationMs": 3000}) in game.calls


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
