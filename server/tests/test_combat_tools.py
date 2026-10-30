from __future__ import annotations

import math
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


def _target_handlers(los_hit: dict[str, Any] | None, stuck_deg: float | None = None) -> dict[str, Any]:
    """A Red Ship with one turret that swings onto whatever it's aimed at two polls later (or stops stuck_deg off)."""
    h = default_handlers()
    turret = {**WEAPONS["weapons"][1], "position": [0.0, 0.0, 0.0], "forward": [0.0, 0.0, -1.0], "up": [0.0, 1.0, 0.0],
              "aimDirection": [1.0, 0.0, 0.0]}
    aim = {"at": None, "polls": 0}

    def weapons_get(_: dict[str, Any]) -> dict[str, Any]:
        if aim["at"] is not None:
            aim["polls"] += 1
            if aim["polls"] >= 2:
                d = aim["at"]
                if stuck_deg is not None:
                    t = math.radians(stuck_deg)
                    d = [d[0] * math.cos(t) + abs(d[2]) * math.sin(t), d[1], d[2] * math.cos(t)]
                turret["aimDirection"] = d
        return {"gridName": "Red Ship", "ammo": {}, "weapons": [dict(turret)]}

    def turret_aim(p: dict[str, Any]) -> dict[str, Any]:
        aim["at"] = p["position"]
        return {"aimed": ["Gatling Turret"]}

    h["weapons.get"] = weapons_get
    h["block.get"] = lambda p: {
        "block": {"id": p["blockId"], "name": "Whale_Hydrogen Tank 2", "gridId": "2002"}, "gridName": "Whale",
        "position": [0.0, 0.0, -300.0], "forward": [1, 0, 0], "up": [0, 1, 0], "gridVelocity": [0.0, 0.0, 5.0]}
    h["grid.motion"] = lambda p: {"center": [0.0, 0.0, 0.0], "forward": [0.0, 0.0, -1.0], "up": [0.0, 1.0, 0.0],
                                  "right": [1.0, 0.0, 0.0], "cockpit": None, "isStatic": False, "gravityG": 0,
                                  "angularVelocity": [0, 0, 0], "gyros": []}
    h["turret.aim"] = turret_aim
    h["los.check"] = lambda p: {"clear": los_hit is None, "distance": 300, "hit": los_hit}
    h["weapon.fire"] = lambda p: {"fired": ["Gatling Turret"], "mode": p["mode"]}
    return h


async def test_target_block_aims_turrets_at_the_block_and_fires_through_its_armour():
    h = _target_handlers({"entity": "Whale", "entityId": "2002", "block": {"name": "Heavy Armor Block"}, "fromStart": 296})
    text = await _call(h, "target_block", {"block": "86099400851731085", "target_grid": "Whale", "weapons": "turrets",
                                           "fire": True})
    assert text.startswith("Target: Whale_Hydrogen Tank 2 on Whale, 300 m away.")
    assert "Leading it at 5 m/s." in text and "On target: Gatling Turret (within " in text
    assert "Gatling Turret's shots meet Heavy Armor Block first, 4 m before Whale_Hydrogen Tank 2." in text
    assert text.endswith("Fired one shot from Gatling Turret.")


async def test_target_block_holds_fire_when_another_ship_is_in_the_way():
    h = _target_handlers({"entity": "Blue Ship", "entityId": "5005", "block": {"name": "Armor"}, "fromStart": 120})
    text = await _call(h, "target_block", {"block": "86099400851731085", "target_grid": "Whale", "weapons": "turrets",
                                           "fire": True})
    assert "! Blue Ship is in Gatling Turret's line of fire (120 m out)." in text
    assert "Held fire on Gatling Turret" in text and "Fired one shot" not in text


async def test_target_block_can_aim_beside_it_for_a_warning_shot():
    aimed: list[dict[str, Any]] = []
    h = _target_handlers(None)
    h["turret.aim"] = lambda p: aimed.append(p) or {"aimed": ["Gatling Turret"]}
    text = await _call(h, "target_block", {"block": "86099400851731085", "target_grid": "Whale", "weapons": "turrets",
                                           "miss_by_m": 15})
    assert "aiming 15 m to the side" in text
    pos = aimed[0]["position"]
    assert abs(abs(pos[0]) - 15) < 1e-6 and pos[2] == -300


async def test_target_block_names_armour_plainly():
    h = _target_handlers({"entity": "Whale", "entityId": "2002",
                          "block": {"name": "MyObjectBuilder_CubeBlock/HalfArmorBlock"}, "fromStart": 294})
    text = await _call(h, "target_block", {"block": "86099400851731085", "target_grid": "Whale", "weapons": "turrets"})
    assert "Gatling Turret's shots meet Half Armor Block first, 6 m before Whale_Hydrogen Tank 2." in text


async def test_target_block_holds_fire_on_a_turret_that_cant_reach():
    h = _target_handlers(None, stuck_deg=20)
    text = await _call(h, "target_block", {"block": "86099400851731085", "target_grid": "Whale", "weapons": "turrets",
                                           "fire": True})
    assert "! Gatling Turret couldn't get on target (20 deg off: out of its arc or blocked)." in text
    assert "Held fire on Gatling Turret: not on target." in text and "Fired one shot" not in text
