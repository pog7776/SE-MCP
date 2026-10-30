"""Ship notes: one markdown file per ship (its "AGENTS.md") with what an agent must know to operate it, such as
custom scripts that change how it flies, odd block layouts and habits. Kept outside the world so they survive
reloads and apply to every copy of a ship with that name."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path


def notes_dir() -> Path:
    override = os.environ.get("SE_MCP_SHIP_NOTES")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", Path.home())) / "SpaceEngineers" / "SEMcp" / "ships"


def _file_name(ship: str) -> str:
    return (re.sub(r"[^\w\- ]+", "_", ship).strip() or "Ship") + ".md"


def path_for(ship: str) -> Path | None:
    """The ship's notes file, matched by name ignoring case, or None."""
    folder = notes_dir()
    if not folder.is_dir():
        return None
    wanted = _file_name(ship).lower()
    return next((p for p in folder.glob("*.md") if p.name.lower() == wanted), None)


def read(ship: str) -> str | None:
    path = path_for(ship)
    return path.read_text(encoding="utf-8") if path else None


def write(ship: str, text: str, append: bool = False) -> Path:
    """Replace (or add to) the ship's notes. The previous version is kept in an old/ subfolder."""
    path = path_for(ship) or notes_dir() / _file_name(ship)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old.strip():
        backups = path.parent / "old"
        backups.mkdir(exist_ok=True)
        (backups / f"{time.strftime('%Y%m%d-%H%M%S')} {path.name}").write_text(old, encoding="utf-8")
    if append and old.strip():
        text = old.rstrip() + "\n\n" + text.strip() + "\n"
    elif not text.lstrip().startswith("#"):
        text = f"# {ship}\n\n{text.strip()}\n"
    path.write_text(text, encoding="utf-8")
    return path


class Reminders:
    """Point agents at a ship's notes until they've read them, so nobody flies a ship blind to its quirks."""

    def __init__(self) -> None:
        self.read: set[str] = set()

    def seen(self, ship: str) -> None:
        self.read.add(ship.lower())

    def line(self, ship: str | None) -> str | None:
        if not ship or ship.lower() in self.read or not path_for(ship):
            return None
        return (f"! {ship} has ship notes (its scripts and quirks, e.g. what changes how it flies). "
                f"Read them first: ship_notes('{ship}').")

    def prefix(self, ship: str | None, text: str) -> str:
        line = self.line(ship)
        return f"{line}\n{text}" if line else text

    def gate(self, ship: str | None) -> str | None:
        """Before a tool moves a ship: stop once if its notes are unread. Calling again goes ahead."""
        line = self.line(ship)
        if not line:
            return None
        self.seen(ship or "")
        return f"Not moving it yet. {line.lstrip('! ')} Then call this again (a second call goes ahead regardless)."
