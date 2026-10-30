"""In-game chat runner: answers '/ai ...' chat lines by starting a Claude turn with this MCP server's tools.

The plugin keeps '/ai' lines private and queues them; this process long-polls `chat.poll`, runs `claude -p` for
each one (resuming one conversation so follow-ups make sense) and posts the reply back as a local chat line.
Start it with `uv run se-mcp-runner` in SE-MCP/server while the game is running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .bridge import Bridge, BridgeError

POLL_WAIT_MS = 25000
RETRY_SECONDS = 5
# Replies longer than this are cut into several chat lines so none runs off the chat box.
CHAT_LINE_CHARS = 300
RESET_WORDS = {"reset", "new", "forget"}

PERSONA = """You are the {ai} of Space Engineers player {player}, talking to them through the in-game chat.
{where}
They read your reply in a small chat box while playing, so:
- Answer in one to three short sentences of plain text. No markdown, lists, headings or code blocks.
- Do what they ask with the space-engineers tools, then say briefly what you did or found.
- If something is risky (weapons, moving a ship near structures, deleting or dumping items), say what you would do and
  ask first, unless they clearly told you to go ahead.
- Don't narrate tool calls. Don't send in-game notifications for your answer: it is posted to chat for you."""


@dataclass
class Turn:
    reply: str
    session_id: str | None = None


Ask = Callable[[str, str, "str | None"], Awaitable[Turn]]


def runner_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "SpaceEngineers" / "SEMcp" / "runner"


def persona_prompt(message: dict[str, Any]) -> str:
    ai = ai_name(message)
    if message.get("persona") == "ship" and message.get("ship"):
        where = f"They are seated in the ship '{message['ship']}' (id {message.get('shipId')}); that is the ship you run."
    else:
        where = "They are on foot in their suit; you live in the suit."
    return PERSONA.format(ai=ai, player=message.get("player") or "the player", where=where)


def ai_name(message: dict[str, Any]) -> str:
    return "Ship AI" if message.get("persona") == "ship" else "Suit AI"


def chat_lines(text: str, width: int = CHAT_LINE_CHARS) -> list[str]:
    """Splits a reply into chat-sized lines, on paragraph breaks first, then between words."""
    lines: list[str] = []
    for paragraph in (p.strip() for p in text.replace("\r", "").split("\n")):
        while len(paragraph) > width:
            cut = paragraph.rfind(" ", 0, width)
            cut = cut if cut > width // 2 else width
            lines.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip()
        if paragraph:
            lines.append(paragraph)
    return lines or ["(no answer)"]


@dataclass
class Conversation:
    """The Claude session the chat continues, kept on disk so a runner restart picks it up."""

    path: Path
    session_id: str | None = None
    last_at: float = 0.0
    # A long gap starts a fresh conversation instead of dragging in an old one.
    idle_reset_seconds: float = 3 * 3600
    clock: Callable[[], float] = field(default=time.time)

    def load(self) -> Conversation:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.session_id, self.last_at = data.get("sessionId"), float(data.get("lastAt", 0))
        except (OSError, ValueError):
            pass
        return self

    def current(self) -> str | None:
        if self.session_id and self.clock() - self.last_at > self.idle_reset_seconds:
            self.session_id = None
        return self.session_id

    def update(self, session_id: str | None) -> None:
        self.session_id, self.last_at = session_id, self.clock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"sessionId": session_id, "lastAt": self.last_at}), encoding="utf-8")
        except OSError:
            pass


class Runner:
    def __init__(self, bridge: Bridge, ask: Ask, conversation: Conversation, log: Callable[[str], None] = print):
        self.bridge = bridge
        self.ask = ask
        self.conversation = conversation
        self.log = log

    async def say(self, ai: str, text: str) -> None:
        for line in chat_lines(text):
            await self.bridge.call("notify", {"text": line, "kind": "chat", "sender": ai})

    async def handle(self, message: dict[str, Any]) -> None:
        ai = ai_name(message)
        text = (message.get("text") or "").strip()
        self.log(f"> {message.get('player')}: {text}")
        if text.lower() in RESET_WORDS:
            self.conversation.update(None)
            await self.say(ai, "Fresh start. What do you need?")
            return

        await self.bridge.call("notify", {"text": f"{ai} is thinking...", "kind": "hud", "durationMs": 3000})
        try:
            turn = await self.ask(text, persona_prompt(message), self.conversation.current())
        except Exception as e:  # the player should hear about any failure, not just a silent chat
            self.log(f"! {e}")
            await self.say(ai, f"I couldn't answer that: {e}")
            return
        if turn.session_id:
            self.conversation.update(turn.session_id)
        self.log(f"< {ai}: {turn.reply}")
        await self.say(ai, turn.reply)

    async def poll_once(self) -> list[dict[str, Any]]:
        result = await self.bridge.call("chat.poll", {"waitMs": POLL_WAIT_MS}, timeout=POLL_WAIT_MS / 1000 + 10)
        return list((result or {}).get("messages") or [])

    async def run(self) -> None:
        waiting = False
        while True:
            try:
                messages = await self.poll_once()
                if waiting:
                    self.log("Connected to the game; listening for chat.")
                waiting = False
            except BridgeError as e:
                if e.kind == "timeout":
                    continue
                if not waiting:
                    self.log(f"Waiting for the game: {e}")
                waiting = True
                await asyncio.sleep(RETRY_SECONDS)
                continue
            for message in messages:
                try:
                    await self.handle(message)
                except BridgeError as e:
                    self.log(f"! Couldn't reply in game: {e}")


def mcp_config(directory: Path) -> Path:
    """An MCP config that starts this same server with this same Python, so the runner needs no other setup."""
    path = directory / "mcp.json"
    config = {"mcpServers": {"space-engineers": {"command": sys.executable, "args": ["-m", "se_mcp"]}}}
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def claude_ask(claude: str, directory: Path, model: str | None = None, timeout: float = 600) -> Ask:
    """Runs one headless Claude Code turn per message (under the user's Claude plan) with only the game's tools."""
    config = mcp_config(directory)

    async def ask(text: str, system: str, session_id: str | None) -> Turn:
        args = [claude, "-p", "--output-format", "json", "--mcp-config", str(config), "--strict-mcp-config",
                "--tools", "", "--allowedTools", "mcp__space-engineers", "--append-system-prompt", system]
        if model:
            args += ["--model", model]
        if session_id:
            args += ["--resume", session_id]
        # Headless turns otherwise start before the MCP server connects, with no game tools at all.
        env = {**os.environ, "MCP_CONNECTION_NONBLOCKING": "false"}
        process = await asyncio.create_subprocess_exec(
            *args, cwd=directory, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(text.encode("utf-8")), timeout)
        except asyncio.TimeoutError:
            process.kill()
            raise RuntimeError(f"it took longer than {timeout:g}s") from None
        try:
            result = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = (err or out).decode("utf-8", "replace").strip()[-300:]
            raise RuntimeError(f"claude exited with code {process.returncode}: {detail or 'no output'}") from None
        if result.get("is_error"):
            raise RuntimeError(str(result.get("result") or result.get("subtype") or "unknown error"))
        return Turn(str(result.get("result") or "").strip(), result.get("session_id"))

    return ask


def main() -> None:
    parser = argparse.ArgumentParser(prog="se-mcp-runner", description=__doc__.splitlines()[0])
    parser.add_argument("--claude", default=shutil.which("claude"), help="path to the claude CLI")
    parser.add_argument("--model", help="model for the chat turns (default: Claude Code's default)")
    parser.add_argument("--timeout", type=float, default=600, help="seconds one answer may take")
    args = parser.parse_args()
    if not args.claude:
        sys.exit("Couldn't find the claude CLI on PATH; pass --claude <path>.")

    directory = runner_dir()
    conversation = Conversation(directory / "conversation.json").load()
    runner = Runner(Bridge(), claude_ask(args.claude, directory, args.model, args.timeout), conversation,
                    log=lambda line: print(line, flush=True))
    print(f"SE-MCP chat runner: answering '/ai' chat with {args.claude}. Ctrl+C to stop.", flush=True)
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
