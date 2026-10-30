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

# Souls that ship with the plugin; "default" is what a new SOUL.md starts as.
PRESETS_DIR = Path(__file__).parent / "souls"


def presets() -> dict[str, str]:
    """Preset name -> its one-line description (the first '> ' line of the file)."""
    found = {}
    for path in sorted(PRESETS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^>\s*(.+)$", text, re.MULTILINE)
        found[path.stem] = match.group(1).strip() if match else ""
    return found


def preset_text(name: str) -> str | None:
    wanted = name.strip().lower().removesuffix(".md")
    path = next((p for p in PRESETS_DIR.glob("*.md") if p.stem.lower() == wanted), None)
    return path.read_text(encoding="utf-8") if path else None


DEFAULT_SOUL = preset_text("default") or "# Soul\n\nName: Suit AI\n"


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


def _last_section(soul: str) -> tuple[int, list[str]]:
    """Where the last '## ' section starts, and its real bullet points (not the '(...)' placeholders)."""
    starts = [m.start() for m in re.finditer(r"^## ", soul, re.MULTILINE)]
    if not starts:
        return len(soul), []
    body = soul[starts[-1]:].splitlines()[1:]
    notes = [line for line in body if line.strip().startswith("-") and not line.strip("- ").startswith("(")]
    return starts[-1], notes


def carry_notes(old: str, new: str) -> str:
    """The new soul with the old one's notes about the player (its last section's bullets) in its own last section."""
    _, notes = _last_section(old)
    if not notes:
        return new
    start, _ = _last_section(new)
    heading, *body = new[start:].splitlines()
    kept = [line for line in body if not line.strip("- ").startswith("(")]
    return new[:start] + "\n".join([heading, *kept, *notes]).rstrip() + "\n"


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
