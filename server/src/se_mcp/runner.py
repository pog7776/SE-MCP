"""In-game companion runner: answers '/ai ...' chat, the Comms tab's Suit AI, and watch events with Claude.

The plugin turns those into events; this process long-polls `events.poll` and feeds each one to a single
long-lived headless Claude Code process (stream-json over stdin/stdout) that has this MCP server's tools. Replies
go back to wherever the player asked: a local chat line, or the Comms tab conversation.
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
from typing import Any, Callable, Protocol

from . import companion
from .bridge import Bridge, BridgeError

POLL_WAIT_MS = 25000
RETRY_SECONDS = 5
# Replies longer than this are cut into several chat lines so none runs off the chat box.
CHAT_LINE_CHARS = 300
RESET_WORDS = {"reset", "new", "forget"}
# What the AI answers when an event isn't worth bothering the player about.
SILENT = "SILENT"

# Fixed rules, kept out of SOUL.md so a personality edit can't remove them.
RULES = f"""You are the AI companion of a Space Engineers player, living in their suit (and in the ship when they sit
in one). You talk to them through the in-game chat or the Comms tab of the terminal.

Every message starts with a [Context: ...] line saying where the player is and where the message came from.

How to reply:
- They read your reply in a small chat box while playing: one to three short sentences of plain text. No markdown,
  lists, headings or code blocks.
- Do what they ask with the space-engineers tools, then say briefly what you did or found. Don't narrate tool calls.
- If something is risky (weapons, moving a ship near structures, dumping or deleting items), say what you would do
  and ask first, unless they clearly told you to go ahead.
- Your reply is posted for you. Don't use the say tool to answer.
- Your personality is in your soul (below). When the player asks you to change how you act, your name or how you
  address them, or tells you a lasting preference, update it with ai_soul and write_ai_soul. Packaged personalities
  are listed by ai_soul_presets and switched with use_ai_soul. If your soul says to stay in character, do so in
  every reply, warnings included; these rules are about how you work, not who you are.
- To keep an eye on something for them ("tell me when hydrogen is low"), add a watch with the watch tool.

