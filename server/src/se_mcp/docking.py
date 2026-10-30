"""Docking: fly a ship's connector onto another connector with gyro and thruster overrides.

The remote control autopilot can't do this: it always turns its leading face toward the direction of travel, so
every small sideways correction swings the whole ship (and a connector far from the RC swings tens of meters).
Instead this holds the orientation with gyro overrides (our connector facing the target connector) and moves the
ship with thruster overrides, both from a simple closed loop run over RPC a few times a second:

- rotation: target spin = axis(our connector forward -> -target forward) x min(gain x angle, max), no roll rate;
- translation: position error -> target velocity (capped) -> acceleration (capped) -> force = mass x accel, split
  over the thrusters by which way each one pushes. Dampeners are off while flying and back on afterwards.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Callable

from .bridge import Bridge
from .steering import RAD_S_TO_RPM, Steering, angle_between, cross, dot, frame_of, length, normalize

Vec = list[float]


def add(a: Vec, b: Vec, k: float = 1.0) -> Vec:
    return [x + y * k for x, y in zip(a, b)]


def sub(a: Vec, b: Vec) -> Vec:
    return [x - y for x, y in zip(a, b)]


def scale(a: Vec, k: float) -> Vec:
    return [x * k for x in a]


def clamp_length(a: Vec, limit: float) -> Vec:
    n = length(a)
    return scale(a, limit / n) if n > limit else a


MIN_OVERRIDE = 0.015


def thrust_split(force: Vec, thrusters: list[dict[str, Any]]) -> dict[str, float]:
    """Override fraction per thruster so the ship pushes with `force` (N): thrusters are grouped by the way they
    push; each group whose direction has a positive share of the force gets that share over its total thrust."""
    groups: list[tuple[Vec, float, list[str]]] = []
    for t in thrusters:
        if not t["working"] or t["maxThrust"] <= 0:
            continue
        for i, (d, total, ids) in enumerate(groups):
            if dot(d, t["push"]) > 0.98:
                groups[i] = (d, total + t["maxThrust"], ids + [t["id"]])
                break
        else:
            groups.append((t["push"], t["maxThrust"], [t["id"]]))
    out: dict[str, float] = {}
    for d, total, ids in groups:
        share = dot(force, d)
        frac = min(1.0, share / total) if share > 0 else 0.0
        # Thrusters ignore tiny overrides (below ~1%), which stalls slow touchdowns: lift small asks to 1.5%.
        if 0.001 < frac < MIN_OVERRIDE:
            frac = MIN_OVERRIDE
        for i in ids:
            out[i] = round(frac, 4)
    return out


class StallError(RuntimeError):
    """The ship stopped getting closer to its mark: it's touching something."""


# Ships that were parked with their thrusters off when undock released them, so dock can park them the same way.
PARKED_WITH_THRUSTERS_OFF: set[str] = set()


