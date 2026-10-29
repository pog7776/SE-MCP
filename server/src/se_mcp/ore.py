"""Ore detection: summarizing the HUD's ore markers (what the ship's ore detectors have found)."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


def by_ore(markers: list[dict[str, Any]]) -> "OrderedDict[str, dict[str, Any]]":
    """Per ore: marker count and the nearest marker (markers arrive nearest first)."""
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for m in markers:
        g = groups.setdefault(m["ore"], {"count": 0, "nearest": m, "bodies": []})
        g["count"] += 1
        if m["body"] not in g["bodies"]:
            g["bodies"].append(m["body"])
    return groups


def why_empty(r: dict[str, Any]) -> str:
    detectors = r.get("detectors")
    if detectors is not None and not detectors:
        return "This ship has no ore detector."
    if detectors and not any(d["working"] for d in detectors):
        return "The ore detector is off or not working."
    if not r.get("visible", True):
        return "Ore markers are hidden on the HUD."
    if not r.get("controlling") and detectors and not any(d["broadcastUsingAntennas"] for d in detectors):
        return ("Ore detectors only report while someone controls the ship, or with 'broadcast using antennas' on. "
                "Sit in a cockpit, or turn that option on.")
    reach = max((d["range"] for d in detectors or []), default=0)
    return f"No ore within the detector's range ({reach:.0f} m)." if reach else "No ore markers right now."


def scan_text(r: dict[str, Any], ore: str | None = None, limit: int = 15) -> str:
    markers = r["markers"]
    if ore:
        markers = [m for m in markers if ore.lower() in m["ore"].lower()]
    det = r.get("detectors") or []
    head = ", ".join(f"{d['name']} {d['range']:.0f} m{'' if d['working'] else ' (OFF)'}" for d in det)
    if not markers:
        return (f"No {ore} found. " if ore else "") + why_empty(r)
    groups = by_ore(markers)
    lines = [f"Ore detected ({r['total']} markers{'; detectors: ' + head if head else ''}):"]
    for name, g in groups.items():
        n = g["nearest"]
        lines.append(f"- {name}: {g['count']} marker(s) on {', '.join(g['bodies'])}; nearest {n['distance']:,.0f} m at "
                     f"{', '.join(f'{c:.0f}' for c in n['position'])}")
    if ore:
        lines.append(f"Nearest {limit} {ore} markers:")
        for m in markers[:limit]:
            lines.append(f"  {m['distance']:,.0f} m: {', '.join(f'{c:.0f}' for c in m['position'])} ({m['body']})")
    return "\n".join(lines)
