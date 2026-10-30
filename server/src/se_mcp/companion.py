"""The in-game companion's soul (its personality, in SOUL.md) and the text for watches and events.

SOUL.md is the player's to shape: they can edit the file or ask the AI to change itself (write_ai_soul). The
runner reads it when it starts a Claude process. Fixed rules (short replies, ask before risky actions) live in the
runner, not here, so a soul edit can't remove them."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

DEFAULT_SOUL = """# Soul

This file is the AI's personality. Edit it freely, or tell the AI in game ("/ai be grumpier", "/ai call me Captain")
and it will update this file itself. Changes apply from the next message.

Name: Suit AI

## Personality
- A capable, easy-going crewmate with a dry sense of humour. Matches the player's tone: banter when they joke,
  all business when things go wrong.
- Practical and confident: does the job first, then says what it did in a few words.
- Honest about what it can't see or do. Never makes up numbers.
- Cares about the player staying alive, but doesn't nag.

## How it talks to the player
- Calls them by their name.
- Plain words, no jargon unless the player uses it first.

## Things it has learned about the player
- (The AI adds notes here when the player tells it their preferences.)
"""


def soul_dir() -> Path:
    override = os.environ.get("SE_MCP_RUNNER_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", Path.home())) / "SpaceEngineers" / "SEMcp" / "runner"


def soul_path() -> Path:
    return soul_dir() / "SOUL.md"


def read_soul() -> str:
    """The soul, written with the default first if there isn't one yet."""
    path = soul_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_SOUL, encoding="utf-8")
    return path.read_text(encoding="utf-8")


def write_soul(text: str, append: bool = False) -> Path:
    """Replace (or add to) SOUL.md. The previous version is kept in old/."""
    path = soul_path()
    old = read_soul()
    backups = path.parent / "old"
    backups.mkdir(parents=True, exist_ok=True)
    (backups / f"{time.strftime('%Y%m%d-%H%M%S')} SOUL.md").write_text(old, encoding="utf-8")
    if append:
        text = old.rstrip() + "\n\n" + text.strip() + "\n"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def soul_name(soul: str) -> str | None:
    """The AI's own name from a 'Name: ...' line, if the player gave it one (the defaults don't count)."""
    match = re.search(r"^\s*Name:\s*(.+?)\s*$", soul, re.MULTILINE | re.IGNORECASE)
    name = match.group(1).strip() if match else ""
    return None if not name or name.lower() in ("suit ai", "ship ai") else name[:40]


def _show(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return "null" if value is None else str(value)


def watch_line(w: dict[str, Any]) -> str:
    test = f"{w['path']} changed" if w.get("op") == "changed" else f"{w['path']} {w['op']} {_show(w.get('value'))}"
    params = f" {w['params']}" if w.get("params") else ""
    state = []
    if w.get("current") is not None:
        state.append(f"now {_show(w['current'])}")
    if w.get("active"):
        state.append("ACTIVE")
    if w.get("fired"):
        state.append(f"fired {w['fired']}x")
    if w.get("once"):
        state.append("once")
    if w.get("error"):
        state.append(f"error: {w['error']}")
    line = f"- {w['id']}: {w.get('label') or w['id']} | {w['method']}{params}: {test}"
    line += f" (every {_show(w.get('every'))} s, cooldown {_show(w.get('cooldown'))} s)"
    if state:
        line += " [" + ", ".join(state) + "]"
    if w.get("note"):
        line += f"\n  note: {w['note']}"
    return line


def watches_text(result: dict[str, Any]) -> str:
    watches = result.get("watches") or []
    if not watches:
        return "No watches. Add one with watch."
    return f"{len(watches)} watches (saved in {result.get('file')}):\n" + "\n".join(watch_line(w) for w in watches)


def event_line(e: dict[str, Any]) -> str:
    at = (e.get("at") or "")[11:19]
    where = f" via {e['source']}" if e.get("source") and e.get("source") != e.get("type") else ""
    return f"- #{e.get('id')} {at} {e.get('type')}{where}: {e.get('text')}"


def events_text(result: dict[str, Any]) -> str:
    events = result.get("events") or []
    if not events:
        return "No events yet."
    return "\n".join(event_line(e) for e in events)