class ConnectorPilot:
    """Moves a ship so its connector sits at a point on the target connector's axis, facing it."""

    def __init__(self, bridge: Bridge, ship_id: str, connector_id: str, target: dict[str, Any],
                 log: Callable[[str], None] = lambda _: None, max_speed: float = 2.0, max_accel: float = 2.0):
        self.bridge, self.ship_id, self.connector_id, self.target = bridge, ship_id, connector_id, target
        self.log = log
        self.max_speed, self.max_accel = max_speed, max_accel
        self.steering = Steering(bridge, ship_id)

    async def connector(self) -> dict[str, Any]:
        r = await self.bridge.call("dock.get", {"gridId": self.ship_id})
        return next(c for c in r["connectors"] if c["id"] == self.connector_id)

    async def start(self) -> None:
        """Thrusters on (ships often park with them off), take the gyros (calibrated), dampeners off."""
        m = await self.bridge.call("grid.motion", {"gridId": self.ship_id, "thrusters": True})
        self.thrusters_were_on = any(t["working"] for t in m["thrusters"])
        await self.bridge.call("flight.set", {"gridId": self.ship_id, "thrustersOn": True})
        await self.steering.take_control()
        await self.steering.calibrate(("Pitch", "Yaw", "Roll"))
        await self.bridge.call("flight.set", {"gridId": self.ship_id, "dampeners": False})

    async def stop(self, docked: bool = False, park: bool = False) -> None:
        """Release every override and turn the dampeners back on. Once docked, thrusters go off when `park` (a
        docked ship's thrusters fight its carrier's), or else if that's how the ship was parked before."""
        params: dict[str, Any] = {"gridId": self.ship_id, "release": True, "dampeners": True}
        if docked and (park or not getattr(self, "thrusters_were_on", True) or self.ship_id in PARKED_WITH_THRUSTERS_OFF):
            params["thrustersOn"] = False
            PARKED_WITH_THRUSTERS_OFF.discard(self.ship_id)
        await self.bridge.call("flight.set", params)

    async def hold(self, distance: float, tolerance: float = 0.15, timeout_s: float = 90,
                   until: Callable[[dict[str, Any]], bool] | None = None,
                   slow_within: float = 0.0, slow_speed: float = 0.3) -> dict[str, Any]:
        """Fly the connector to `distance` meters out along the target's axis; return the connector when there
        (position within tolerance, nearly stopped and lined up) or when until(connector) is true.
        Within slow_within meters of the mark, speed is capped at slow_speed (touchdowns). Raises StallError if
        it stops closing in for 3 s while still off the mark: something is in the way."""
        point = add(self.target["position"], self.target["forward"], distance)
        facing = scale(self.target["forward"], -1)
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        last_log = 0.0
        best, best_at = math.inf, loop.time()
        while True:
            m = await self.bridge.call("grid.motion", {"gridId": self.ship_id, "thrusters": True})
            conn = await self.connector()
            # Rotation: spin about (our forward x wanted forward), slowing as it closes; no roll.
            angle = angle_between(conn["forward"], facing)
            spin = scale(normalize(cross(conn["forward"], facing)), min(0.6 * angle, 0.25)) if angle > 1e-3 else [0.0, 0.0, 0.0]
            gyros = {g["id"]: list(self.steering.commands(spin, g).get(p, 0.0) for p in ("Pitch", "Yaw", "Roll"))
                     for g in m["gyros"] if g["working"] and g["id"] in {x["id"] for x in self.steering.gyros}}
            # Translation.
            error = sub(point, conn["position"])
            velocity = m["linearVelocity"]
            limit = slow_speed if length(error) < slow_within else self.max_speed
            want_v = clamp_length(scale(error, 0.4), limit)
            accel = clamp_length(scale(sub(want_v, velocity), 1.2), self.max_accel)
            thrust = thrust_split(scale(accel, m["mass"]), m["thrusters"])
            await self.bridge.call("flight.set", {"thrusters": thrust, "gyros": gyros})

            now = loop.time()
            if now - last_log > 2:
                self.log(f"  {length(error):6.2f} m to go, {length(velocity):4.2f} m/s, {math.degrees(angle):5.1f} deg, {conn['status']}")
                last_log = now
            if until and until(conn):
                return conn
            if length(error) < tolerance and length(velocity) < 0.08 and math.degrees(angle) < 1.0:
                return conn
            # Stall: no progress for 3 s while still off the mark means we're pushing against something.
            if length(error) < best - 0.05:
                best, best_at = length(error), now
            # (Only when clearly off: within a few cm the push is tiny and progress is naturally slow.)
            elif now - best_at > 3 and length(error) > max(0.25, 3 * tolerance):
                raise StallError(f"stopped closing in {length(error):.2f} m from the mark: something is in the way")
            if now > end:
                raise TimeoutError(f"still {length(error):.2f} m and {math.degrees(angle):.1f} deg off after {timeout_s:.0f} s")


    async def settle(self, until: Callable[[dict[str, Any]], bool], pulses: int = 12, fraction: float = 0.05) -> dict[str, Any]:
        """The last few centimeters: the closed loop asks for so little thrust there that the ship hovers (a 36 t
        fighter didn't move on 2% overrides, did on 5%). Push toward the target in short pulses at `fraction`,
        braking if it picks up speed, until until(connector) is true; returns the connector either way."""
        toward = scale(self.target["forward"], -1)
        m = await self.bridge.call("grid.motion", {"gridId": self.ship_id, "thrusters": True})
        push = [t["id"] for t in m["thrusters"] if t["working"] and dot(t["push"], toward) > 0.98]
        brake = [t["id"] for t in m["thrusters"] if t["working"] and dot(t["push"], toward) < -0.98]
        # Gyros at zero spin hold the attitude (hold() leaves its last spin command in place).
        await self.bridge.call("flight.set", {"gyros": {g["id"]: [0.0, 0.0, 0.0] for g in self.steering.gyros}})
        conn = await self.connector()
        for _ in range(pulses):
            if until(conn):
                break
            await self.bridge.call("flight.set", {"thrusters": {i: fraction for i in push}})
            await asyncio.sleep(0.25)
            await self.bridge.call("flight.set", {"thrusters": {i: 0.0 for i in push}})
            for _ in range(6):
                await asyncio.sleep(0.1)
                conn = await self.connector()
                if until(conn):
                    return conn
            m = await self.bridge.call("grid.motion", {"gridId": self.ship_id})
            if dot(m["linearVelocity"], toward) > 0.2:
                await self.bridge.call("flight.set", {"thrusters": {i: fraction for i in brake}})
                await asyncio.sleep(0.2)
                await self.bridge.call("flight.set", {"thrusters": {i: 0.0 for i in brake}})
        return conn


class GearPilot(ConnectorPilot):
    """The same flying, with a landing gear / magnetic plate as the reference: its lock face (the block's down
    side) is brought onto a surface. The target is a point on the surface with forward = the surface normal."""

    async def connector(self) -> dict[str, Any]:
        r = await self.bridge.call("dock.get", {"gridId": self.ship_id})
        g = next(x for x in r["landingGear"] if x["id"] == self.connector_id)
        return {**g, "forward": scale(g["up"], -1), "status": g["mode"]}


def gear_face(gear: dict[str, Any]) -> Vec:
    """The direction a landing gear or magnetic plate locks toward: the block's down side."""
    return scale(gear["up"], -1)


