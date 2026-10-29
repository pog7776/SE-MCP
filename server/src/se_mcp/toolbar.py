"""Toolbars (ship controllers, button panels, timers, sensors, the player's own) and block groups: formatting."""

from __future__ import annotations

from typing import Any


def slot_line(s: dict[str, Any]) -> str:
    where = f"{s['page']}.{s['slot']}"
    if s["kind"] in ("block", "group"):
        what = f"{s['target']}{' (group)' if s['kind'] == 'group' else ''}: {s.get('actionName') or s.get('action')}"
        return f"  {where}  {what}  [action {s.get('action')}]"
    return f"  {where}  {s.get('text')} ({s['kind']})"


def toolbar_text(t: dict[str, Any], page: int | None = None) -> str:
    owner = "Your toolbar" if t["owner"] == "player" else f"{t['owner']} toolbar"
    slots = [s for s in t["slots"] if page is None or s["page"] == page]
    head = f"{owner} ({t['type']}, {t['pages']} pages, showing page {t['currentPage']}):"
    if not slots:
        return head + (f"\n  page {page} is empty" if page else "\n  empty")
    lines = [head]
    current = None
    for s in slots:
        if s["page"] != current:
            current = s["page"]
            lines.append(f" Page {current}:")
        lines.append(slot_line(s))
    return "\n".join(lines)


def change_text(r: dict[str, Any]) -> str:
    before = slot_line(r["before"]).strip() if r.get("before") else "empty"
    after = slot_line(r["after"]).strip() if r.get("after") else "empty"
    owner = "your toolbar" if r["owner"] == "player" else r["owner"]
    return f"{owner}: {before}  ->  {after}"


def group_text(r: dict[str, Any]) -> str:
    if r["deleted"]:
        return f"Deleted group '{r['name']}'."
    verb = "Created" if r["created"] else "Updated"
    names = ", ".join(b["name"] for b in r["blocks"][:30]) + ("..." if len(r["blocks"]) > 30 else "")
    return f"{verb} group '{r['name']}' ({r['before']} -> {len(r['blocks'])} blocks): {names}"
