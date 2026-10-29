"""Radio: what antennas and beacons the ship or suit hears, its own radio equipment, and laser antenna links."""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any

from .bridge import Bridge
from .resolve import ResolveError

Vec = list[float]


def clean(name: str | None) -> str:
    # The game puts an icon glyph from Unicode's private-use area in some HUD names; drop it.
    return re.sub("[-]", "", name or "").strip()


async def signals(bridge: Bridge, grid_id: str | None = None, measure_s: float = 0) -> dict[str, Any]:
    """radio.signals, optionally sampled twice to add each signal's velocity and speed."""
    params = {"gridId": grid_id} if grid_id else {}
    first = await bridge.call("radio.signals", params)
    if measure_s <= 0:
        return first
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.sleep(min(measure_s, 10))
    second = await bridge.call("radio.signals", params)
    dt = max(0.1, loop.time() - start)
    before = {s["antennaId"]: s["position"] for s in first["signals"]}
    for s in second["signals"]:
        if s["antennaId"] in before:
            v = [(a - b) / dt for a, b in zip(s["position"], before[s["antennaId"]])]
            s["velocity"] = [round(x, 1) for x in v]
            s["speed"] = round(math.hypot(*v), 1)
    return second


def find_signal(listing: dict[str, Any], name: str) -> dict[str, Any]:
    wanted = name.strip().lower()
    pool = [s for s in listing["signals"] if not s.get("ownShip")]
    exact = [s for s in pool if wanted in (clean(s["name"]).lower(), (s.get("gridName") or "").lower())]
    found = exact or [s for s in pool if wanted in clean(s["name"]).lower() or wanted in (s.get("gridName") or "").lower()]
    if not found:
        names = ", ".join(clean(s["name"]) for s in pool[:15]) or "none"
        raise ResolveError(f"No radio signal matching '{name}'. Heard: {names}")
    # Several antennas on one ship are the same target: take the nearest.
    if len({s["entityId"] for s in found}) > 1:
        raise ResolveError(f"'{name}' matches several signals: {', '.join(clean(s['name']) for s in found)}")
    return min(found, key=lambda s: s["distance"])


def signal_line(s: dict[str, Any]) -> str:
    name = clean(s["name"])
    what = f"{s['kind']} on {s['gridName']}" if s.get("gridName") and s["gridName"] != name else s["kind"]
    parts = [f"- {name} ({what}, {s['body']}, {s['relation']}): {s['distance']:,.0f} m"]
    if s.get("speed") is not None:
        parts.append(f"moving {s['speed']:.1f} m/s" if s["speed"] > 0.2 else "stationary")
    if not s["direct"]:
        parts.append("relayed")
    if s.get("radius"):
        parts.append(f"broadcasting {s['radius']:,.0f} m")
    return ", ".join(parts)


def signals_text(listing: dict[str, Any], include_own: bool = False) -> str:
    shown = [s for s in listing["signals"] if include_own or not s.get("ownShip")]
    shown = [s for s in shown if s["kind"] != "suit" or s["relation"] != "owner" or include_own]
    who = "your suit" if listing["listenerKind"] == "suit" else listing["listener"]
    if not shown:
        return f"Nothing heard on {who}'s radio."
    return f"Heard by {who} ({len(shown)} signal(s)):\n" + "\n".join(signal_line(s) for s in shown)


def equipment_text(r: dict[str, Any]) -> str:
    if not r["equipment"]:
        return f"No antennas, beacons or laser antennas on {r['gridName']}."
    lines = [f"{r['gridName']} radio equipment:"]
    for e in r["equipment"]:
        state = "working" if e["working"] else ("OFF" if e.get("enabled") is False else "NOT WORKING")
        where = f" on {e['gridName']}" if e["gridName"] != r["gridName"] else ""
        if e["kind"] == "laser":
            link = f"linked to {e['other']['name']} on {e['other']['gridName']}" if e.get("other") else e["status"]
            reach = "unlimited" if e["range"] is None else f"{e['range']:,.0f} m"
            extra = f"{link}; range {reach}"
            extra += ", permanent" if e["permanent"] else ""
            extra += ", target outside its rotation limits" if e.get("outsideLimits") else ""
        else:
            hud = f", HUD '{e['hudText']}'" if e.get("hudText") else ""
            extra = f"radius {e['radius']:,.0f} m{hud}"
            if e["kind"] == "antenna":
                extra += "" if e["broadcasting"] else ", NOT broadcasting (receive only)"
                extra += ", shows ship name" if e.get("showShipName") else ""
        lines.append(f"- {e['name']} ({e['kind']}{where}): {state}; {extra}")
    return "\n".join(lines)
