"""Guiding the player: a route to a point that steers around whatever is in the way, from line-of-sight checks.

The search is kept free of the game so it can be tested with fake geometry: `blocked(a, b)` says what a straight
line from a to b hits first (or None), and the planner tries detour points beside each obstacle.
"""

from __future__ import annotations

import math
from typing import Any, Awaitable, Callable

Vec = list[float]
Blocked = Callable[[Vec, Vec], Awaitable[dict[str, Any] | None]]

# How far beside an obstacle to try a detour point, and in how many directions round the line.
DETOUR_DISTANCES = (12.0, 25.0, 50.0, 100.0)
DETOUR_DIRECTIONS = 8


def sub(a: Vec, b: Vec) -> Vec:
    return [a[i] - b[i] for i in range(3)]


def add(a: Vec, b: Vec, k: float = 1.0) -> Vec:
    return [a[i] + b[i] * k for i in range(3)]


def norm(a: Vec) -> float:
    return math.sqrt(sum(c * c for c in a))


def unit(a: Vec) -> Vec:
    n = norm(a)
    return [c / n for c in a] if n > 1e-9 else [0.0, 0.0, 1.0]


def cross(a: Vec, b: Vec) -> Vec:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def as_vec(p: Any) -> Vec:
    """A position from the game: [x, y, z] or {"x", "y", "z"}."""
    if isinstance(p, dict):
        return [float(p["x"]), float(p["y"]), float(p["z"])]
    return [float(c) for c in p]


def path_length(start: Vec, points: list[Vec]) -> float:
    total, here = 0.0, start
    for p in points:
        total += norm(sub(p, here))
        here = p
    return total


def frame(start: Vec, end: Vec) -> tuple[Vec, Vec, Vec]:
    """Forward along start -> end, and two unit vectors across it."""
    forward = unit(sub(end, start))
    helper = [0.0, 1.0, 0.0] if abs(forward[1]) < 0.9 else [1.0, 0.0, 0.0]
    side = unit(cross(forward, helper))
    return forward, side, cross(side, forward)


def detour_candidates(start: Vec, end: Vec, hit: Vec, clearance: float = 0.0) -> list[Vec]:
    """Points beside an obstacle hit on the way from start to end: around the line, at a few distances,
    level with the hit (pulled back toward the start a little), shortest overall detour first.
    clearance: the mover's own radius (a ship), which pushes the points out and back by that much more."""
    forward, side, up = frame(start, end)
    base = add(hit, forward, -3.0 - clearance)
    points = []
    for d in (clearance + d for d in DETOUR_DISTANCES):
        for k in range(DETOUR_DIRECTIONS):
            a = 2 * math.pi * k / DETOUR_DIRECTIONS
            offset = add([side[i] * math.cos(a) for i in range(3)], up, math.sin(a))
            points.append(add(base, offset, d))
    return sorted(points, key=lambda p: path_length(start, [p, end]))


def beside(a: Vec, b: Vec, radius: float) -> list[tuple[Vec, Vec]]:
    """Four lines parallel to a -> b, `radius` out on each side: a ship of that radius sweeping along a -> b
    touches what these touch (roughly; a full shape sweep is PLAN item 15's path.sweep)."""
    _, side, up = frame(a, b)
    return [(add(a, v, radius * k), add(b, v, radius * k)) for v in (side, up) for k in (1, -1)]


async def plan_route(start: Vec, end: Vec, blocked: Blocked, max_checks: int = 120, depth: int = 2,
                     clearance: float = 0.0) -> tuple[list[Vec] | None, dict[str, Any] | None, int]:
    """Waypoints from start to end (end included) that each have a clear line, or None.
    Returns (route, what blocks the direct line, checks used). Prefers one turn beside the obstacle (both legs
    clear, shortest first); failing that, recurses from the reachable detour points, up to `depth` turns."""
    checks = 0
    first: dict[str, Any] | None = None

    async def check(a: Vec, b: Vec) -> dict[str, Any] | None:
        nonlocal checks
        checks += 1
        return await blocked(a, b)

    async def search(a: Vec, level: int) -> list[Vec] | None:
        nonlocal first
        hit = await check(a, end)
        if level == 0:
            first = hit
        if hit is None:
            return [end]
        if level >= depth:
            return None
        reachable = []
        for c in detour_candidates(a, end, as_vec(hit["position"]), clearance):
            if checks >= max_checks:
                break
            if await check(a, c) is None:
                if await check(c, end) is None:
                    return [c, end]
                reachable.append(c)
        for c in reachable[:3]:
            rest = await search(c, level + 1)
            if rest is not None:
                return [c] + rest
        return None

    route = await search(start, 0)
    return route, first, checks


def obstacle_name(hit: dict[str, Any]) -> str:
    if hit.get("voxel"):
        return "an asteroid"
    name = hit.get("entity") or "something"
    block = (hit.get("block") or {}).get("name") or ""
    # Armour has no name of its own, only its type id ("MyObjectBuilder_CubeBlock/LargeBlockArmor...").
    return f"{name} ({block})" if block and "/" not in block else name


def distance_text(d: float) -> str:
    return f"{d / 1000:.1f} km" if d >= 1000 else f"{d:.0f} m"
