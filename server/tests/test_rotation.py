from __future__ import annotations

import math
import time

import pytest

from se_mcp import steering
from se_mcp.bridge import Bridge
from se_mcp.steering import angle_between, attitude_error, frame_of, turned_frame

from .fake_bridge import TOKEN, FakeGame
from .test_steering import SHIP, SimShip

FRAME = {"center": [0, 0, 0], "forward": [0.0, 0.0, -1.0], "up": [0.0, 1.0, 0.0], "right": [1.0, 0.0, 0.0]}


def close(a, b, tol=1e-6) -> bool:
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_turned_frame_matches_what_a_pilot_means():
    assert close(turned_frame(FRAME, yaw=90)[0], FRAME["right"])          # yaw right: nose to the right
    assert close(turned_frame(FRAME, pitch=90)[0], FRAME["up"])           # pitch up: nose up
    f, u = turned_frame(FRAME, roll=90)                                    # roll right: top tips right
    assert close(f, FRAME["forward"]) and close(u, FRAME["right"])
    assert close(turned_frame(FRAME, yaw=180)[0], [0, 0, 1])


@pytest.mark.parametrize("yaw,pitch,roll", [(90, 0, 0), (180, 0, 0), (0, 0, 180), (30, -45, 60), (0, 179.9, 0)])
def test_attitude_error_finds_the_turn(yaw, pitch, roll):
    f, u = turned_frame(FRAME, yaw, pitch, roll)
    axis, angle = attitude_error(FRAME["forward"], FRAME["up"], f, u)
    # Turning the start frame about the axis by the angle lands on the goal.
    assert close(steering.rotate(FRAME["forward"], axis, math.degrees(angle)), f, 1e-6)
    assert close(steering.rotate(FRAME["up"], axis, math.degrees(angle)), u, 1e-6)


class CentredShip(SimShip):
    def handlers(self):
        h = super().handlers()
        motion = h["grid.motion"]
        h["grid.motion"] = lambda p: {**motion(p), "center": [0.0, 0.0, 0.0], "cockpit": None}
        return h


async def test_rotate_ship_turns_around_and_lets_go():
    ship = CentredShip()
    async with FakeGame(ship.handlers()) as game:
        text = await steering.rotate_ship(Bridge(port=game.port, token=TOKEN), SHIP, yaw=180)
    assert text.startswith("Turned 180 deg"), text
    assert close(ship.basis["forward"], [0, 0, 1], 0.03) and close(ship.basis["up"], [0, 1, 0], 0.03)
    assert ship.overrides["Override"] is False


async def test_rotate_ship_rolls_without_moving_the_nose():
    ship = CentredShip()
    async with FakeGame(ship.handlers()) as game:
        await steering.rotate_ship(Bridge(port=game.port, token=TOKEN), SHIP, roll=-90)
    assert close(ship.basis["forward"], [0, 0, -1], 0.03) and close(ship.basis["up"], [-1, 0, 0], 0.03)


async def test_point_ship_aims_a_side_at_a_point_even_straight_behind():
    ship = CentredShip()
    async with FakeGame(ship.handlers()) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        text = await steering.point_ship(bridge, SHIP, [0, 0, 500], [0, 0, 1], [0, 0, 0])
        assert "target 500 m away" in text, text
        assert angle_between(ship.basis["forward"], [0, 0, 1]) < math.radians(1.5)
        again = await steering.point_ship(bridge, SHIP, [0, 0, 500], [0, 0, 1], [0, 0, 0])
    assert again.startswith("Already pointing at it")


async def test_turns_refuse_on_stations_and_in_gravity():
    async with FakeGame(CentredShip(static=True).handlers()) as game:
        assert "station" in await steering.rotate_ship(Bridge(port=game.port, token=TOKEN), SHIP, yaw=10)
    async with FakeGame(CentredShip(gravity=0.8).handlers()) as game:
        assert "force=True" in await steering.point_ship(Bridge(port=game.port, token=TOKEN), SHIP, [1, 0, 0], [0, 0, 1], [0, 0, 0])


def test_frame_prefers_the_cockpit():
    m = {"center": [1, 2, 3], "forward": [0, 0, -1], "up": [0, 1, 0], "right": [1, 0, 0],
         "cockpit": {"name": "Helm", "forward": [1, 0, 0], "up": [0, 1, 0]}}
    f = frame_of(m)
    assert f["ref"] == "Helm" and close(f["right"], [0, 0, 1])


def test_turn_speed_never_outruns_the_brakes():
    # Far out it's the speed cap; near the goal, the proportional ease-in.
    assert steering.turn_speed(3.0, 0.5) == 0.5
    assert steering.turn_speed(0.1, 0.5) == pytest.approx(0.06)
    # A weak ship (0.1 rad/s^2) is held to what it can stop from: v^2/2a (plus the loop's delay) within the angle.
    v = steering.turn_speed(0.5, 2.0, accel=0.1, delay=0.3, gain=1.0)
    a = 0.1 * 0.7
    assert v * 0.3 + v * v / (2 * a) == pytest.approx(0.5)
    assert v < steering.turn_speed(0.5, 2.0, gain=1.0)


def test_spin_up_reads_the_acceleration_until_near_full_speed():
    s = steering.SpinUp()
    s.sample(0.0, 0.0, 0.0)
    s.sample(0.5, 0.1, 1.0)
    assert s.accel == pytest.approx(0.2) and not s.done
    s.sample(1.0, 0.2, 1.0)
    s.sample(4.0, 0.8, 1.0)
    assert s.accel == pytest.approx(0.2) and s.done
    s.sample(5.0, 0.0, 1.0)  # braking later doesn't change it
    assert s.accel == pytest.approx(0.2)


class HeavyShip(CentredShip):
    """Gyros too weak for the ship: spin changes at most ACCEL rad/s^2 (in wall-clock time) toward the overrides."""

    ACCEL = 0.1

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.w = [0.0, 0.0, 0.0]
        self.t = time.monotonic()

    def omega(self):
        return self.w

    def step(self):
        now = time.monotonic()
        dt, self.t = now - self.t, now
        want = SimShip.omega(self)
        diff = [want[i] - self.w[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in diff))
        k = min(1.0, self.ACCEL * dt / n) if n > 0 else 0
        self.w = [self.w[i] + diff[i] * k for i in range(3)]
        speed = math.sqrt(sum(x * x for x in self.w))
        if speed > 0:
            axis = [x / speed for x in self.w]
            for name in self.basis:
                self.basis[name] = steering.rotate(self.basis[name], axis, math.degrees(speed * dt))


async def test_heavy_ship_brakes_in_time_instead_of_overshooting():
    ship = HeavyShip()
    async with FakeGame(ship.handlers()) as game:
        text = await steering.rotate_ship(Bridge(port=game.port, token=TOKEN), SHIP, yaw=90, max_rpm=6)
    assert "gains spin at" in text and "overshot" not in text, text
    assert angle_between(ship.basis["forward"], [1, 0, 0]) < math.radians(1.5)
