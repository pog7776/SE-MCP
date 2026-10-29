"""Recipes and production queues: what makes an item, how much it costs in this world, and which block to use."""

from __future__ import annotations

import time
from typing import Any

from . import items as items_mod
from .bridge import Bridge
from .resolve import ResolveError

_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
CACHE_SECONDS = 300


async def recipes(bridge: Bridge) -> dict[str, Any]:
    """All craftable recipes plus world multipliers, cached (definitions don't change mid-session)."""
    key = id(bridge)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = await bridge.call("recipes.list")
    _CACHE[key] = (time.monotonic(), data)
    return data


def is_refining(recipe: dict[str, Any]) -> bool:
    return any(i["type"] == "Ore" for i in recipe["inputs"])


def inputs_for(recipe: dict[str, Any], runs: float, book: dict[str, Any]) -> list[tuple[str, str, float]]:
    """Ingredients for `runs` of a recipe in this world. Assemblers divide by the world's efficiency multiplier
    (3x in default survival); refineries don't (their yield comes from the block and its upgrades)."""
    divisor = 1.0 if recipe["ignoresEfficiency"] or is_refining(recipe) else float(book.get("assemblerEfficiency") or 1)
    return [(i["type"], i["subtype"], i["amount"] * runs / divisor) for i in recipe["inputs"]]


def producing(item: tuple[str, str], book: dict[str, Any]) -> list[dict[str, Any]]:
    """Recipes that make an item. Identical recipes in different blocks (e.g. survival kit and refinery
    stone processing) are merged, listing every block that has them."""
    merged: dict[Any, dict[str, Any]] = {}
    for r in book["recipes"]:
        if not any((o["type"], o["subtype"]) == item for o in r["outputs"]):
            continue
        key = (repr(r["inputs"]), repr(r["outputs"]), r["seconds"])
        if key in merged:
            merged[key] = {**merged[key], "madeIn": sorted(set(merged[key]["madeIn"]) | set(r["madeIn"]))}
        else:
            merged[key] = r
    return list(merged.values())


def resolve_item(query: str, book: dict[str, Any]) -> tuple[str, str]:
    known = {(o["type"], o["subtype"]) for r in book["recipes"] for o in r["outputs"]}
    return items_mod.resolve_one(query, known, "anything that can be crafted")


def runs_for(recipe: dict[str, Any], item: tuple[str, str], amount: float) -> float:
    per_run = next(o["amount"] for o in recipe["outputs"] if (o["type"], o["subtype"]) == item)
    return amount / per_run


def seconds_for(recipe: dict[str, Any], runs: float, book: dict[str, Any]) -> float:
    speed = float((book.get("refinerySpeed") if is_refining(recipe) else book.get("assemblerSpeed")) or 1)
    return recipe["seconds"] * runs / speed


def duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def ship_totals(inventory: dict[str, Any]) -> dict[tuple[str, str], float]:
    return {(t["type"], t["subtype"]): t["amount"] for t in inventory["totals"]}


def ingredient_lines(needs: list[tuple[str, str, float]], have: dict[tuple[str, str], float] | None) -> tuple[list[str], list[str]]:
    lines, short = [], []
    for t, s, amount in needs:
        line = f"  - {items_mod.amount_text(amount, t)} {items_mod.display(t, s)}"
        if have is not None:
            got = have.get((t, s), 0.0)
            line += f" (ship has {items_mod.amount_text(got, t)})"
            if got + 1e-6 < amount:
                short.append(f"{items_mod.amount_text(amount - got, t)} {items_mod.display(t, s)}")
        lines.append(line)
    return lines, short


async def pick_producer(bridge: Bridge, grid_id: str, recipe: dict[str, Any], name: str | None) -> dict[str, Any]:
    """The named block, else the best working block on the ship that makes this recipe (shortest queue)."""
    blocks = (await bridge.call("production.get", {"gridId": grid_id}))["blocks"]
    candidates = [b for b in blocks if b["displayType"] in recipe["madeIn"]]
    if name:
        wanted = name.strip().lower()
        named = [b for b in blocks if b["id"] == name.strip() or wanted in b["name"].lower()]
        if not named:
            raise ResolveError(f"No production block matching '{name}'. There are: {', '.join(b['name'] for b in blocks) or 'none'}")
        usable = [b for b in named if b in candidates]
        if not usable:
            raise ResolveError(f"{named[0]['name']} ({named[0]['displayType']}) can't make {recipe['name']}; "
                               f"it's made in: {', '.join(recipe['madeIn'])}")
        return usable[0]
    working = [b for b in candidates if b["working"] and b.get("mode") in (None, "Assembly")]
    if not working:
        raise ResolveError(f"Nothing on this ship can make {recipe['name']} right now (needs a working "
                           f"{' or '.join(recipe['madeIn'])}).")
    return min(working, key=lambda b: sum(q["amount"] for q in b["queue"]))
