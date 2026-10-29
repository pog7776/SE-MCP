"""Autopilot and GPS helpers: parsing coordinates, finding the remote control, formatting."""

from __future__ import annotations

import math
import re
from typing import Any

from . import resolve
from .bridge import Bridge
from .resolve import ResolveError

Vec = list[float]

_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def parse_coords(text: str) -> tuple[str | None, Vec] | None:
    """'GPS:Name:x:y:z:...' (the game's clipboard format), or three numbers separated by spaces/commas."""
    text = text.strip()
    gps = re.match(rf"^GPS:([^:]*):({_NUMBER}):({_NUMBER}):({_NUMBER}):", text + ":")
    if gps:
        return gps.group(1), [float(gps.group(i)) for i in (2, 3, 4)]
    numbers = re.fullmatch(rf"\(?\s*({_NUMBER})\s*[, ]\s*({_NUMBER})\s*[, ]\s*({_NUMBER})\s*\)?", text)
    if numbers:
        return None, [float(n) for n in numbers.groups()]
    return None


def distance(a: Vec, b: Vec) -> float:
    return math.dist(a, b)


async def find_gps(bridge: Bridge, name: str) -> dict[str, Any]:
    entries = (await bridge.call("gps.list"))["gps"]
    wanted = name.strip().lower()
    exact = [g for g in entries if g["name"].lower() == wanted]
    partial = exact or [g for g in entries if wanted in g["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        names = ", ".join(g["name"] for g in entries[:15]) or "none"
        raise ResolveError(f"No GPS entry matching '{name}'. Your GPS entries: {names}")
    raise ResolveError(f"'{name}' matches several GPS entries: {', '.join(g['name'] for g in partial)}")


async def resolve_target(bridge: Bridge, target: str) -> tuple[str, Vec]:
    """A GPS name, a pasted GPS string, or x,y,z coordinates."""
    parsed = parse_coords(target)
    if parsed:
        name, coords = parsed
        return name or "Target", coords
    gps = await find_gps(bridge, target)
    return gps["name"], gps["coords"]


async def remote_control(bridge: Bridge, block: str | None, grid: str | None) -> dict[str, Any]:
    """The named RC, or the only working one on the (current) ship."""
    if block:
        target = await resolve.block(bridge, block, grid)
        return await bridge.call("rc.get", {"blockId": target["id"]})
    ship = await resolve.grid(bridge, grid)
    listing = await bridge.call("blocks.list", {"gridId": ship["id"], "type": "RemoteControl", "limit": 50})
    rcs = [b for b in listing["blocks"] if b.get("accessible", True)]
    if not rcs:
        raise ResolveError(f"{ship.get('name') or 'This ship'} has no Remote Control block; autopilot needs one.")
    if len(rcs) > 1:
        raise ResolveError("Several Remote Controls: " + ", ".join(f"{b['name']} ({b['id']})" for b in rcs) + ". Say which one.")
    return await bridge.call("rc.get", {"blockId": rcs[0]["id"]})


def status(rc: dict[str, Any]) -> str:
    lines = [f"{rc['name']} on {rc['gridName']}: autopilot {'ON' if rc['autopilot'] else 'off'}, "
             f"speed {rc['speed']:.1f} m/s (limit {rc['speedLimit']:.0f}), mode {rc['flightMode']}, "
             f"leading face {rc['direction']}, collision avoidance {'on' if rc.get('collisionAvoidance') else 'off'}"
             + (", precision mode" if rc.get("dockingMode") else "")]
    wp = rc.get("currentWaypoint")
    if wp and not rc["autopilot"] and wp["distance"] < 10:
        lines.append(f"Arrived at {wp['name']} ({wp['distance']:.0f} m off).")
    elif wp:
        eta = f", ~{wp['distance'] / rc['speed']:.0f} s at this speed" if rc["speed"] > 0.5 else ""
        lines.append(f"Heading to {wp['name']}: {wp['distance']:.0f} m away{eta}")
    elif rc["waypoints"]:
        lines.append("Waypoints: " + ", ".join(w["name"] for w in rc["waypoints"]))
    if rc.get("gravityG"):
        lines.append(f"Natural gravity {rc['gravityG']:.2f} g")
    if rc.get("remoteControlled"):
        lines.append("Someone is remote-controlling the ship through this block.")
    return "\n".join(lines)


def gps_line(g: dict[str, Any]) -> str:
    d = g["distance"]
    dist = f"{d / 1000:.1f} km" if d >= 10000 else f"{d:.0f} m"
    x, y, z = g["coords"]
    desc = f" ({g['description']})" if g.get("description") else ""
    hidden = "" if g.get("showOnHud", True) else " [hidden]"
    return f"- {g['name']}, {dist} away{desc}{hidden}: GPS:{g['name']}:{x:.2f}:{y:.2f}:{z:.2f}:"
