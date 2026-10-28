"""Turn a ship with gyroscope overrides so a ship-fixed direction points at a world direction.

Used by align_to_sun. The plugin only exposes primitives (grid.motion, sun.info, block.set_property);
the control loop lives here so it can change without restarting the game.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .bridge import Bridge

Vec = list[float]
RAD_S_TO_RPM = 60 / (2 * math.pi)
AXES = ("right", "up", "forward")


def dot(a: Vec, b: Vec) -> float:
    return sum(x * y for x, y in zip(a, b))


def cross(a: Vec, b: Vec) -> Vec:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def length(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec) -> Vec:
    n = length(a)
    return [x / n for x in a] if n > 1e-9 else [0.0, 0.0, 0.0]


def angle_between(a: Vec, b: Vec) -> float:
    return math.atan2(length(cross(a, b)), dot(a, b))


class SteeringError(Exception):
    pass


@dataclass
class Steering:
    """Gyro override control for one grid. `convention` maps each override (Pitch/Yaw/Roll) to the gyro-local
    axis it spins about, with a sign; it's measured by calibrate() because the game's signs are not obvious."""

    bridge: Bridge
    grid_id: str
    step_s: float = 0.15
    pulse_s: float = 0.8
    convention: dict[str, tuple[str, int]] = field(default_factory=dict)
    gyros: list[dict] = field(default_factory=list)

    async def motion(self) -> dict:
        return await self.bridge.call("grid.motion", {"gridId": self.grid_id})

    async def _set(self, gyro_id: str, **values: float | bool) -> None:
        for prop, value in values.items():
            await self.bridge.call("block.set_property", {"blockId": gyro_id, "property": prop, "value": value})

    async def take_control(self) -> None:
        m = await self.motion()
        self.gyros = [g for g in m["gyros"] if g["working"]]
        if not self.gyros:
            raise SteeringError("No working gyroscopes I can use on this ship.")
        for g in self.gyros:
            await self._set(g["id"], Override=True, Pitch=0.0, Yaw=0.0, Roll=0.0)

    async def release(self) -> None:
        for g in self.gyros:
            await self._set(g["id"], Pitch=0.0, Yaw=0.0, Roll=0.0)
            await self._set(g["id"], Override=False)

    async def calibrate(self, props: tuple[str, ...]) -> None:
        """Briefly spin on each override and see which way the ship actually turns."""
        g = self.gyros[0]
        for prop in props:
            await self._set(g["id"], **{prop: 1.0})
            await asyncio.sleep(self.pulse_s)
            m = await self.motion()
            await self._set(g["id"], **{prop: 0.0})
            gyro = next(x for x in m["gyros"] if x["id"] == g["id"])
            w = m["angularVelocity"]
            comps = {axis: dot(w, gyro[axis]) for axis in AXES}
            axis = max(comps, key=lambda a: abs(comps[a]))
            if abs(comps[axis]) < 1e-4:
                raise SteeringError(f"The ship didn't turn when testing {prop}. Is it docked, landed or too heavy for its gyros?")
            self.convention[prop] = (axis, 1 if comps[axis] > 0 else -1)
            await asyncio.sleep(self.pulse_s)

    def commands(self, w: Vec, gyro: dict) -> dict[str, float]:
        """Override values (RPM) that make this gyro push toward world angular velocity w (rad/s)."""
        return {prop: round(sign * dot(w, gyro[axis]) * RAD_S_TO_RPM, 3) for prop, (axis, sign) in self.convention.items()}

    async def point(
        self,
        current_direction: Callable[[], Awaitable[tuple[Vec, Vec]]],
        max_rpm: float = 1.0,
        tolerance_deg: float = 0.7,
        timeout_s: float = 120,
    ) -> float:
        """Turn until current_direction() (returns ship vector, target vector) line up. Returns the final angle in degrees."""
        max_speed = max_rpm / RAD_S_TO_RPM
        deadline = time.monotonic() + timeout_s
        while True:
            vector, target = await current_direction()
            angle = angle_between(vector, target)
            m = await self.motion()
            if math.degrees(angle) < tolerance_deg and length(m["angularVelocity"]) < 0.01:
                return math.degrees(angle)
            if time.monotonic() > deadline:
                raise SteeringError(f"Gave up after {timeout_s:.0f} s, still {math.degrees(angle):.1f} deg off.")
            # Spin about vector x target, slowing down as the angle closes.
            speed = min(0.6 * angle, max_speed)
            w = [x * speed for x in normalize(cross(vector, target))]
            gyros_now = {g["id"]: g for g in m["gyros"]}
            for g in self.gyros:
                await self._set(g["id"], **self.commands(w, gyros_now.get(g["id"], g)))
            await asyncio.sleep(self.step_s)


def perpendicular_overrides(direction: Vec, gyro: dict) -> tuple[str, ...]:
    """Overrides worth calibrating to turn `direction`: skip the one spinning about it (it can't move it)."""
    spin_axis_of = {"Pitch": "right", "Yaw": "up", "Roll": "forward"}
    alignment = {prop: abs(dot(direction, gyro[axis])) for prop, axis in spin_axis_of.items()}
    useless = max(alignment, key=alignment.get)
    # If the direction isn't close to a gyro axis, all three help.
    return tuple(p for p in ("Pitch", "Yaw", "Roll") if p != useless or alignment[useless] < 0.9)


async def align_to_sun(bridge: Bridge, grid_id: str, max_rpm: float = 1.0, force: bool = False, **steering_options: float) -> str:
    motion = await bridge.call("grid.motion", {"gridId": grid_id})
    if motion["isStatic"]:
        return "That's a station (static grid); it can't be rotated."
    if motion["gravityG"] > 0.05 and not force:
        return (f"The ship is in {motion['gravityG']:.2f} g of natural gravity. Turning it could tip it over or crash it, "
                "so I won't unless you confirm (force=True).")

    async def panel_state() -> tuple[Vec, Vec, list[dict]]:
        sun = await bridge.call("sun.info", {"gridId": grid_id})
        panels = sun["panels"] or []
        if not panels:
            raise SteeringError("No solar panels on this ship.")
        normal = normalize([sum(p["normal"][i] for p in panels) for i in range(3)])
        target = sun["sunDirection"]
        if dot(normal, target) < 0 and all(p["twoSided"] for p in panels):
            normal = [-x for x in normal]  # two-sided panels: aim whichever face is closer
        return normal, target, panels

    async def direction() -> tuple[Vec, Vec]:
        normal, target, _ = await panel_state()
        return normal, target

    def output(panels: list[dict]) -> tuple[float, float]:
        return sum(p["currentMaxMW"] for p in panels) * 1000, sum(p["maxOutputMW"] for p in panels) * 1000

    try:
        normal, target, panels = await panel_state()
    except SteeringError as e:
        return str(e)
    start_angle = math.degrees(angle_between(normal, target))
    before_kw, max_kw = output(panels)
    if start_angle < 1:
        return f"Already facing the sun ({start_angle:.1f} deg off): {before_kw:.0f} of {max_kw:.0f} kW."

    steering = Steering(bridge, grid_id, **steering_options)
    try:
        await steering.take_control()
        await steering.calibrate(perpendicular_overrides(normal, steering.gyros[0]))
        end_angle = await steering.point(direction, max_rpm=max_rpm)
    except SteeringError as e:
        return f"Stopped: {e} Gyros released."
    finally:
        await steering.release()

    await asyncio.sleep(steering.step_s * 4)  # let the panels' output catch up
    _, _, panels = await panel_state()
    after_kw, _ = output(panels)
    return (f"Turned {start_angle:.0f} deg; the solar panels are now {end_angle:.1f} deg off the sun. "
            f"Solar output {before_kw:.0f} -> {after_kw:.0f} kW (max {max_kw:.0f} kW). Gyros released. "
            "The sun moves slowly, so this drifts over time.")
