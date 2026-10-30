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


def perpendicular(v: Vec) -> Vec:
    """Some unit vector at right angles to v."""
    helper = [1.0, 0.0, 0.0] if abs(v[0]) < 0.9 else [0.0, 1.0, 0.0]
    return normalize(cross(v, helper))


def rotate(v: Vec, axis: Vec, degrees: float) -> Vec:
    """v turned about axis by degrees, right-handed (Rodrigues)."""
    k, t = normalize(axis), math.radians(degrees)
    kxv, kdv = cross(k, v), dot(k, v)
    return [v[i] * math.cos(t) + kxv[i] * math.sin(t) + k[i] * kdv * (1 - math.cos(t)) for i in range(3)]


def frame_of(m: dict) -> dict:
    """Which way is forward/up/right on a ship, from grid.motion: the cockpit's view if it has one, else the grid's
    own axes (stations). center is the middle of the ship's bounding box."""
    cp = m.get("cockpit")
    if cp:
        return {"center": m["center"], "forward": cp["forward"], "up": cp["up"], "right": cross(cp["forward"], cp["up"]),
                "ref": cp["name"]}
    return {"center": m["center"], "forward": m["forward"], "up": m["up"], "right": m["right"], "ref": None}


def turned_frame(frame: dict, yaw: float = 0, pitch: float = 0, roll: float = 0) -> tuple[Vec, Vec]:
    """Forward and up after turning yaw degrees right, then pitch nose up, then roll right, as the pilot sees it."""
    f, u = frame["forward"], frame["up"]
    if yaw:
        f, u = rotate(f, u, -yaw), u
    if pitch:
        r = cross(f, u)
        f, u = rotate(f, r, pitch), rotate(u, r, pitch)
    if roll:
        f, u = f, rotate(u, f, roll)
    return normalize(f), normalize(u)


