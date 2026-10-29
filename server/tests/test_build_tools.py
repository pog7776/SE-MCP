from __future__ import annotations

from typing import Any

import pytest

from se_mcp import build, production
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers

BOOK = {
    "assemblerSpeed": 3, "assemblerEfficiency": 3, "refinerySpeed": 3,
    "refineryEfficiency": {"Refinery": 1.0, "Basic Refinery": 0.7},
    "recipes": [
        {"id": "BlueprintDefinition/SteelPlate", "name": "Steel Plate", "madeIn": ["Assembler"],
         "inputs": [{"type": "Ingot", "subtype": "Iron", "amount": 21}],
         "outputs": [{"type": "Component", "subtype": "SteelPlate", "amount": 1}], "seconds": 1, "ignoresEfficiency": False},
        {"id": "BlueprintDefinition/IronOreToIngot", "name": "Iron Ingot", "madeIn": ["Refinery"],
         "inputs": [{"type": "Ore", "subtype": "Iron", "amount": 1}],
         "outputs": [{"type": "Ingot", "subtype": "Iron", "amount": 0.7}], "seconds": 0.05, "ignoresEfficiency": False},
        {"id": "BlueprintDefinition/ScrapToIronIngot", "name": "Iron Ingot", "madeIn": ["Refinery"],
         "inputs": [{"type": "Ore", "subtype": "Scrap", "amount": 1}],
         "outputs": [{"type": "Ingot", "subtype": "Iron", "amount": 0.8}], "seconds": 0.04, "ignoresEfficiency": False},
    ],
}
PLATES = {("Component", "SteelPlate"): 600.0}


def test_breakdown_goes_components_to_ingots_to_ore_from_matching_ore():
    ingots, unknown = build.components_to_ingots(PLATES, BOOK)
    assert ingots == {("Ingot", "Iron"): pytest.approx(4200)} and not unknown
    ore, _ = build.ingots_to_ore(ingots, BOOK)
    assert ore == {("Ore", "Iron"): pytest.approx(6000)}  # iron ore, not scrap
    basic, _ = build.ingots_to_ore(ingots, BOOK, "Basic Refinery")
    assert basic[("Ore", "Iron")] == pytest.approx(6000 / 0.7)


def test_queue_list_subtracts_stock():
    assert build.queue_list(PLATES, {("Component", "SteelPlate"): 450}) == {("Component", "SteelPlate"): 150}


def handlers(calls: list) -> dict[str, Any]:
    production._CACHE.clear()
    h = default_handlers()
    gyro = {"id": "Gyro/LargeBlockGyro", "name": "Gyroscope", "size": "large", "pcu": 50, "count": 1,
            "components": [{"type": "Component", "subtype": "SteelPlate", "count": 600}]}

    def record(name):
        def handler(p):
            calls.append((name, p))
            return {"block": "Assembler", "queued": "Steel Plate", "amount": p.get("amount")}
        return handler

    h.update({
        "recipes.list": lambda p: BOOK,
        "blockdefs.find": lambda p: {"blocks": [gyro]},
        "inventory.get": lambda p: {"totals": [] if p.get("character") else [{"type": "Component", "subtype": "SteelPlate", "amount": 100}]},
        "buildplanner.get": lambda p: {"items": [{"index": 0, "block": "Gyroscope", "size": "large", "inProgress": False,
                                                  "components": [{"type": "Component", "subtype": "SteelPlate", "count": 600}]}]},
        "production.get": lambda p: {"blocks": [{"id": "7200001", "name": "Assembler", "displayType": "Assembler",
                                                 "working": True, "mode": "Assembly", "queue": []}]},
        "production.queue": record("queue"),
        "blocks.list": lambda p: {"total": 1, "offset": 0, "blocks": [{"id": "7300001", "name": "Projector", "type": "Projector"}]},
        "projector.get": lambda p: {"name": "Projector", "projecting": True, "working": True, "projectedGrid": "Miner Mk2",
                                    "totalBlocks": 100, "remainingBlocks": 3, "remainingArmorBlocks": 2, "buildableNow": 1,
                                    "remaining": [{**gyro, "count": 1}], "unfinishedBlocks": 1,
                                    "unfinishedComponents": [{"type": "Component", "subtype": "SteelPlate", "count": 50}]},
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


async def test_block_cost_scales_and_reports_shortfall():
    text = await call("block_cost", {"block": "gyroscope", "count": 2}, [])
    assert text.startswith("2x Gyroscope (large grid, 100 PCU)")
    assert "1,200 Steel Plate (ship has 100)" in text and "8,400 kg Iron Ingot" in text and "12,000 kg Iron Ore" in text


async def test_build_planner_queue_makes_only_the_missing_part():
    calls: list = []
    text = await call("build_planner", {"action": "queue"}, calls)
    assert calls == [("queue", {"blockId": "7200001", "blueprint": "BlueprintDefinition/SteelPlate", "amount": 500})]
    assert "queued 500 Steel Plate" in text


async def test_projector_counts_unplaced_and_unfinished_blocks():
    calls: list = []
    text = await call("projector_status", {"queue_missing": True}, calls)
    assert "97/100 blocks placed" in text and "1 buildable now" in text
    assert "650 Steel Plate (ship has 100)" in text
    assert calls and calls[0][1]["amount"] == 550
