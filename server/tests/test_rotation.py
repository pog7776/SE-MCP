from __future__ import annotations

import math

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
