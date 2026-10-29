"""Programmable block helpers: code backups, waiting for the compile result, and output formatting."""

from __future__ import annotations

import asyncio
import os
import re
import time
from importlib import resources
from pathlib import Path
from typing import Any

from .bridge import Bridge


def primer() -> str:
    return resources.files(__package__).joinpath("primer.md").read_text(encoding="utf-8")


def backup_dir() -> Path:
    override = os.environ.get("SE_MCP_PB_BACKUPS")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", Path.home())) / "SpaceEngineers" / "SEMcp" / "pb-backups"


def backup(state: dict[str, Any], code: str) -> Path | None:
    """Save a PB's current code before it's replaced, so nothing the player wrote is ever lost."""
    if not code.strip():
        return None
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\- ]+", "_", state.get("name") or "PB").strip() or "PB"
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')} {safe_name} {state['id']}.cs"
    path.write_text(code, encoding="utf-8")
    return path


async def wait_for_output(bridge: Bridge, block_id: str, timeout_s: float = 3.0, poll_s: float = 0.25) -> dict[str, Any]:
    """On a multiplayer client the server compiles/runs and the output arrives a little later; in single player
    it's already there. Poll briefly until the PB has something to say."""
    state = await bridge.call("pb.get", {"blockId": block_id, "includeCode": False})
    deadline = time.monotonic() + timeout_s
    while not state.get("isServer") and not (state.get("output") or "").strip() and time.monotonic() < deadline:
        await asyncio.sleep(poll_s)
        state = await bridge.call("pb.get", {"blockId": block_id, "includeCode": False})
    return state


def output_text(state: dict[str, Any]) -> str:
    """Compiler messages (errors and warnings from the last compile), then the PB's terminal output."""
    parts = []
    messages = state.get("compilerMessages") or []
    if messages:
        parts.append("Compiler:\n" + "\n".join(f"  {m}" for m in messages))
    out = (state.get("output") or "").strip()
    if out:
        parts.append(out.replace("\r\n", "\n"))
    return "\n".join(parts) if parts else "(no output)"


def status_line(state: dict[str, Any]) -> str:
    name = state.get("name") or state["id"]
    if not state.get("scriptsEnabled", True):
        return f"{name}: in-game scripts are disabled in this world's settings."
    if not state.get("working"):
        return f"{name}: the block is off, unpowered or damaged, so it can't run."
    if state.get("hasCompileErrors"):
        return f"{name}: compile errors."
    if state.get("hasCompileErrors") is False:
        return f"{name}: compiled OK."
    return f"{name}:"
