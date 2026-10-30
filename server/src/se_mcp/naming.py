"""Bulk renaming: work out new terminal names for many blocks from one description of the change.

Operations apply in this order: strip a prefix, find/replace, a template, then add a prefix and suffix. Adding a
prefix or suffix that is already there does nothing, so running the same rename twice is harmless."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def plan(blocks: list[dict[str, Any]], prefix: str | None = None, suffix: str | None = None,
         find: str | None = None, replace: str = "", strip_prefix: str | None = None,
         template: str | None = None) -> list[tuple[dict[str, Any], str]]:
    """(block, new name) for every block whose name would change.

    template placeholders: {name} (after strip/find), {type} (display type, e.g. "Ion Thruster"),
    {n} (1, 2, 3... per display type, in the given order), {i} (1, 2, 3... across all blocks)."""
    counts: dict[str, int] = defaultdict(int)
    changes = []
    if template:
        # Number in natural order ("Thruster 2" before "Thruster 10"), not the game's text order.
        blocks = sorted(blocks, key=lambda x: _natural(x["name"]))
    for i, block in enumerate(blocks, 1):
        name = block["name"]
        if strip_prefix and name.lower().startswith(strip_prefix.lower()):
            name = name[len(strip_prefix):].lstrip()
        if find:
            name = re.sub(re.escape(find), lambda _: replace, name, flags=re.IGNORECASE).strip()
        if template:
            kind = block.get("displayType") or block.get("type") or "Block"
            counts[kind] += 1
            name = template.format_map(_Fields(name=name, type=kind, n=counts[kind], i=i)).strip()
        if prefix and not name.lower().startswith(prefix.strip().lower()):
            name = f"{prefix.rstrip()} {name}" if not prefix.endswith((" ", "-", "_", ".", ":")) else prefix + name
        if suffix and not name.lower().endswith(suffix.strip().lower()):
            name = f"{name} {suffix.lstrip()}" if not suffix.startswith((" ", "-", "_", ".", ":")) else name + suffix
        name = re.sub(r"\s{2,}", " ", name).strip()
        if name and name != block["name"]:
            changes.append((block, name))
    return changes


def _natural(name: str) -> list[Any]:
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.lower()) for part in re.split(r"(\d+)", name) if part]


class _Fields(dict):
    """Leaves unknown {placeholders} as they are instead of failing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def summary(changes: list[tuple[dict[str, Any], str]], limit: int = 12) -> str:
    lines = [f"  {block['name']} -> {new}" for block, new in changes[:limit]]
    if len(changes) > limit:
        lines.append(f"  ... and {len(changes) - limit} more")
    return "\n".join(lines)
