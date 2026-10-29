"""Forecasts: how long batteries, hydrogen, oxygen, uranium and the suit last at the current rate.

Batteries report their in/out power directly. Gas and suit levels don't, so they're sampled twice a few
seconds apart. Reactors burn 1 kg of uranium per MWh produced (vanilla definitions: fuel/s = output /
FuelProductionToCapacityMultiplier(3600) x Ratio(1) / item mass(1 kg)).
"""

from __future__ import annotations

import math
from typing import Any

URANIUM_KG_PER_MWH = 1.0


def duration(hours: float) -> str:
    if math.isinf(hours) or hours > 24 * 365:
        return "indefinitely"
    seconds = hours * 3600
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def batteries(g: dict[str, Any]) -> str | None:
    b = (g.get("power") or {}).get("batteries")
    if not b or not b.get("count"):
        return None
    net = b["outputMW"] - b["inputMW"]
    head = f"Batteries: {b['storedMWh']:.1f}/{b['maxMWh']:.1f} MWh ({b['fill']:.0f}%)"
    if net > 1e-3:
        return f"{head}, draining {net:.2f} MW: empty in {duration(b['storedMWh'] / net)}"
    if net < -1e-3:
        missing = b["maxMWh"] - b["storedMWh"]
        return f"{head}, charging {-net:.2f} MW: full in {duration(missing / -net)}" if missing > 0.01 else f"{head}, full"
    return f"{head}, steady"


def reactors(g: dict[str, Any], uranium_kg: float) -> str | None:
    producers = [p for p in (g.get("power") or {}).get("producers", []) if p["type"] == "Reactor"]
    if not producers:
        return None
    output = sum(p["outputMW"] for p in producers)
    maximum = sum(p["maxMW"] for p in producers)
    head = f"Reactors: {output:.2f}/{maximum:.1f} MW, {uranium_kg:.1f} kg uranium on board"
    if uranium_kg <= 0:
        return head + ": NO FUEL"
    now = f"lasts {duration(uranium_kg / (output * URANIUM_KG_PER_MWH))} at this output" if output > 1e-3 else "idle"
    return f"{head}: {now}; {duration(uranium_kg / (maximum * URANIUM_KG_PER_MWH))} at full power"


def gas(name: str, before: dict[str, Any] | None, after: dict[str, Any] | None, seconds: float) -> str | None:
    if not after or not after.get("count"):
        return None
    head = f"{name}: {after['fill']:.0f}% ({after['storedL']:,.0f}/{after['capacityL']:,.0f} L)"
    if not before or seconds <= 0:
        return head
    rate = (after["storedL"] - before["storedL"]) / seconds  # L/s, negative = draining
    if rate < -0.5:
        return f"{head}, draining {-rate:,.0f} L/s: empty in {duration(after['storedL'] / -rate / 3600)}"
    if rate > 0.5:
        return f"{head}, filling {rate:,.0f} L/s: full in {duration((after['capacityL'] - after['storedL']) / rate / 3600)}"
    return f"{head}, steady"


def suit(before: dict[str, Any], after: dict[str, Any], seconds: float) -> list[str]:
    lines = []
    for key, label in (("energy", "Suit energy"), ("oxygen", "Suit oxygen"), ("hydrogen", "Suit hydrogen")):
        now = after.get(key)
        if now is None:
            continue
        rate = (now - before.get(key, now)) / seconds if seconds > 0 else 0  # % per second
        if rate < -1e-3:
            lines.append(f"{label}: {now:.0f}%, falling: empty in {duration(now / -rate / 3600)}")
        elif rate > 1e-3:
            lines.append(f"{label}: {now:.0f}%, refilling")
        else:
            lines.append(f"{label}: {now:.0f}%, steady")
    return lines
