from __future__ import annotations

import math
from typing import Any

import pytest

from se_mcp.bridge import Bridge
from se_mcp.steering import align_to_sun, angle_between, cross, dot, normalize, perpendicular_overrides

from .fake_bridge import TOKEN, FakeGame


SHIP = "1001"
GYRO = "50"
SIM_DT = 0.25  # simulated seconds per grid.motion call


def rotate(v: list[float], axis: list[float], angle: float) -> list[float]:
    """Rodrigues rotation of v about a unit axis."""
    c, s = math.cos(angle), math.sin(angle)
    k = axis
    kxv = cross(k, v)
    kdv = dot(k, v)
    return [v[i] * c + kxv[i] * s + k[i] * kdv * (1 - c) for i in range(3)]


class SimShip:
    """A rigid ship whose spin follows the gyro overrides instantly. The gyro is mounted rotated relative to the
    ship, and its override signs are awkward on purpose, so the controller has to calibrate to get it right."""

    def __init__(self, gravity: float = 0.0, static: bool = False, panels: bool = True):
        self.basis = {"right": [1.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0], "forward": [0.0, 0.0, -1.0]}
        self.sun = normalize([0.3, -0.2, -0.9])
        self.overrides = {"Override": False, "Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0}
        self.gravity, self.static, self.panels = gravity, static, panels
        self.calls: list[tuple[str, dict]] = []

    def gyro_axes(self) -> dict[str, list[float]]:
        # Gyro mounted on its side: its right is the ship's up, its up is the ship's forward.
        return {"right": self.basis["up"], "up": self.basis["forward"], "forward": self.basis["right"]}

    def omega(self) -> list[float]:
        if not self.overrides["Override"]:
            return [0.0, 0.0, 0.0]
        g = self.gyro_axes()
        rpm_to_rad = 2 * math.pi / 60
        # Hidden convention: Pitch spins about +right, Yaw about -up, Roll about +forward.
        w = [0.0, 0.0, 0.0]
        for prop, axis, sign in (("Pitch", "right", 1), ("Yaw", "up", -1), ("Roll", "forward", 1)):
            for i in range(3):
                w[i] += sign * self.overrides[prop] * rpm_to_rad * g[axis][i]
        return w

    def step(self) -> None:
        w = self.omega()
        speed = math.sqrt(dot(w, w))
        if speed > 0:
            axis = [x / speed for x in w]
            for name in self.basis:
                self.basis[name] = rotate(self.basis[name], axis, speed * SIM_DT)

    def handlers(self) -> dict[str, Any]:
        def motion(_: dict) -> dict:
            self.step()
            g = self.gyro_axes()
            return {
                "isStatic": self.static, "gravityG": self.gravity, **self.basis,
                "angularVelocity": self.omega(), "linearVelocity": [0, 0, 0],
                "gyros": [{"id": GYRO, "name": "Gyroscope", **g, "overriding": self.overrides["Override"], "working": True}],
            }

        def sun(_: dict) -> dict:
            normal = self.basis["up"]
            exposure = abs(dot(normal, self.sun))
            panels = [{"id": "60", "name": "Solar Panel", "normal": normal, "twoSided": True, "exposure": exposure,
                       "maxOutputMW": 0.16, "currentMaxMW": 0.16 * exposure}] if self.panels else []
            return {"sunDirection": self.sun, "panels": panels}

        def set_property(p: dict) -> dict:
            self.calls.append(("set", p))
            before = self.overrides[p["property"]]
            self.overrides[p["property"]] = p["value"]
            return {"property": p["property"], "changed": [{"block": "Gyroscope", "before": before, "after": p["value"]}], "skipped": []}

        return {"grid.motion": motion, "sun.info": sun, "block.set_property": set_property}


async def run_align(ship: SimShip, **kwargs: Any) -> str:
    async with FakeGame(ship.handlers()) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            return await align_to_sun(bridge, SHIP, step_s=0.0, pulse_s=0.0, **kwargs)
        finally:
            await bridge.close()


async def test_aligns_panels_with_the_sun_and_releases_gyros():
    ship = SimShip()
    start = math.degrees(angle_between(ship.basis["up"], ship.sun))
    text = await run_align(ship, max_rpm=6)
    end = math.degrees(min(angle_between(ship.basis["up"], ship.sun), angle_between([-x for x in ship.basis["up"]], ship.sun)))
    assert start > 20
    assert end < 1, text
    assert "Gyros released" in text
    assert ship.overrides == {"Override": False, "Pitch": 0.0, "Yaw": 0.0, "Roll": 0.0}


async def test_refuses_in_gravity_unless_forced():
    ship = SimShip(gravity=0.8)
    text = await run_align(ship)
    assert "natural gravity" in text
    assert not ship.calls  # never touched the gyros


async def test_refuses_stations():
    assert "station" in await run_align(SimShip(static=True))


async def test_no_panels():
    assert "No solar panels" in await run_align(SimShip(panels=False))


def test_perpendicular_overrides_skips_the_useless_axis():
    gyro = {"right": [1, 0, 0], "up": [0, 1, 0], "forward": [0, 0, -1]}
    # Turning the "up" direction: spinning about up (Yaw) can't help.
    assert perpendicular_overrides([0, 1, 0], gyro) == ("Pitch", "Roll")
    # A diagonal direction needs all three.
    assert perpendicular_overrides(normalize([1, 1, 1]), gyro) == ("Pitch", "Yaw", "Roll")
