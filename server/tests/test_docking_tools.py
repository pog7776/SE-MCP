from __future__ import annotations

import pytest

from se_mcp import docking


def thruster(tid: str, push: list[float], thrust: float) -> dict:
    return {"id": tid, "push": push, "maxThrust": thrust, "working": True}


def test_thrust_split_uses_the_thrusters_that_push_that_way():
    ts = [thruster("1", [1, 0, 0], 1000), thruster("2", [1, 0, 0], 1000), thruster("3", [-1, 0, 0], 500),
          thruster("4", [0, 1, 0], 800)]
    out = docking.thrust_split([1000, -400, 0], ts)
    assert out["1"] == out["2"] == 0.5        # 1000 N over 2000 N pushing +x
    assert out["3"] == 0.0 and out["4"] == 0.0  # nothing wanted along -x or +y
    assert docking.thrust_split([0, 5000, 0], ts)["4"] == 1.0  # capped at full


def test_pick_prefers_free_connectors_and_matches_names():
    cs = [{"id": "5000003", "name": "Connector 3", "working": True, "status": "Connected"},
          {"id": "5000004", "name": "Connector 4", "working": True, "status": "Unconnected"}]
    assert docking.pick(cs, None, True, "Red Ship")["name"] == "Connector 4"
    assert docking.pick(cs, "connector 3", True, "Red Ship")["name"] == "Connector 3"
    assert docking.pick(cs, "5000004", True, "Red Ship")["name"] == "Connector 4"  # by block id
    with pytest.raises(ValueError):
        docking.pick(cs, "Connector 9", True, "Red Ship")


def test_status_text():
    r = {"gridName": "Red Fighter", "isStatic": False, "mergeBlocks": [],
         "connectors": [{"name": "Connector", "gridName": "Red Fighter", "size": "small", "working": True, "status": "Connected",
                         "other": {"name": "Connector 3", "gridName": "Red Ship"}}],
         "landingGear": [{"name": "Landing Gear", "mode": "Unlocked", "autoLock": True}]}
    text = docking.status_text(r)
    assert "- Connector (small connector): Connected to Connector 3 on Red Ship" in text
    assert "- Landing Gear (landing gear): Unlocked, auto-lock" in text