ship_frame = frame_of


def facing(direction: Vec, frame: dict[str, Any]) -> str:
    """'right', 'up', ... when a direction lies along one of the ship's axes, else the two nearest ('forward-left')."""
    parts = []
    for k, (axis, pos, neg) in enumerate((("forward", "forward", "back"), ("up", "up", "down"), ("right", "right", "left"))):
        d = dot(direction, frame[axis])
        parts.append((abs(d), k, pos if d > 0 else neg))
    parts.sort(reverse=True)
    if parts[0][0] > 0.95:
        return parts[0][2]
    return "-".join(p[2] for p in sorted(parts[:2], key=lambda p: p[1]))  # forward/back, then up/down, then left/right


def placement(position: Vec, direction: Vec, frame: dict[str, Any]) -> str:
    """Where a block sits on the ship (meters right/up/forward of the ship's center) and which way it faces."""
    off = sub(position, frame["center"])
    where = []
    for axis, pos, neg in (("right", "right", "left"), ("up", "up", "down"), ("forward", "forward", "back")):
        d = dot(off, frame[axis])
        where.append(f"{abs(d):.0f} m {pos if d >= 0 else neg}")
    return f"faces {facing(direction, frame)}, {', '.join(where)} of center"


def status_text(r: dict[str, Any], frame: dict[str, Any] | None = None) -> str:
    lines = [f"{r['gridName']}{' (station)' if r['isStatic'] else ''} docking:"]
    if frame:
        lines[0] += f" (sides as seen from {frame['ref']})" if frame["ref"] else " (sides by the grid's own axes)"
    for c in r["connectors"]:
        where = f" on {c['gridName']}" if c["gridName"] != r["gridName"] else ""
        state = c["status"] if c["working"] else "NOT WORKING"
        to = f" to {c['other']['name']} on {c['other']['gridName']}" if c.get("other") else ""
        at = f"; {placement(c['position'], c['forward'], frame)}" if frame and "position" in c else ""
        lines.append(f"- {c['name']}{where} ({c['size']} connector): {state}{to}{at}")
    for g in r["landingGear"]:
        at = f"; {placement(g['position'], gear_face(g), frame)}" if frame and "position" in g else ""
        lines.append(f"- {g['name']} (landing gear): {g['mode']}{', auto-lock' if g['autoLock'] else ''}{at}")
    for m in r["mergeBlocks"]:
        lines.append(f"- {m['name']} (merge block): {'merged' if m['connected'] else 'not merged'}{'' if m['enabled'] else ', off'}")
    if len(lines) == 1:
        lines.append("  no connectors, landing gear or merge blocks")
    return "\n".join(lines)


def pick(connectors: list[dict[str, Any]], name: str | None, want_free: bool, what: str) -> dict[str, Any]:
    pool = [c for c in connectors if c["working"]]
    if name:
        found = ([c for c in pool if str(c.get("id")) == name.strip()] or [c for c in pool if c["name"].lower() == name.lower()]
                 or [c for c in pool if name.lower() in c["name"].lower()])
        if not found:
            raise ValueError(f"No working connector '{name}' on {what}. Connectors: {', '.join(c['name'] for c in connectors)}")
        return found[0]
    free = [c for c in pool if c["status"] != "Connected"] if want_free else pool
    if not free:
        raise ValueError(f"No free connector on {what}.")
    return free[0]


async def footprint_obstacles(bridge: Bridge, ship_id: str, gear: dict[str, Any], spot: Vec, normal: Vec,
                              clearance: float, margin: float = 1.0, step: float = 1.0) -> list[dict[str, Any]]:
    """What stands in the way if the ship (orientation unchanged) had its gear at spot + normal x clearance.

    The ship's box corners are taken relative to the gear and moved to the spot; the patch they cover on the
    surface plane (plus margin) is sampled with rays that look through the ship itself. Anything rising higher
    than the gear's height minus 0.3 m is in the way (the gear is the lowest part when landing)."""
    m = await bridge.call("grid.motion", {"gridId": ship_id, "thrusters": True})
    rel = [sub(c, gear["position"]) for c in m["corners"]]
    u = normalize(cross(normal, [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0]))
    v = cross(normal, u)
    us, vs = [dot(r, u) for r in rel], [dot(r, v) for r in rel]
    top = max(dot(r, normal) for r in rel) + clearance + 2
    found: list[dict[str, Any]] = []
    a = min(us) - margin
    while a <= max(us) + margin:
        b = min(vs) - margin
        while b <= max(vs) + margin:
            q = add(add(spot, u, a), v, b)
            r = await bridge.call("los.check", {"from": add(q, normal, top), "to": add(q, normal, -1.0), "skip": 0.0,
                                                "ignoreId": ship_id})
            h = r.get("hit")
            if h:
                height = top - h["fromStart"]
                if height > clearance - 0.3:
                    found.append({"height": round(height, 2), "at": [round(a, 1), round(b, 1)],
                                  "what": (h.get("block") or {}).get("name") or h.get("entity")})
            b += step
        a += step
    return found
