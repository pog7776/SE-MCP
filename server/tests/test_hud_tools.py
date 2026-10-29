from __future__ import annotations

from se_mcp import hud


def base(**over) -> dict:
    h = {
        "id": "1360001", "name": "Red Fighter", "size": "small", "isStatic": False, "grids": 1, "blocks": 612,
        "mass": 36100, "speed": 0.0, "distance": 4, "pilot": None, "dampeners": True, "handbrake": False, "autopilot": [],
        "thrusters": {"count": 48, "on": 48, "working": 48}, "gyros": {"count": 2, "on": 2, "working": 2, "overriding": 0},
        "thrustOverrides": 0,
        "batteries": {"count": 1, "working": 1, "storedMWh": 0.1, "maxMWh": 0.1, "inMW": 0, "outMW": 0, "recharge": 0, "discharge": 0},
        "reactors": {"count": 5, "working": 5, "outMW": 0, "maxMW": 2.5, "uraniumKg": 25},
        "solar": {"count": 0, "outMW": 0}, "hydrogen": {"count": 3, "on": 3, "fill": 100, "stockpile": 0}, "oxygen": None,
        "cargo": {"containers": 0, "fill": 0}, "connectors": {"count": 1, "connected": 1, "connectable": 0},
        "docked": [{"connector": "Connector", "to": "Connector 3", "ship": "Red Ship", "shipId": "7530001"}],
        "gearLocked": 0, "gear": 4, "antennas": [{"name": "Antenna", "on": True, "working": True, "broadcasting": True, "radius": 3000}],
        "beacons": [], "weapons": {"count": 3, "working": 3, "turrets": 0, "targeting": 0, "shooting": 0}, "damaged": 0,
        "subgrids": [],
    }
    h.update(over)
    return h


def test_thrusters_off_while_docked_is_normal():
    text = hud.hud_text(base(thrusters={"count": 48, "on": 0, "working": 0}))
    assert "! 48/48 thrusters OFF (normal while parked" in text
    assert "Docked: Connector -> Connector 3 on Red Ship" in text


def test_thrusters_off_in_flight_is_a_problem():
    text = hud.hud_text(base(docked=[], thrusters={"count": 48, "on": 40, "working": 40}))
    assert "8/48 thrusters OFF: the ship can't manoeuvre that way" in text


def test_subgrids_show_their_joint():
    h = base(isStatic=True, name="Red Platform", docked=[], grids=3, subgrids=[
        {"id": "1", "name": "Drawbridge", "size": "large", "blocks": 46,
         "joint": {"name": "Hinge 3", "kind": "hinge", "onGrid": "Red Platform", "working": True, "angleDeg": -90, "locked": False}},
        {"id": "2", "name": "Solar Panel Array", "size": "large", "blocks": 49,
         "joint": {"name": "Hinge", "kind": "hinge", "onGrid": "Solar Array Hinge", "working": True, "angleDeg": -27.9, "locked": False}}])
    text = hud.hud_text(h)
    assert text.startswith("RED PLATFORM: station, 4 m away, 3 grids")
    assert "subgrid Drawbridge (large, 46 blocks) on hinge 'Hinge 3' at -90 deg" in text
    assert "on hinge 'Hinge' at -27.9 deg (mounted on Solar Array Hinge)" in text


def test_recharge_with_no_other_power_warns():
    h = base(batteries={"count": 2, "working": 2, "storedMWh": 5, "maxMWh": 6, "inMW": 0, "outMW": 0, "recharge": 2, "discharge": 0},
             reactors={"count": 0, "working": 0, "outMW": 0, "maxMW": 0, "uraniumKg": 0})
    assert "All batteries on Recharge and nothing else makes power" in hud.hud_text(h)
