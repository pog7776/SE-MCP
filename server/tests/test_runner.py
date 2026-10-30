from __future__ import annotations

from se_mcp.bridge import Bridge
from se_mcp.runner import Conversation, Runner, Turn, chat_lines, persona_prompt

from .fake_bridge import TOKEN, FakeGame, default_handlers


def message(text: str, **over) -> dict:
    return {"id": 1, "text": text, "player": "dingus", "persona": "suit", "ship": None, "shipId": None, **over}


class FakeAsk:
    def __init__(self, reply: str = "Hydrogen is at 80%.", fail: Exception | None = None):
        self.reply, self.fail = reply, fail
        self.calls: list[tuple[str, str, str | None]] = []

    async def __call__(self, text: str, system: str, session_id: str | None) -> Turn:
        self.calls.append((text, system, session_id))
        if self.fail:
            raise self.fail
        return Turn(self.reply, "session-2")


def chats(game: FakeGame) -> list[tuple[str, str]]:
    return [(p["sender"], p["text"]) for m, p in game.calls if m == "notify" and p.get("kind") == "chat"]


async def test_message_gets_an_answer_and_keeps_the_conversation(tmp_path):
    conversation = Conversation(tmp_path / "c.json", session_id="session-1", last_at=100, clock=lambda: 200)
    ask = FakeAsk()
    async with FakeGame() as game:
        runner = Runner(Bridge(port=game.port, token=TOKEN), ask, conversation, log=lambda _: None)
        await runner.handle(message("how much hydrogen?"))

    assert ask.calls[0][0] == "how much hydrogen?"
    assert ask.calls[0][2] == "session-1"
    assert chats(game) == [("Suit AI", "Hydrogen is at 80%.")]
    assert ("notify", {"text": "Suit AI is thinking...", "kind": "hud", "durationMs": 3000}) in game.calls
    assert Conversation(tmp_path / "c.json").load().session_id == "session-2"


async def test_reset_starts_a_fresh_conversation_without_asking(tmp_path):
    conversation = Conversation(tmp_path / "c.json", session_id="session-1")
    ask = FakeAsk()
    async with FakeGame() as game:
        runner = Runner(Bridge(port=game.port, token=TOKEN), ask, conversation, log=lambda _: None)
        await runner.handle(message("Reset"))

    assert ask.calls == []
    assert conversation.session_id is None
    assert chats(game) == [("Suit AI", "Fresh start. What do you need?")]


async def test_failure_is_told_to_the_player(tmp_path):
    ask = FakeAsk(fail=RuntimeError("claude exited with code 1: not logged in"))
    async with FakeGame() as game:
        runner = Runner(Bridge(port=game.port, token=TOKEN), ask, Conversation(tmp_path / "c.json"), log=lambda _: None)
        await runner.handle(message("hello", persona="ship", ship="Red Ship", shipId="7"))

    assert chats(game) == [("Ship AI", "I couldn't answer that: claude exited with code 1: not logged in")]


async def test_poll_returns_queued_messages(tmp_path):
    handlers = default_handlers()
    handlers["chat.poll"] = lambda p: {"prefix": "/ai", "messages": [message("hi")] if p["waitMs"] > 0 else []}
    async with FakeGame(handlers) as game:
        runner = Runner(Bridge(port=game.port, token=TOKEN), FakeAsk(), Conversation(tmp_path / "c.json"), log=lambda _: None)
        assert [m["text"] for m in await runner.poll_once()] == ["hi"]


def test_idle_conversation_expires(tmp_path):
    now = [10_000.0]
    conversation = Conversation(tmp_path / "c.json", session_id="old", last_at=0, idle_reset_seconds=3600,
                                clock=lambda: now[0])
    assert conversation.current() is None
    conversation.update("new")
    now[0] += 60
    assert conversation.current() == "new"


def test_persona_names_the_ship():
    prompt = persona_prompt(message("x", persona="ship", ship="Red Fighter", shipId="136"))
    assert prompt.startswith("You are the Ship AI of Space Engineers player dingus")
    assert "seated in the ship 'Red Fighter' (id 136)" in prompt
    assert "on foot in their suit" in persona_prompt(message("x"))


def test_long_replies_split_between_words():
    lines = chat_lines("word " * 100, width=50)
    assert all(len(line) <= 50 for line in lines)
    assert " ".join(lines).split() == ["word"] * 100
    assert chat_lines("one\n\ntwo") == ["one", "two"]
    assert chat_lines("") == ["(no answer)"]
