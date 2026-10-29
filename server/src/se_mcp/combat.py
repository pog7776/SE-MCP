"""Combat helpers: picking weapons by name, formatting weapon and damage reports, and camera tracking math."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .resolve import ResolveError
from .vision import entity_line

Vec = list[float]

TARGET_FLAGS = ("enemies", "neutrals", "characters", "largeGrids", "smallGrids", "stations", "missiles", "meteors")
TARGET_LABELS = {"enemies": "enemies", "neutrals": "neutrals", "characters": "characters", "largeGrids": "large grids",
                 "smallGrids": "small grids", "stations": "stations", "missiles": "missiles", "meteors": "meteors",
                 "friends": "friends"}


def pick(weapons: list[dict[str, Any]], spec: str | None, kinds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Weapons by comma-separated names (exact, then partial), or "all". kinds limits to turret/fixed/turret controller."""
    pool = [w for w in weapons if not kinds or w["kind"] in kinds]
    if not spec or spec.strip().lower() in ("all", "*"):
        if not pool:
            raise ResolveError("No matching weapons on this ship.")
        return pool
    chosen: dict[str, dict[str, Any]] = {}
    for name in [s.strip() for s in spec.split(",") if s.strip()]:
        wanted = name.lower()
        exact = [w for w in pool if w["name"].lower() == wanted or w["id"] == name]
        found = exact or [w for w in pool if wanted in w["name"].lower()]
        if not found:
            names = ", ".join(w["name"] for w in pool) or "none"
            raise ResolveError(f"No weapon matching '{name}'. Available: {names}")
        for w in found:
            chosen[w["id"]] = w
    return list(chosen.values())


def _ammo_line(a: dict[str, Any]) -> str:
    rounds = a["carried"] * a["rounds"]
    return f"{a['name']}: {a['carried']:g} on board ({rounds:,.0f} rounds)"


def weapon_line(w: dict[str, Any], ammo: dict[str, Any], ship: str = "") -> str:
    state = "working" if w["working"] else ("OFF" if w.get("enabled") is False else "NOT WORKING")
    where = f" on {w['gridName']}" if w.get("gridName") not in (None, ship) else ""
    parts = [f"- {w['name']} ({w['kind']}{where}): {state}"]
    if w["kind"] != "turret controller":
        loaded = ammo.get(w.get("loaded") or "", {}).get("name") or w.get("loaded") or "nothing"
        parts.append(f"loaded {loaded}, {w.get('magazinesInside', 0):g} mag(s) inside")
        if len(w.get("magazines", [])) > 1:
            parts.append("takes " + " / ".join(ammo.get(m, {}).get("name", m) for m in w["magazines"]))
        if w.get("shooting"):
            parts.append("FIRING")
    if "ai" in w:
        if w.get("underControl"):
            parts.append("player is controlling it")
        if w.get("target"):
            parts.append("TARGET: " + entity_line(w["target"]))
        elif w.get("ai"):
            on = [TARGET_LABELS[k] for k, v in w["targets"].items() if v]
            parts.append(f"idle, range {w['range']:.0f} m, targets {', '.join(on) or 'nothing'}")
            if w.get("targetingGroup"):
                parts.append(f"aims at {w['targetingGroup']}")
        else:
            parts.append("AI off (manual)")
    if w.get("tools"):
        parts.append("tools: " + ", ".join(w["tools"]))
    return "; ".join(parts)


def weapons_text(r: dict[str, Any]) -> str:
    weapons = r["weapons"]
    if not weapons:
        return f"No weapons on {r['gridName']}."
    lines = [f"{r['gridName']}: {len(weapons)} weapon(s)."]
    lines += [weapon_line(w, r["ammo"], r["gridName"]) for w in weapons]
    empty = [w["name"] for w in weapons if w["kind"] != "turret controller" and not w.get("magazinesInside")
             and not r["ammo"].get(w.get("loaded") or "", {}).get("carried")]
    if r["ammo"]:
        lines.append("Ammo on board:")
        lines += ["  " + _ammo_line(a) for a in r["ammo"].values()]
    if empty:
        lines.append("Out of ammo: " + ", ".join(empty))
    return "\n".join(lines)


def damage_text(r: dict[str, Any], limit: int = 40) -> str:
    damaged = r["damaged"]
    if not damaged:
        return f"{r['gridName']}: no damage ({r['blocks']} blocks all at full integrity)."
    broken = [d for d in damaged if d.get("functional") is False]
    by_type: dict[str, int] = defaultdict(int)
    for d in damaged:
        by_type[d["type"]] += 1
    lines = [f"{r['gridName']}: {len(damaged)} of {r['blocks']} blocks damaged or unfinished"
             + (f", {len(broken)} not working." if broken else "."),
             "By type: " + ", ".join(f"{t} {n}" for t, n in sorted(by_type.items(), key=lambda x: -x[1]))]
    for d in sorted(damaged, key=lambda d: d["integrity"])[:limit]:
        missing = ", ".join(f"{n} {k}" for k, n in d["missing"].items())
        lines.append(f"- {d['name']} ({d['type']}): {d['integrity']:.0f}%"
                     + (" NOT WORKING" if d.get("functional") is False else "")
                     + (", deformed" if d.get("deformed") else "")
                     + (f"; needs {missing}" if missing else ""))
    if len(damaged) > limit:
        lines.append(f"... and {len(damaged) - limit} more")
    return "\n".join(lines)


def predict(position: Vec, velocity: Vec, seconds: float) -> Vec:
    return [p + v * seconds for p, v in zip(position, velocity)]



def no_fix_text(r: dict[str, Any]) -> str:
    """Why a camera.track step found nothing."""
    tried = r.get("notReady") or []
    if r.get("camera"):
        return f"{r['camera']} scanned there and found nothing."
    if tried and all(t["reason"] == "outside its view cone" for t in tried):
        return "No camera on this ship points that way (it's outside every camera's view cone)."
    charging = [t for t in tried if t["reason"] == "charging"]
    if charging:
        soonest = min(t["msUntilScan"] for t in charging) / 1000
        return f"The cameras that can see there are still charging ({soonest:.1f} s)."
    return "No camera could scan there."
