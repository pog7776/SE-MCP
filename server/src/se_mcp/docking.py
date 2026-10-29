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
from .steering import RAD_S_TO_RPM, Steering, angle_between, cross, dot, length, normalize

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
        for i in ids:
            out[i] = round(frac, 4)
    return out


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

    async def stop(self, docked: bool = False) -> None:
        """Release every override and turn the dampeners back on; once docked, thrusters go back off if that's
        how the ship was parked."""
        params: dict[str, Any] = {"gridId": self.ship_id, "release": True, "dampeners": True}
        if docked and (not getattr(self, "thrusters_were_on", True) or self.ship_id in PARKED_WITH_THRUSTERS_OFF):
            params["thrustersOn"] = False
            PARKED_WITH_THRUSTERS_OFF.discard(self.ship_id)
        await self.bridge.call("flight.set", params)

    async def hold(self, distance: float, tolerance: float = 0.15, timeout_s: float = 90,
                   until: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
        """Fly the connector to `distance` meters out along the target's axis; return the connector when there
        (position within tolerance, nearly stopped and lined up) or when until(connector) is true."""
        point = add(self.target["position"], self.target["forward"], distance)
        facing = scale(self.target["forward"], -1)
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        last_log = 0.0
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
            want_v = clamp_length(scale(error, 0.4), self.max_speed)
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
            if now > end:
                raise TimeoutError(f"still {length(error):.2f} m and {math.degrees(angle):.1f} deg off after {timeout_s:.0f} s")


def status_text(r: dict[str, Any]) -> str:
    lines = [f"{r['gridName']}{' (station)' if r['isStatic'] else ''} docking:"]
    for c in r["connectors"]:
        where = f" on {c['gridName']}" if c["gridName"] != r["gridName"] else ""
        state = c["status"] if c["working"] else "NOT WORKING"
        to = f" to {c['other']['name']} on {c['other']['gridName']}" if c.get("other") else ""
        lines.append(f"- {c['name']}{where} ({c['size']} connector): {state}{to}")
    for g in r["landingGear"]:
        lines.append(f"- {g['name']} (landing gear): {g['mode']}{', auto-lock' if g['autoLock'] else ''}")
    for m in r["mergeBlocks"]:
        lines.append(f"- {m['name']} (merge block): {'merged' if m['connected'] else 'not merged'}{'' if m['enabled'] else ', off'}")
    if len(lines) == 1:
        lines.append("  no connectors, landing gear or merge blocks")
    return "\n".join(lines)


def pick(connectors: list[dict[str, Any]], name: str | None, want_free: bool, what: str) -> dict[str, Any]:
    pool = [c for c in connectors if c["working"]]
    if name:
        found = [c for c in pool if c["name"].lower() == name.lower()] or [c for c in pool if name.lower() in c["name"].lower()]
        if not found:
            raise ValueError(f"No working connector '{name}' on {what}. Connectors: {', '.join(c['name'] for c in connectors)}")
        return found[0]
    free = [c for c in pool if c["status"] != "Connected"] if want_free else pool
    if not free:
        raise ValueError(f"No free connector on {what}.")
    return free[0]
