from __future__ import annotations

import pytest

from se_mcp import combat, radio
from se_mcp.resolve import ResolveError


def signal(name: str, entity: str, distance: float, **extra) -> dict:
    return {"antennaId": entity + "1", "entityId": entity, "name": name, "gridName": extra.pop("gridName", name),
            "kind": extra.pop("kind", "antenna"), "body": extra.pop("body", "large ship"),
            "relation": extra.pop("relation", "nobody"), "position": [0, 0, distance], "distance": distance,
            "radius": 10000, "direct": extra.pop("direct", True), "ownShip": extra.pop("ownShip", False), **extra}


HEARD = {"listener": "Red Ship", "listenerKind": "ship", "signals": [
    signal("RED.Red Ship", "1000001", 5, ownShip=True, relation="owner"),
    signal("Blue Ship", "2000001", 1146, speed=0.0),
    signal("IMEX.Mining Carriage", "3000001", 10521, kind="beacon", gridName="Mining Carriage",
           relation="neutral", speed=20.2, direct=False),
]}


def test_signals_text_hides_own_ship_and_cleans_names():
    text = radio.signals_text(HEARD)
    assert "Red Ship (" not in text.split("\n", 1)[1]
    assert "- IMEX.Mining Carriage (beacon on Mining Carriage, large ship, neutral): 10,521 m, moving 20.2 m/s, relayed" in text


def test_find_signal_by_ship_name():
    assert radio.find_signal(HEARD, "mining carriage")["entityId"] == "3000001"
    with pytest.raises(ResolveError):
        radio.find_signal(HEARD, "Red Ship")  # our own ship isn't a target


def test_contacts_merge_radio_turrets_and_sensors():
    w = {"weapons": [{"name": "Gatling Turret", "target": {"id": "2000001", "name": "Blue Ship", "type": "LargeGrid",
                                                            "relation": "nobody", "distance": 1140, "speed": 0}}]}
    sensed = {"sensors": [{"name": "Sensor", "detected": [{"id": "4000001", "name": "Pirate", "type": "CharacterHuman",
                                                           "relation": "enemies", "distance": 12, "speed": 3}]}]}
    text = combat.contacts_text("Red Ship", HEARD, w, sensed, include_friendly=True)
    lines = text.split("\n")
    assert lines[1].startswith("- Pirate (enemies)")  # hostile first
    assert "via radio (antenna); targeted by Gatling Turret" in text
