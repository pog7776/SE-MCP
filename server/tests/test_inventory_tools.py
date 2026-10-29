from __future__ import annotations

from typing import Any

from se_mcp import organizer
from se_mcp.bridge import Bridge
from se_mcp.items import display, match
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

SHIP, FIGHTER = "1001", "3003"


def inv(block_id: str, name: str, type_: str, items: list[tuple[str, str, float]], *, index: int = 0, role: str | None = None,
        group: int = 0, ship: str = SHIP, free: float = 1000, custom: str | None = None) -> dict[str, Any]:
    return {
        "blockId": block_id, "name": name, "type": type_, "subtype": "", "gridName": "Miner", "customData": custom,
        "shipId": ship, "shipName": "Miner" if ship == SHIP else "Fighter", "index": index, "role": role, "working": True,
        "conveyorGroup": group, "volumeL": 0, "maxVolumeL": free,
        "items": [{"type": t, "subtype": s, "amount": a} for t, s, a in items],
    }


def layout(*inventories: dict[str, Any]) -> dict[str, Any]:
    return {"gridName": "Miner", "shipId": SHIP, "inventories": list(inventories)}


def test_item_names():
    known = [("Ore", "Iron"), ("Ingot", "Iron"), ("Component", "SteelPlate"), ("Ingot", "Stone"), ("PhysicalGunObject", "WelderItem")]
    assert match("iron ingots", known) == [("Ingot", "Iron")]
    assert len(match("iron", known)) == 2
    assert match("steel plates", known) == [("Component", "SteelPlate")]
    assert display("Ingot", "Stone") == "Gravel" and display("PhysicalGunObject", "WelderItem") == "Welder"


def test_untagged_ship_only_clears_strays_and_fuels_empty_reactors():
    p = organizer.plan(layout(
        inv("1", "Connector", "ShipConnector", [("Ore", "Iron", 500)]),
        inv("2", "Cargo A", "CargoContainer", [("Ingot", "Uranium", 100)], free=100),
        inv("3", "Cargo B", "CargoContainer", [], free=5000),
        inv("4", "Reactor", "Reactor", []),
        inv("5", "Assembler", "Assembler", [("Component", "SteelPlate", 30)], index=1, role="output"),
    ))
    moves = [(m.subtype, m.source["name"], m.dest["name"], m.amount) for m in p.moves]
    assert ("Iron", "Connector", "Cargo B", None) in moves
    assert ("SteelPlate", "Assembler", "Cargo B", None) in moves
    assert ("Uranium", "Cargo A", "Reactor", 50) in moves
    assert not any(m.source["name"] == "Cargo A" and m.subtype != "Uranium" for m in p.moves)


def test_tags_sort_cargo_and_respect_ignore_and_docked_ships():
    p = organizer.plan(layout(
        inv("1", "Cargo [Ore]", "CargoContainer", [("Component", "SteelPlate", 10)]),
        inv("2", "Cargo", "CargoContainer", [], custom="[Components]"),
        inv("3", "Junk [Ignore]", "CargoContainer", [("Ore", "Stone", 99)]),
        inv("4", "Big Cargo", "CargoContainer", [("Ore", "Ice", 300), ("Ingot", "Gold", 5)]),
        inv("5", "Fighter Connector", "ShipConnector", [("AmmoMagazine", "NATO_25x184mm", 10)], ship=FIGHTER),
    ))
    moves = {(m.subtype, m.source["name"], m.dest["name"]) for m in p.moves}
    assert ("SteelPlate", "Cargo [Ore]", "Cargo") in moves      # tagged home elsewhere
    assert ("Ice", "Big Cargo", "Cargo [Ore]") in moves          # [Ore] boxes take ice
    assert not any(s == "Gold" for s, _, _ in moves)             # no [Ingots] box: stays put
    assert not any(src == "Junk [Ignore]" for _, src, _ in moves)
    assert not any(src == "Fighter Connector" for _, src, _ in moves)
    assert any("Fighter" in n for n in p.notes)


def test_moves_stay_on_their_conveyor_network():
    p = organizer.plan(layout(
        inv("1", "Connector", "ShipConnector", [("Ore", "Iron", 500)], group=0),
        inv("2", "Isolated Cargo", "CargoContainer", [], group=1),
    ))
    assert not p.moves and "No cargo on the same conveyors" in p.notes[0]


def handlers(moved: list[dict]) -> dict[str, Any]:
    h = default_handlers()
    blocks_list = h["blocks.list"]
    names = {"assembler": ("7000001", "Assembler"), "cargo": ("7000002", "Main Cargo")}

    def blocks(p: dict) -> dict:
        key = (p.get("nameContains") or "").lower()
        if key in names:
            i, n = names[key]
            return {"total": 1, "offset": 0, "blocks": [{"id": i, "name": n, "type": "X"}]}
        return blocks_list(p)

    def transfer(p: dict) -> dict:
        moved.append(p)
        return {"item": {"type": p["type"], "subtype": p["subtype"]}, "from": "Assembler", "to": "Main Cargo",
                "route": "through conveyors", "requested": 40, "moved": 40, "leftInSource": 0, "reason": None}

    h.update({
        "blocks.list": blocks, "inventory.transfer": transfer,
        "inventory.layout": lambda p: layout(
            inv("7000001", "Assembler", "Assembler", [("Ingot", "Iron", 100)], index=0, role="input"),
            inv("7000001", "Assembler", "Assembler", [("Component", "SteelPlate", 40)], index=1, role="output"),
            inv("7000002", "Main Cargo", "CargoContainer", []),
        ),
    })
    return h


async def call(tool: str, args: dict[str, Any], moved: list[dict]) -> str:
    async with FakeGame(handlers(moved)) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
            return "\n".join(c.text for c in result.content)
        finally:
            await bridge.close()


async def test_move_items_takes_from_the_output_inventory():
    moved: list[dict] = []
    text = await call("move_items", {"item": "steel plates", "from_block": "assembler", "to_block": "cargo"}, moved)
    assert moved == [{"type": "Component", "subtype": "SteelPlate", "fromBlockId": "7000001", "fromIndex": 1,
                      "toBlockId": "7000002", "toIndex": 0}]
    assert text.startswith("Moved 40 Steel Plate: Assembler -> Main Cargo (through conveyors).")


async def test_organize_needs_apply_to_move():
    moved: list[dict] = []
    text = await call("organize_inventory", {}, moved)
    assert "Steel Plate: Assembler output -> Main Cargo" in text and "apply=True" in text and not moved
    text = await call("organize_inventory", {"apply": True}, moved)
    assert len(moved) == 1 and "1 move(s) done" in text