def attitude_error(forward: Vec, up: Vec, target_forward: Vec, target_up: Vec) -> tuple[Vec, float]:
    """The world axis and angle (radians) of the shortest turn from one orientation to another. Works for any
    angle, 180 degrees included, through the quaternion of R = target * current^T."""
    fc, uc = normalize(forward), normalize(up)
    ft, ut = normalize(target_forward), normalize(target_up)
    # Re-orthogonalise up against forward, so slightly skewed inputs still make a rotation.
    uc = normalize([uc[i] - dot(uc, fc) * fc[i] for i in range(3)])
    ut = normalize([ut[i] - dot(ut, ft) * ft[i] for i in range(3)])
    rc, rt = cross(fc, uc), cross(ft, ut)
    R = [[rt[i] * rc[j] + ut[i] * uc[j] + ft[i] * fc[j] for j in range(3)] for i in range(3)]
    trace = R[0][0] + R[1][1] + R[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        w, x, y, z = 0.25 * s, (R[2][1] - R[1][2]) / s, (R[0][2] - R[2][0]) / s, (R[1][0] - R[0][1]) / s
    elif R[0][0] > R[1][1] and R[0][0] > R[2][2]:
        s = math.sqrt(1 + R[0][0] - R[1][1] - R[2][2]) * 2
        w, x, y, z = (R[2][1] - R[1][2]) / s, 0.25 * s, (R[0][1] + R[1][0]) / s, (R[0][2] + R[2][0]) / s
    elif R[1][1] > R[2][2]:
        s = math.sqrt(1 + R[1][1] - R[0][0] - R[2][2]) * 2
        w, x, y, z = (R[0][2] - R[2][0]) / s, (R[0][1] + R[1][0]) / s, 0.25 * s, (R[1][2] + R[2][1]) / s
    else:
        s = math.sqrt(1 + R[2][2] - R[0][0] - R[1][1]) * 2
        w, x, y, z = (R[1][0] - R[0][1]) / s, (R[0][2] + R[2][0]) / s, (R[1][2] + R[2][1]) / s, 0.25 * s
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    v = [x, y, z]
    angle = 2 * math.atan2(length(v), w)
    return (normalize(v) if length(v) > 1e-9 else [0.0, 0.0, 0.0]), angle


class SteeringError(Exception):
    pass


def turn_speed(angle: float, max_speed: float, accel: float | None = None, delay: float = 0.0,
               gain: float = 0.6, margin: float = 0.7) -> float:
    """How fast to spin (rad/s) with `angle` radians to go: slowing in proportion near the goal (gain), and never
    faster than the ship can brake from in time: at `accel` rad/s^2 (times a safety margin), after the control
    loop's `delay` seconds of carrying on before it reacts."""
    speed = min(gain * angle, max_speed)
    if accel:
        a = accel * margin
        speed = min(speed, a * (math.sqrt(delay * delay + 2 * angle / a) - delay))
    return speed


@dataclass
class SpinUp:
    """How fast a ship picks up spin while it speeds up at the start of a turn (rad/s^2). Gyros brake as hard as
    they accelerate, so this is the braking rate too. Measured from start to the last sample still well short of
    the commanded speed; a ship that gets there within a step reads low, which only makes braking gentler."""

    start: tuple[float, float] | None = None
    accel: float | None = None
    done: bool = False

    def sample(self, t: float, speed: float, commanded: float) -> None:
        if self.done:
            return
        if self.start is None:
            self.start = (t, speed)
            return
        t0, s0 = self.start
        if t - t0 >= 0.1 and speed > s0:
            self.accel = (speed - s0) / (t - t0)
        if commanded > 0 and speed >= 0.7 * commanded:
            self.done = True


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
    spin_up: SpinUp = field(default_factory=SpinUp)
    overshoot_deg: float = 0.0

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

        async def error() -> tuple[Vec, float, dict]:
            vector, target = await current_direction()
            m = await self.motion()
            # Spin about vector x target. Pointing straight away has no cross product, so any axis at right angles does.
            axis = cross(vector, target)
            return (normalize(axis) if length(axis) > 1e-3 else perpendicular(vector)), angle_between(vector, target), m

        return await self._turn(error, max_rpm, tolerance_deg, timeout_s)

    async def orient(
        self,
        target: Callable[[dict], tuple[Vec, Vec, Vec, Vec]],
        max_rpm: float = 2.0,
        tolerance_deg: float = 1.0,
        timeout_s: float = 180,
    ) -> float:
        """Turn until target(grid.motion) (returns current forward, up, wanted forward, up) line up: forward and
        roll both. Returns the final angle in degrees."""

        async def error() -> tuple[Vec, float, dict]:
            m = await self.motion()
            axis, angle = attitude_error(*target(m))
            return axis, angle, m

        return await self._turn(error, max_rpm, tolerance_deg, timeout_s)

    async def _turn(self, error: Callable[[], Awaitable[tuple[Vec, float, dict]]], max_rpm: float,
                    tolerance_deg: float, timeout_s: float) -> float:
        """The turn loop: error() gives the axis and angle still to turn. Spins up as fast as allowed, measuring how
        quickly the ship gains spin, then follows a braking curve in (turn_speed). Records spin_up and overshoot_deg."""
        max_speed = max_rpm / RAD_S_TO_RPM
        began = time.monotonic()
        deadline = began + timeout_s
        commanded, closest, last = 0.0, math.inf, began
        while True:
            axis, angle, m = await error()
            now = time.monotonic()
            delay, last = max(now - last, self.step_s), now  # how long a command stands before the next one
            w_now = m["angularVelocity"]
            if math.degrees(angle) < tolerance_deg and length(w_now) < 0.01:
                return math.degrees(angle)
            if now > deadline:
                raise SteeringError(f"Gave up after {timeout_s:.0f} s, still {math.degrees(angle):.1f} deg off.")
            # Past the goal, the angle grows again from the closest it got.
            closest = min(closest, angle)
            if closest < math.radians(3 * tolerance_deg):
                self.overshoot_deg = max(self.overshoot_deg, math.degrees(angle - closest))
            self.spin_up.sample(now - began, dot(w_now, axis), commanded)
            accel = self.spin_up.accel if self.spin_up.done else None
            commanded = turn_speed(angle, max_speed, accel, delay, gain=1.0 if accel else 0.6)
            w = [x * commanded for x in axis]
            gyros_now = {g["id"]: g for g in m["gyros"]}
            for g in self.gyros:
                await self._set(g["id"], **self.commands(w, gyros_now.get(g["id"], g)))
            await asyncio.sleep(self.step_s)

    def report(self) -> str:
        """How the turn went, for the result text: the measured spin-up rate and any overshoot."""
        parts = []
        if self.spin_up.accel:
            parts.append(f"It gains spin at {math.degrees(self.spin_up.accel):.1f} deg/s^2")
        if self.overshoot_deg >= 1:
            parts.append(f"overshot by {self.overshoot_deg:.1f} deg")
        text = ", ".join(parts)
        return f" {text[:1].upper()}{text[1:]}." if parts else ""


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


def refuse_turn(motion: dict, force: bool) -> str | None:
    """Why a ship shouldn't be turned now, if there's a reason."""
    if motion["isStatic"]:
        return "That's a station (static grid); it can't be rotated."
    if motion["gravityG"] > 0.05 and not force:
        return (f"The ship is in {motion['gravityG']:.2f} g of natural gravity. Turning it could tip it over or crash it, "
                "so I won't unless you confirm (force=True).")
    return None


def to_local(v: Vec, frame: dict) -> Vec:
    """A world vector as (right, up, forward) parts in the ship's frame."""
    return [dot(v, frame["right"]), dot(v, frame["up"]), dot(v, frame["forward"])]


def to_world(local: Vec, frame: dict) -> Vec:
    return [local[0] * frame["right"][i] + local[1] * frame["up"][i] + local[2] * frame["forward"][i] for i in range(3)]


async def rotate_ship(bridge: Bridge, grid_id: str, yaw: float = 0, pitch: float = 0, roll: float = 0,
                      max_rpm: float = 2.0, force: bool = False, tolerance_deg: float = 1.0) -> str:
    """Turn a ship by yaw/pitch/roll degrees from where it points now (as its pilot sees it), then let go."""
    motion = await bridge.call("grid.motion", {"gridId": grid_id})
    if why := refuse_turn(motion, force):
        return why
    start = frame_of(motion)
    goal_forward, goal_up = turned_frame(start, yaw, pitch, roll)
    _, total = attitude_error(start["forward"], start["up"], goal_forward, goal_up)
    if math.degrees(total) < tolerance_deg:
        return "That's no turn at all."

    def target(m: dict) -> tuple[Vec, Vec, Vec, Vec]:
        f = frame_of(m)
        return f["forward"], f["up"], goal_forward, goal_up

    steering = Steering(bridge, grid_id)
    try:
        await steering.take_control()
        await steering.calibrate(("Pitch", "Yaw", "Roll"))
        end = await steering.orient(target, max_rpm=max_rpm, tolerance_deg=tolerance_deg)
    except SteeringError as e:
        return f"Stopped: {e} Gyros released."
    finally:
        await steering.release()
    return f"Turned {math.degrees(total):.0f} deg (now {end:.1f} deg off the goal).{steering.report()} Gyros released."


async def point_ship(bridge: Bridge, grid_id: str, target: Vec, face_local: Vec, origin_local: Vec,
                     max_rpm: float = 2.0, force: bool = False, tolerance_deg: float = 1.0) -> str:
    """Turn a ship until a direction fixed to it (face_local, in its right/up/forward frame, from origin_local
    relative to its center) points at a world position. Roll is left as it is."""
    motion = await bridge.call("grid.motion", {"gridId": grid_id})
    if why := refuse_turn(motion, force):
        return why

    async def direction() -> tuple[Vec, Vec]:
        f = frame_of(await bridge.call("grid.motion", {"gridId": grid_id}))
        origin = [f["center"][i] + to_world(origin_local, f)[i] for i in range(3)]
        return to_world(face_local, f), [target[i] - origin[i] for i in range(3)]

    vector, toward = await direction()
    start = math.degrees(angle_between(vector, toward))
    if start < tolerance_deg:
        return f"Already pointing at it ({start:.1f} deg off), {length(toward):.0f} m away."
    steering = Steering(bridge, grid_id)
    try:
        await steering.take_control()
        await steering.calibrate(("Pitch", "Yaw", "Roll"))
        end = await steering.point(direction, max_rpm=max_rpm, tolerance_deg=tolerance_deg)
    except SteeringError as e:
        return f"Stopped: {e} Gyros released."
    finally:
        await steering.release()
    return f"Turned {start:.0f} deg; now {end:.1f} deg off, target {length(toward):.0f} m away.{steering.report()} Gyros released."


async def turn_to(bridge: Bridge, grid_id: str, current_direction: Callable[[], Awaitable[tuple[Vec, Vec]]],
                  max_rpm: float = 1.0, force: bool = False, tolerance_deg: float = 2.0) -> str:
    """Turn a ship until current_direction()'s ship vector points along its target vector, then release the gyros."""
    motion = await bridge.call("grid.motion", {"gridId": grid_id})
    if why := refuse_turn(motion, force):
        return why
    vector, target = await current_direction()
    start = math.degrees(angle_between(vector, target))
    if start < tolerance_deg:
        return f"Already lined up ({start:.1f} deg off)."
    steering = Steering(bridge, grid_id)
    try:
        await steering.take_control()
        await steering.calibrate(perpendicular_overrides(vector, steering.gyros[0]))
        end = await steering.point(current_direction, max_rpm=max_rpm, tolerance_deg=tolerance_deg)
    except SteeringError as e:
        return f"Stopped: {e} Gyros released."
    finally:
        await steering.release()
    return f"Turned the ship {start:.0f} deg (now {end:.1f} deg off).{steering.report()} Gyros released."
