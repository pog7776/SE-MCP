from __future__ import annotations

from typing import Any

from se_mcp import combat
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

TARGETS = {"enemies": True, "neutrals": False, "characters": True, "largeGrids": True, "smallGrids": True,
           "stations": True, "missiles": False, "meteors": False}

WEAPONS = {
    "gridName": "Red Ship",
    "weapons": [
        {"id": "7000001", "name": "Interior Turret", "type": "LargeInteriorTurret", "kind": "turret", "gridName": "Red Ship",
         "enabled": True, "working": True, "shooting": False, "magazines": ["RifleMag", "NATO"], "loaded": "RifleMag",
         "magazinesInside": 4, "ai": True, "underControl": False, "hasTarget": True, "aimed": True,
         "target": {"id": "9000001", "name": "Pirate", "type": "SmallGrid", "relation": "enemies", "distance": 220,
                    "size": [10, 5, 12], "speed": 30, "position": [0, 0, 220], "velocity": [0, 0, -30]},
         "range": 600, "targets": TARGETS, "targetingGroup": None},
        {"id": "7000002", "name": "Gatling Turret", "type": "", "kind": "turret", "gridName": "Red Ship", "enabled": True,
         "working": True, "shooting": False, "magazines": ["Gatling"], "loaded": "Gatling", "magazinesInside": 0,
         "ai": True, "underControl": False, "hasTarget": False, "aimed": False, "target": None, "range": 800,
         "targets": TARGETS, "targetingGroup": "Propulsion"},
        {"id": "7000003", "name": "Artillery", "type": "LargeBlockLargeCalibreGun", "kind": "fixed", "gridName": "Red Ship",
         "enabled": True, "working": True, "shooting": False, "magazines": ["Shell"], "loaded": "Shell", "magazinesInside": 0},
    ],
    "ammo": {"RifleMag": {"name": "MR-50A Rifle Magazine", "rounds": 50, "carried": 4},
             "NATO": {"name": "5.56x45mm NATO magazine", "rounds": 30, "carried": 0},
             "Gatling": {"name": "Gatling Ammo Box", "rounds": 140, "carried": 2},
             "Shell": {"name": "Artillery Shell", "rounds": 1, "carried": 0}},
}


def test_weapons_text():
    text = combat.weapons_text(WEAPONS)
    assert "TARGET: Pirate (SmallGrid, enemies) 220 m away" in text
    assert "aims at Propulsion" in text
    assert "Gatling Ammo Box: 2 on board (280 rounds)" in text
    assert text.endswith("Out of ammo: Artillery")


def test_pick_by_name_and_kind():
    assert [w["name"] for w in combat.pick(WEAPONS["weapons"], "all", ("turret",))] == ["Interior Turret", "Gatling Turret"]
    assert combat.pick(WEAPONS["weapons"], "gatling")[0]["id"] == "7000002"


def test_damage_text():
    r = {"gridName": "Red Ship", "blocks": 100, "damaged": [
        {"name": "Thruster", "type": "Thrust", "integrity": 35, "functional": False, "deformed": False, "missing": {"Thrust": 4}},
        {"name": "Light Armor Block", "type": "CubeBlock", "integrity": 80, "functional": None, "deformed": True, "missing": {}}]}
    text = combat.damage_text(r)
    assert "2 of 100 blocks damaged or unfinished, 1 not working." in text
    assert "- Thruster (Thrust): 35% NOT WORKING; needs 4 Thrust" in text


async def _call(handlers: dict[str, Any], tool: str, args: dict[str, Any]) -> str:
    async with FakeGame(handlers) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
        finally:
            await bridge.close()
    return "\n".join(c.text for c in result.content)


async def test_load_magazine_refuses_ammo_not_on_board():
    h = default_handlers()
    h["weapons.get"] = lambda p: WEAPONS
    calls: list[dict[str, Any]] = []
    h["weapon.magazine"] = lambda p: calls.append(p) or {"switched": True, "before": "RifleMag"}
    text = await _call(h, "load_magazine", {"weapon": "Interior Turret", "magazine": "5.56"})
    assert "No 5.56x45mm NATO magazine on board" in text and not calls
    text = await _call(h, "load_magazine", {"weapon": "Gatling", "magazine": "box"})
    assert text == "Gatling Turret only takes Gatling Ammo Box."


async def test_set_turrets_sends_only_given_flags():
    h = default_handlers()
    h["weapons.get"] = lambda p: WEAPONS
    sent: list[dict[str, Any]] = []
    h["turret.set"] = lambda p: sent.append(p) or {"changed": ["Interior Turret"]}
    await _call(h, "set_turrets", {"turrets": "Interior", "characters": True, "large_grids": False, "range_m": 400})
    assert sent[0] == {"blockIds": ["7000001"], "reset": False, "targets": {"characters": True, "largeGrids": False}, "range": 400}


async def test_track_target_follows_a_moving_hit():
    h = default_handlers()
    h["grid.motion"] = lambda p: {"center": [0, 0, 0]}
    fixes = iter([[0, 0, 200], [0, 0, 190], [0, 0, 180]])
    guesses: list[list[float]] = []

    def track(p: dict[str, Any]) -> dict[str, Any]:
        guesses.append(p["position"])
        pos = next(fixes, [0, 0, 180])
        return {"camera": "Front", "lost": False, "hit": {"id": "9000001", "name": "Pirate", "type": "SmallGrid",
                "relation": "enemies", "distance": pos[2], "size": [10, 5, 12], "speed": 10, "position": pos, "velocity": [0, 0, -10]}}

    h["camera.track"] = track
    text = await _call(h, "track_target", {"at": "0, 0, 200", "seconds": 0.6, "interval_s": 0.25})
    assert "Tracked Pirate (SmallGrid, enemies)" in text
    assert "closing at" in text
    assert guesses[1][2] < 200  # the second scan aims where the target should have moved to
