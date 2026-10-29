"""Measuring grid surfaces: height maps from one game-side ray scan, landing-spot search, route clearance.

A surface frame is (origin, normal n, axes u and v in the plane). Heights are measured along n from the origin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .bridge import Bridge
from .docking import add, dot, sub

Vec = list[float]


@dataclass
class HeightMap:
    origin: Vec
    n: Vec
    u: Vec
    v: Vec
    u0: float
    v0: float
    step: float
    heights: list[list[float | None]]
    hazards: dict[tuple[int, int], str] | None = None  # (i, j) -> T thruster, X weapon, D/G/W tool, H door, M joint

    def hazard(self, a: float, b: float) -> str | None:
        i, j = round((a - self.u0) / self.step), round((b - self.v0) / self.step)
        return (self.hazards or {}).get((i, j))

    def at(self, a: float, b: float) -> float | None:
        i, j = round((a - self.u0) / self.step), round((b - self.v0) / self.step)
        if 0 <= i < len(self.heights) and 0 <= j < len(self.heights[0]):
            return self.heights[i][j]
        return None

    def cells(self, a0: float, a1: float, b0: float, b1: float):
        a = math.floor(a0)
        while a <= a1:
            b = math.floor(b0)
            while b <= b1:
                yield a, b, self.at(a, b)
                b += self.step
            a += self.step

    def coords(self, p: Vec) -> tuple[float, float, float]:
        d = sub(p, self.origin)
        return dot(d, self.u), dot(d, self.v), dot(d, self.n)

    def point(self, a: float, b: float, h: float = 0.0) -> Vec:
        return add(add(add(self.origin, self.u, a), self.v, b), self.n, h)


async def scan(bridge: Bridge, origin: Vec, n: Vec, u: Vec, v: Vec, half_u: float, half_v: float, step: float = 1.0,
               top: float = 25.0, depth: float = 10.0, ignore_id: str | None = None) -> HeightMap:
    params: dict[str, Any] = {"origin": origin, "normal": n, "u": u, "v": v, "u0": -half_u, "u1": half_u,
                              "v0": -half_v, "v1": half_v, "step": step, "top": top, "depth": depth}
    if ignore_id:
        params["ignoreId"] = ignore_id
    r = await bridge.call("surface.scan", params)
    hazards = {}
    for h in r.get("hazards") or []:
        i, j, code = h.split(",")
        hazards[(int(i), int(j))] = code
    return HeightMap(origin, n, u, v, r["u0"], r["v0"], r["step"], r["heights"], hazards)


def render(hm: HeightMap, mark: tuple[float, float] | None = None) -> str:
    """Rows along u, columns along v; each cell the height in whole meters (0-9, + for 10+, - below, . nothing),
    or a letter for a hazardous block (T thruster, X weapon, D drill, G grinder, W welder, H door, M rotor/hinge/piston)."""
    lines = []
    for i, row in enumerate(hm.heights):
        a = hm.u0 + i * hm.step
        chars = []
        for j, h in enumerate(row):
            b = hm.v0 + j * hm.step
            if mark and abs(a - mark[0]) < hm.step / 2 and abs(b - mark[1]) < hm.step / 2:
                chars.append("@")
            elif (hm.hazards or {}).get((i, j)):
                chars.append(hm.hazards[(i, j)])
            elif h is None:
                chars.append(".")
            elif h < -0.5:
                chars.append("-")
            else:
                chars.append(str(int(round(h))) if h < 9.5 else "+")
        lines.append(f"{a:+5.0f} " + "".join(chars))
    return "\n".join(lines)


def level(hm: HeightMap, a: float, b: float, h: float, tolerance: float = 0.05) -> bool:
    """The cell at (a, b) and its four neighbours are all at height h (a flat face to lock onto)."""
    s = hm.step
    for da, db in ((0, 0), (s, 0), (-s, 0), (0, s), (0, -s)):
        x = hm.at(a + da, b + db)
        if x is None or abs(x - h) > tolerance:
            return False
    return True


def find_spots(hm: HeightMap, box: tuple[float, float, float, float], others: list[tuple[float, float]],
               near: tuple[float, float], margin: float = 1.0, limit: int = 5, hazard_margin: float = 2.0) -> list[dict[str, Any]]:
    """Spots (gear at a, b) where the ship's footprint `box` (u0, u1, v0, v1 relative to the gear) plus margin has
    nothing taller than the surface under the gear (+0.3 m), the other gears touch the same height (±0.15 m), and
    at least 60% of the footprint is backed by hull (so not perched on top of a pillar), and no hazardous block
    (thrusters, weapons, tools, doors, joints) lies under it or within hazard_margin. Nearest first."""
    out = []
    for i, row in enumerate(hm.heights):
        for j, h in enumerate(row):
            if h is None:
                continue
            a, b = hm.u0 + i * hm.step, hm.v0 + j * hm.step
            if any(hm.at(a + oa, b + ob) is None or abs(hm.at(a + oa, b + ob) - h) > 0.15 for oa, ob in others):
                continue
            # Gear only locks on a face parallel to it: every gear's cell and its neighbours must be level
            # (a gentle armor slope passes a two-point height check but not this).
            if not all(level(hm, a + oa, b + ob, h) for oa, ob in [(0.0, 0.0)] + list(others)):
                continue
            tallest, backed, total = -math.inf, 0, 0
            for _, _, x in hm.cells(a + box[0] - margin, a + box[1] + margin, b + box[2] - margin, b + box[3] + margin):
                total += 1
                if x is None:
                    continue
                tallest = max(tallest, x)
                if x > h - 3:
                    backed += 1
            if tallest > h + 0.3 or backed < 0.6 * total:
                continue
            if hm.hazards and any(hm.hazard(x, y) for x, y, _ in hm.cells(
                    a + box[0] - hazard_margin, a + box[1] + hazard_margin, b + box[2] - hazard_margin, b + box[3] + hazard_margin)):
                continue
            out.append({"a": a, "b": b, "height": h, "distance": math.dist((a, b), near)})
    out.sort(key=lambda s: s["distance"])
    return out[:limit]


def route_clearance(hm: HeightMap, box: tuple[float, float, float, float], start: tuple[float, float],
                    end: tuple[float, float], margin: float = 1.0) -> float:
    """The tallest thing under the ship's footprint anywhere along a straight slide from start to end."""
    tallest = -math.inf
    for k in range(21):
        t = k / 20
        a, b = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
        for _, _, x in hm.cells(a + box[0] - margin, a + box[1] + margin, b + box[2] - margin, b + box[3] + margin):
            if x is not None:
                tallest = max(tallest, x)
    return tallest
