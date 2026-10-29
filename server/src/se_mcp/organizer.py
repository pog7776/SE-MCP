"""Inventory organizer: plan where loose items should go on a ship, then carry the plan out.

Rules, in order:
1. Containers can be tagged in their name or CustomData: [Ore] [Ice] [Ingots] [Components] [Ammo] [Tools]
   [Bottles] [Consumables] (several allowed). Items of a tagged category go to those containers.
2. [Ignore] / [NoSort] on a block keeps the organizer away from it entirely.
3. Loose items in places that aren't storage (connectors, production outputs) go to storage: a tagged
   container if there is one, else the cargo container with the most free space.
4. Items sitting in a cargo container that isn't tagged for them move only if a tagged container exists.
5. Empty reactors get uranium, and empty O2/H2 generators get some ice, so nothing sits unpowered.
6. Everything stays within its conveyor network; things the conveyors can't carry are reported, not forced.
Fuel in reactors, ice in generators, ammo in guns and ingredients in assembler/refinery inputs are left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .items import amount_text, display

CATEGORY_OF_TYPE = {
    "Ore": "ore", "Ingot": "ingots", "Component": "components", "AmmoMagazine": "ammo",
    "PhysicalGunObject": "tools", "OxygenContainerObject": "bottles", "GasContainerObject": "bottles",
    "ConsumableItem": "consumables",
}

TAGS = {
    "ore": "ore", "ores": "ore", "ice": "ice", "ingot": "ingots", "ingots": "ingots",
    "component": "components", "components": "components", "comps": "components",
    "ammo": "ammo", "ammunition": "ammo", "tool": "tools", "tools": "tools", "weapons": "tools",
    "bottle": "bottles", "bottles": "bottles", "consumable": "consumables", "consumables": "consumables",
    "food": "consumables", "medical": "consumables",
}
IGNORE_TAGS = {"ignore", "nosort", "no sort", "locked"}

STORAGE_TYPES = {"CargoContainer"}
# Places items end up by accident: these get emptied into storage.
TRANSIT_TYPES = {"ShipConnector", "Collector", "Drill", "ShipGrinder", "ShipWelder"}

REACTOR_URANIUM_KG = 50
GENERATOR_ICE_FRACTION = 0.25
ICE_LITERS_PER_KG = 0.37
URANIUM_LITERS_PER_KG = 0.052


def category(item_type: str, subtype: str) -> str:
    if item_type == "Ore" and subtype == "Ice":
        return "ice"
    return CATEGORY_OF_TYPE.get(item_type, "other")


def tags_of(inv: dict[str, Any]) -> set[str]:
    text = f"{inv['name']} {inv.get('customData') or ''}"
    found = set()
    for raw in re.findall(r"\[([^\]]+)\]", text):
        for part in re.split(r"[,;/|]", raw):
            key = part.strip().lower()
            if key in IGNORE_TAGS:
                found.add("ignore")
            elif key in TAGS:
                found.add(TAGS[key])
    if "ore" in found:
        found.add("ice")  # an ore box takes ice too, unless there's a dedicated [Ice] box (handled by preference)
    return found


@dataclass
class Move:
    type: str
    subtype: str
    amount: float | None  # None = all of it
    source: dict[str, Any]
    dest: dict[str, Any]
    why: str

    def describe(self) -> str:
        qty = "all" if self.amount is None else amount_text(self.amount, self.type)
        return f"{qty} {display(self.type, self.subtype)}: {label(self.source)} -> {label(self.dest)} ({self.why})"


@dataclass
class Plan:
    moves: list[Move] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def annotate(layout: dict[str, Any]) -> dict[str, Any]:
    """Mark inventories on docked ships, so labels can say where they are."""
    for inv in layout["inventories"]:
        inv["_docked"] = inv.get("shipId", layout.get("shipId")) != layout.get("shipId")
    return layout


def label(inv: dict[str, Any]) -> str:
    role = f" {inv['role']}" if inv.get("role") == "output" else ""
    where = f" (on {inv['shipName']})" if inv.get("_docked") else ""
    return f"{inv['name']}{role}{where}"


def free_liters(inv: dict[str, Any]) -> float:
    return max(0.0, inv["maxVolumeL"] - inv["volumeL"])


def plan(layout: dict[str, Any]) -> Plan:
    result = Plan()
    annotate(layout)
    # Only this ship: docked ships share conveyors but keep their own cargo.
    inventories = [i for i in layout["inventories"] if "ignore" not in tags_of(i) and not i["_docked"]]
    docked = sorted({i["shipName"] for i in layout["inventories"] if i["_docked"]})
    if docked:
        result.notes.append(f"Docked ships left alone: {', '.join(docked)}. Name one to organize it.")
    for inv in inventories:
        inv["_tags"] = tags_of(inv)

    def storage_for(cat: str, group: int, exclude: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        tagged = [i for i in inventories if i["type"] in STORAGE_TYPES and i["conveyorGroup"] == group
                  and cat in i["_tags"] and i is not exclude and free_liters(i) > 1]
        if cat == "ice":  # prefer a dedicated [Ice] box over a general [Ore] box
            tagged.sort(key=lambda i: ("ice" not in re.findall(r"\[([^\]]+)\]", i["name"].lower() + " " + (i.get("customData") or "").lower()), -free_liters(i)))
        else:
            tagged.sort(key=lambda i: -free_liters(i))
        if tagged:
            return tagged[0], f"tagged [{cat}]"
        plain = [i for i in inventories if i["type"] in STORAGE_TYPES and i["conveyorGroup"] == group
                 and not i["_tags"] and i is not exclude and free_liters(i) > 1]
        plain.sort(key=lambda i: -free_liters(i))
        return (plain[0], "most free cargo space") if plain else (None, "")

    has_tags = any(i["_tags"] - {"ignore"} for i in inventories)
    for inv in inventories:
        is_transit = inv["type"] in TRANSIT_TYPES or inv.get("role") == "output"
        is_storage = inv["type"] in STORAGE_TYPES
        if not (is_transit or is_storage):
            continue
        for item in inv["items"]:
            cat = category(item["type"], item["subtype"])
            if is_storage and cat in inv["_tags"]:
                continue  # already where it belongs
            if is_storage and not has_tags:
                continue  # untagged ship: cargo is fine as is
            dest, why = storage_for(cat, inv["conveyorGroup"], inv)
            if dest is None:
                if is_transit:
                    result.notes.append(f"No cargo on the same conveyors to take {display(item['type'], item['subtype'])} from {label(inv)}.")
                continue
            if is_storage and not why.startswith("tagged"):
                continue  # rule 4: only move out of storage into a tagged home
            result.moves.append(Move(item["type"], item["subtype"], None, inv, dest, why))

    # Rule 5: fuel for empty reactors and generators, from storage on the same conveyors.
    def stock(group: int, type_: str, subtype: str) -> list[tuple[dict[str, Any], float]]:
        out = []
        for i in inventories:
            if i["type"] in STORAGE_TYPES | TRANSIT_TYPES and i["conveyorGroup"] == group:
                amount = sum(x["amount"] for x in i["items"] if x["type"] == type_ and x["subtype"] == subtype)
                if amount > 0:
                    out.append((i, amount))
        return sorted(out, key=lambda x: -x[1])

    for inv in inventories:
        if not inv.get("working", True):
            continue
        if inv["type"] == "Reactor" and not any(x["subtype"] == "Uranium" for x in inv["items"]):
            want = min(REACTOR_URANIUM_KG, free_liters(inv) / URANIUM_LITERS_PER_KG)
            for src, amount in stock(inv["conveyorGroup"], "Ingot", "Uranium")[:1]:
                result.moves.append(Move("Ingot", "Uranium", round(min(want, amount), 2), src, inv, "reactor was empty"))
        if inv["type"] == "OxygenGenerator" and not any(x["subtype"] == "Ice" for x in inv["items"]):
            want = inv["maxVolumeL"] * GENERATOR_ICE_FRACTION / ICE_LITERS_PER_KG
            for src, amount in stock(inv["conveyorGroup"], "Ore", "Ice")[:1]:
                result.moves.append(Move("Ore", "Ice", round(min(want, amount), 0), src, inv, "generator had no ice"))

    if not has_tags:
        result.notes.append("No containers are tagged, so cargo is left as is; only stray items and empty fuel slots are handled. "
                            "Tag containers like 'Cargo [Ore]' or 'Cargo [Components]' to sort by category.")
    return result


def describe(p: Plan, grid_name: str) -> str:
    if not p.moves:
        lines = [f"{grid_name}: nothing to move, everything is where it belongs."]
    else:
        lines = [f"{grid_name}: {len(p.moves)} move(s) planned:"] + [f"{n}. {m.describe()}" for n, m in enumerate(p.moves, 1)]
    lines += [f"Note: {n}" for n in p.notes]
    return "\n".join(lines)


def transfer_params(m: Move) -> dict[str, Any]:
    params: dict[str, Any] = {
        "fromBlockId": m.source["blockId"], "fromIndex": m.source["index"],
        "toBlockId": m.dest["blockId"], "toIndex": m.dest["index"],
        "type": m.type, "subtype": m.subtype,
    }
    if m.amount is not None:
        params["amount"] = m.amount
    return params