[Event] messages are not from the player: a watch fired. Decide whether it's worth telling them. If it is, write the
message for them. If not (it's harmless, or you already told them), answer exactly {SILENT}. In creative mode the
player can't suffocate, starve or run out of fuel, so stay {SILENT} about suit oxygen, energy and hydrogen there."""


@dataclass
class Turn:
    reply: str
    session_id: str | None = None


Progress = Callable[[str], None]


class Brain(Protocol):
    async def ask(self, text: str, progress: Progress | None = None) -> Turn: ...
    async def reset(self) -> None: ...


class StatusHud:
    """A small icon and line of text on the player's HUD saying what the AI is doing: listening, thinking, which
    tool it's using, and how many messages wait. Drawn with the plugin's overlay; refreshed with a ttl, so it
    disappears on its own if the runner stops."""

    ICON, TEXT = "ai-status-icon", "ai-status-text"
    COLORS = {"idle": "#8FA3B0B0", "thinking": "#4FC3F7", "tool": "#FFB74D", "error": "#EF5350"}
    TTL = 75

    # Top-left: clear of the chat box (bottom-left) and the toolbar and ship panels.
    def __init__(self, bridge: Bridge, x: float = 0.012, y: float = 0.035, clock: Callable[[], float] = time.monotonic):
        self.bridge, self.x, self.y, self.clock = bridge, x, y, clock
        self.name = "Suit AI"
        self.state, self.tool = "idle", None
        self.started = 0.0
        self.waiting = 0
        self.error_until = 0.0

    def text(self) -> str:
        if self.clock() < self.error_until:
            line = f"{self.name}: couldn't answer"
        elif self.state == "idle":
            line = f"{self.name}: listening"
        else:
            seconds = int(self.clock() - self.started)
            doing = self.tool.replace("_", " ") if self.state == "tool" and self.tool else "thinking"
            line = f"{self.name}: {doing}... {seconds}s"
        if self.waiting:
            line += f" (+{self.waiting} waiting)"
        return line

    def color(self) -> str:
        return self.COLORS["error" if self.clock() < self.error_until else self.state]

    async def draw(self) -> None:
        color = self.color()
        elements = [
            {"id": self.ICON, "kind": "icon", "icon": "chat", "x": self.x, "y": self.y, "w": 0.02, "color": color,
             "ttl": self.TTL},
            {"id": self.TEXT, "kind": "text", "text": self.text(), "x": self.x + 0.016, "y": self.y - 0.009,
             "scale": 0.55, "color": color, "background": "#00000060", "ttl": self.TTL},
        ]
        try:
            await self.bridge.call("overlay.set", {"elements": elements})
        except BridgeError:
            pass  # no world, or the game is gone: the status is cosmetic

    async def busy(self, name: str) -> None:
        self.name, self.state, self.tool, self.started = name, "thinking", None, self.clock()
        await self.draw()

    def using(self, tool: str) -> None:
        self.state, self.tool = "tool", tool

    async def idle(self, failed: bool = False) -> None:
        self.state, self.tool = "idle", None
        if failed:
            self.error_until = self.clock() + 10
        await self.draw()

    async def clear(self) -> None:
        try:
            await self.bridge.call("overlay.remove", {"ids": [self.ICON, self.TEXT]})
        except BridgeError:
            pass


def runner_dir() -> Path:
    return companion.soul_dir()


def ai_name(event: dict[str, Any], soul: str | None = None) -> str:
    named = companion.soul_name(soul) if soul else None
    if named:
        return named
    return "Ship AI" if (event.get("context") or {}).get("persona") == "ship" else "Suit AI"


def context_line(event: dict[str, Any]) -> str:
    c = event.get("context") or {}
    player = c.get("player") or "The player"
    if c.get("persona") == "ship" and c.get("ship"):
        where = f"{player} is seated in the ship '{c['ship']}' (id {c.get('shipId')})"
    else:
        where = f"{player} is on foot in their suit"
    if c.get("creative"):
        where += " (creative mode)"
    source = {"chat": "the chat", "comms": "the Comms tab"}.get(event.get("source") or "", event.get("source") or "the game")
    when = time.strftime("%H:%M")
    if event.get("type") == "chat":
        return f"[Context: {where}. Sent from {source} at {when}.]"
    return f"[Context: {where}. Event at {when}.]"


def event_message(event: dict[str, Any]) -> str:
    data = event.get("data") or {}
    lines = [context_line(event), f"[Event] {event.get('text')}"]
    if data.get("note"):
        lines.append(f"Note from when the watch was set: {data['note']}")
    lines.append(f"(watch '{data.get('watch')}'; remove_watch or watch can change it.)")
    return "\n".join(lines)


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
    def __init__(self, bridge: Bridge, brain: Brain, log: Callable[[str], None] = print,
                 soul: Callable[[], str] = companion.read_soul, status: StatusHud | None = None):
        self.bridge = bridge
        self.brain = brain
        self.log = log
        self.soul = soul
        self.status = status or StatusHud(bridge)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def think(self, event: dict[str, Any], text: str) -> Turn:
        """Ask the brain while the HUD shows it working: elapsed seconds, the tool in use, messages waiting."""
        await self.status.busy(ai_name(event, self.soul()))

        async def tick() -> None:
            while True:
                await asyncio.sleep(1)
                self.status.waiting = self.queue.qsize()
                await self.status.draw()

        ticker = asyncio.create_task(tick())
        failed = False
        try:
            return await self.brain.ask(text, progress=self.status.using)
        except Exception:
            failed = True
            raise
        finally:
            ticker.cancel()
            self.status.waiting = self.queue.qsize()
            await self.status.idle(failed)

    async def reply(self, event: dict[str, Any], text: str) -> None:
        ai = ai_name(event, self.soul())
        if event.get("source") == "comms":
            await self.bridge.call("comms.post", {"text": text, "sender": ai})
            return
        for line in chat_lines(text):
            await self.bridge.call("notify", {"text": line, "kind": "chat", "sender": ai})

    async def handle(self, event: dict[str, Any]) -> None:
        if event.get("type") == "chat":
            await self.handle_chat(event)
        else:
            await self.handle_event(event)

    async def handle_chat(self, event: dict[str, Any]) -> None:
        text = (event.get("text") or "").strip()
        self.log(f"> {(event.get('context') or {}).get('player')} ({event.get('source')}): {text}")
        if text.lower() in RESET_WORDS:
            await self.brain.reset()
            await self.reply(event, "Fresh start. What do you need?")
            return
        try:
            turn = await self.think(event, f"{context_line(event)}\n{text}")
        except Exception as e:  # the player should hear about any failure, not just a silent chat
            self.log(f"! {e}")
            await self.reply(event, f"I couldn't answer that: {e}")
            return
        self.log(f"< {turn.reply}")
        await self.reply(event, turn.reply)

    async def handle_event(self, event: dict[str, Any]) -> None:
        self.log(f"* {event.get('type')}: {event.get('text')}")
        try:
            turn = await self.think(event, event_message(event))
        except Exception as e:
            self.log(f"! {e}")
            return
        reply = turn.reply.strip()
        if not reply or reply.strip(" .").upper() == SILENT:
            self.log("< (stayed quiet)")
            return
        self.log(f"< {reply}")
        await self.reply(event, reply)

    async def poll_once(self) -> list[dict[str, Any]]:
        result = await self.bridge.call("events.poll", {"waitMs": POLL_WAIT_MS}, timeout=POLL_WAIT_MS / 1000 + 10)
        return list((result or {}).get("events") or [])

    async def poll_forever(self) -> None:
        """Keeps collecting events while a turn runs, so the HUD can count what's waiting."""
        waiting = False
        while True:
            try:
                events = await self.poll_once()
                if waiting:
                    self.log("Connected to the game; listening.")
                    await self.status.draw()
                waiting = False
            except BridgeError as e:
                if e.kind == "timeout":
                    continue
                if not waiting:
                    self.log(f"Waiting for the game: {e}")
                waiting = True
                await asyncio.sleep(RETRY_SECONDS)
                continue
            for event in events:
                self.queue.put_nowait(event)
            if events and self.status.state != "idle":
                self.status.waiting = self.queue.qsize()
                await self.status.draw()

    async def keep_status(self) -> None:
        """Redraws the idle status now and then, so its ttl doesn't run out while the runner is alive."""
        while True:
            await asyncio.sleep(StatusHud.TTL / 3)
            if self.status.state == "idle":
                await self.status.draw()

    async def run(self) -> None:
        tasks = [asyncio.create_task(self.poll_forever()), asyncio.create_task(self.keep_status())]
        await self.status.draw()
        try:
            while True:
                event = await self.queue.get()
                self.status.waiting = self.queue.qsize()
                try:
                    await self.handle(event)
                except BridgeError as e:
                    self.log(f"! Couldn't reply in game: {e}")
        finally:
            for task in tasks:
                task.cancel()
            await self.status.clear()


def code_stamp() -> float:
    """Newest change to this package's files. The tools load fresh in each Claude process (it starts its own MCP
    server), so a restart picks up new ones; this runner's own code (RULES) still needs the runner restarted."""
    package = Path(__file__).parent
    return max((p.stat().st_mtime for p in package.rglob("*") if p.suffix in (".py", ".md")), default=0.0)


def mcp_config(directory: Path) -> Path:
    """An MCP config that starts this same server with this same Python, so the runner needs no other setup."""
    path = directory / "mcp.json"
    config = {"mcpServers": {"space-engineers": {"command": sys.executable, "args": ["-m", "se_mcp"]}}}
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


class ClaudeBrain:
    """One headless Claude Code process kept open between messages (stream-json in and out), on the user's plan,
    with only the game's tools. It's restarted, resuming the same conversation, when it dies, hangs or the soul
    changes."""

    def __init__(self, claude: str, directory: Path, conversation: Conversation, model: str | None = None,
                 timeout: float = 600, log: Callable[[str], None] = print):
        self.claude = claude
        self.directory = directory
        self.conversation = conversation
        self.model = model
        self.timeout = timeout
        self.log = log
        self.config = mcp_config(directory)
        self.process: asyncio.subprocess.Process | None = None
        self.soul_stamp: float | None = None
        self.code_stamp: float | None = None
        self.lock = asyncio.Lock()

    def _soul_changed(self) -> bool:
        try:
            return companion.soul_path().stat().st_mtime != self.soul_stamp
        except OSError:
            return False

    def _code_changed(self) -> bool:
        """The MCP server code (tools, soul presets) changed since this Claude process started it."""
        return code_stamp() != self.code_stamp

    async def _start(self) -> asyncio.subprocess.Process:
        soul = companion.read_soul()
        self.soul_stamp = companion.soul_path().stat().st_mtime
        self.code_stamp = code_stamp()
        system = f"{RULES}\n\n# Your soul (SOUL.md)\n\n{soul}"
        args = [self.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--mcp-config", str(self.config), "--strict-mcp-config", "--tools", "",
                "--allowedTools", "mcp__space-engineers", "--append-system-prompt", system]
        if self.model:
            args += ["--model", self.model]
        session = self.conversation.current()
        if session:
            args += ["--resume", session]
        # Headless turns otherwise start before the MCP server connects, with no game tools at all.
        env = {**os.environ, "MCP_CONNECTION_NONBLOCKING": "false"}
        # A file rather than a pipe, so a chatty stderr nobody reads can't stall the process.
        with open(self.directory / "claude-stderr.log", "wb") as stderr:
            self.process = await asyncio.create_subprocess_exec(
                *args, cwd=self.directory, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=stderr, limit=16 * 1024 * 1024)
        self.log(f"(Claude started{', resuming the conversation' if session else ''})")
        return self.process

    async def _stop(self) -> None:
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                pass

    async def reset(self) -> None:
        async with self.lock:
            await self._stop()
            self.conversation.update(None)

    async def ask(self, text: str, progress: Progress | None = None) -> Turn:
        async with self.lock:
            if self.process is not None and (self.process.returncode is not None or self._soul_changed()
                                             or self._code_changed()):
                await self._stop()
            process = self.process or await self._start()
            assert process.stdin is not None and process.stdout is not None
            message = {"type": "user", "message": {"role": "user", "content": text}}
            try:
                process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                await process.stdin.drain()
                result = await asyncio.wait_for(self._read_result(process, progress), self.timeout)
            except asyncio.TimeoutError:
                await self._stop()
                raise RuntimeError(f"it took longer than {self.timeout:g}s") from None
            except (ConnectionError, OSError, EOFError) as e:
                await self._stop()
                detail = self._stderr()
                raise RuntimeError(f"Claude stopped ({detail or e})") from None
            if result.get("session_id"):
                self.conversation.update(result["session_id"])
            if result.get("is_error"):
                raise RuntimeError(str(result.get("result") or result.get("subtype") or "unknown error"))
            return Turn(str(result.get("result") or "").strip(), result.get("session_id"))

    @staticmethod
    async def _read_result(process: asyncio.subprocess.Process, progress: Progress | None = None) -> dict[str, Any]:
        """Reads the stream until the turn's result, telling `progress` the name of each tool as it's called."""
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                raise EOFError("Claude exited")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("type") == "result":
                return message
            if progress and message.get("type") == "assistant":
                for block in (message.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        progress(str(block.get("name", "")).split("__")[-1])

    def _stderr(self) -> str:
        try:
            return (self.directory / "claude-stderr.log").read_text(encoding="utf-8", errors="replace").strip()[-300:]
        except OSError:
            return ""


def main() -> None:
    parser = argparse.ArgumentParser(prog="se-mcp-runner", description=__doc__.splitlines()[0])
    parser.add_argument("--claude", default=shutil.which("claude"), help="path to the claude CLI")
    parser.add_argument("--model", help="model for the companion (default: Claude Code's default)")
    parser.add_argument("--timeout", type=float, default=600, help="seconds one answer may take")
    args = parser.parse_args()
    if not args.claude:
        sys.exit("Couldn't find the claude CLI on PATH; pass --claude <path>.")

    def log(line: str) -> None:
        print(line, flush=True)

    directory = runner_dir()
    companion.read_soul()
    conversation = Conversation(directory / "conversation.json").load()
    brain = ClaudeBrain(args.claude, directory, conversation, args.model, args.timeout, log)
    print(f"SE-MCP companion: answering '/ai' chat, the Comms tab and watches with {args.claude}.\n"
          f"Personality: {companion.soul_path()}. Ctrl+C to stop.", flush=True)
    try:
        asyncio.run(Runner(Bridge(), brain, log).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
