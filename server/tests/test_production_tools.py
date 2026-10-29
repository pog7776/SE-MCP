from __future__ import annotations

from typing import Any

import pytest

from se_mcp import production
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

ASM = "7100001"

BOOK = {
    "assemblerSpeed": 3, "assemblerEfficiency": 3, "refinerySpeed": 3,
    "recipes": [
        {"id": "BlueprintDefinition/SteelPlate", "name": "Steel Plate", "madeIn": ["Assembler", "Survival Kit"],
         "inputs": [{"type": "Ingot", "subtype": "Iron", "amount": 21}],
         "outputs": [{"type": "Component", "subtype": "SteelPlate", "amount": 1}], "seconds": 1, "ignoresEfficiency": False},
        {"id": "BlueprintDefinition/IronOreToIngot", "name": "Iron Ingot", "madeIn": ["Refinery"],
         "inputs": [{"type": "Ore", "subtype": "Iron", "amount": 1}],
         "outputs": [{"type": "Ingot", "subtype": "Iron", "amount": 0.7}], "seconds": 0.05, "ignoresEfficiency": False},
        {"id": "BlueprintDefinition/StoneOreToIngot", "name": "Ingots", "madeIn": ["Refinery"],
         "inputs": [{"type": "Ore", "subtype": "Stone", "amount": 1000}],
         "outputs": [{"type": "Ingot", "subtype": "Stone", "amount": 14}], "seconds": 10, "ignoresEfficiency": False},
        {"id": "BlueprintDefinition/StoneOreToIngotBasic", "name": "Ingots", "madeIn": ["Survival Kit"],
         "inputs": [{"type": "Ore", "subtype": "Stone", "amount": 1000}],
         "outputs": [{"type": "Ingot", "subtype": "Stone", "amount": 14}], "seconds": 10, "ignoresEfficiency": False},
    ],
}


def test_assembler_efficiency_applies_to_assemblers_only():
    plate, ore = BOOK["recipes"][0], BOOK["recipes"][1]
    assert production.inputs_for(plate, 10, BOOK) == [("Ingot", "Iron", 70.0)]
    assert production.inputs_for(ore, 10, BOOK) == [("Ore", "Iron", 10.0)]
    assert production.runs_for(ore, ("Ingot", "Iron"), 7) == pytest.approx(10)


def test_identical_recipes_merge():
    [gravel] = production.producing(("Ingot", "Stone"), BOOK)
    assert gravel["madeIn"] == ["Refinery", "Survival Kit"]


def handlers(calls: list) -> dict[str, Any]:
    production._CACHE.clear()
    h = default_handlers()
    queue: list[dict] = []

    def queue_add(p: dict) -> dict:
        calls.append(p)
        queue.append({"index": len(queue), "blueprint": p["blueprint"].split("/")[1], "amount": p["amount"]})
        return {"block": "Assembler", "queued": "Steel Plate", "amount": p["amount"]}

    h.update({
        "recipes.list": lambda p: BOOK,
        "production.get": lambda p: {"blocks": [{"id": ASM, "name": "Assembler", "displayType": "Assembler", "working": True,
                                                 "mode": "Assembly", "queue": queue}]},
        "production.queue": queue_add,
        "inventory.get": lambda p: {"totals": [{"type": "Ingot", "subtype": "Iron", "amount": 100}]},
    })
    return h


async def call(tool: str, args: dict[str, Any], calls: list) -> str:
    async with FakeGame(handlers(calls)) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
            return "\n".join(c.text for c in result.content)
        finally:
            await bridge.close()


async def test_recipe_shows_world_cost_and_shortfall():
    text = await call("recipe", {"item": "steel plates", "amount": 10}, [])
    assert "70 kg Iron Ingot (ship has 100 kg)" in text and "short" not in text
    text = await call("recipe", {"item": "steel plates", "amount": 100}, [])
    assert "short: 600 kg Iron Ingot" in text


async def test_queue_production_picks_the_assembler_and_reports_shortage():
    calls: list = []
    text = await call("queue_production", {"item": "steel plate", "amount": 20}, calls)
    assert calls == [{"blockId": ASM, "blueprint": "BlueprintDefinition/SteelPlate", "amount": 20}]
    assert text.startswith("Queued 20 Steel Plate in Assembler") and "Short of: 40 kg Iron Ingot" in text


async def test_refined_items_are_not_queued():
    text = await call("queue_production", {"item": "iron ingot", "amount": 5}, [])
    assert "isn't made in an assembler" in text
