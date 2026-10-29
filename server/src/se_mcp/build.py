"""Build planning: block costs broken down to components, ingots and ore, and what the ship is short of."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import items as items_mod
from . import production

Key = tuple[str, str]


def add(into: dict[Key, float], items: list[dict[str, Any]], count_key: str = "count") -> dict[Key, float]:
    for i in items:
        into[(i["type"], i["subtype"])] = into.get((i["type"], i["subtype"]), 0.0) + i[count_key]
    return into


def components_to_ingots(components: dict[Key, float], book: dict[str, Any]) -> tuple[dict[Key, float], list[str]]:
    """Ingots for assembling these components with the world's assembler efficiency. Unknown ones are listed."""
    ingots: dict[Key, float] = defaultdict(float)
    unknown = []
    for key, count in components.items():
        recipes = [r for r in production.producing(key, book) if not production.is_refining(r)]
        if not recipes:
            unknown.append(items_mod.display(*key))
            continue
        r = recipes[0]
        for t, s, amount in production.inputs_for(r, production.runs_for(r, key, count), book):
            ingots[(t, s)] += amount
    return dict(ingots), unknown


def ingots_to_ore(ingots: dict[Key, float], book: dict[str, Any], refinery: str = "Refinery") -> tuple[dict[Key, float], list[str]]:
    """Ore for refining these ingots in the given refinery type (no yield modules). Uses the ore of the same name
    (Iron Ore -> Iron Ingot), not scrap or stone."""
    efficiency = float((book.get("refineryEfficiency") or {}).get(refinery, 1.0))
    ore: dict[Key, float] = defaultdict(float)
    unknown = []
    for key, amount in ingots.items():
        recipes = [r for r in production.producing(key, book) if production.is_refining(r)
                   and len(r["inputs"]) == 1 and r["inputs"][0]["subtype"] == key[1]]
        if not recipes:
            unknown.append(items_mod.display(*key))
            continue
        r = recipes[0]
        per_run = next(o["amount"] for o in r["outputs"] if (o["type"], o["subtype"]) == key) * efficiency
        source = r["inputs"][0]
        ore[(source["type"], source["subtype"])] += amount / per_run * source["amount"]
    return dict(ore), unknown


def table(title: str, needs: dict[Key, float], have: dict[Key, float] | None) -> tuple[list[str], list[str]]:
    rows = sorted(needs.items(), key=lambda kv: -kv[1])
    lines, short = production.ingredient_lines([(t, s, a) for (t, s), a in rows], have)
    return [title] + lines, short


def breakdown(components: dict[Key, float], book: dict[str, Any], have: dict[Key, float] | None,
              depth: str = "ore") -> str:
    """Components -> ingots -> ore, each level against what the ship has."""
    lines, short = table("Components:", components, have)
    out = lines
    if depth in ("ingots", "ore"):
        ingots, unknown = components_to_ingots(components, book)
        lines, _ = table(f"Ingots to assemble them (assembler efficiency x{book.get('assemblerEfficiency') or 1:g}):", ingots, have)
        out += lines
        if unknown:
            out.append("  (no assembler recipe for: " + ", ".join(unknown) + ")")
        if depth == "ore":
            ore, unknown = ingots_to_ore(ingots, book)
            lines, _ = table("Ore to refine those ingots (plain Refinery, no yield modules):", ore, None)
            out += lines
            if unknown:
                out.append("  (not refined from ore: " + ", ".join(unknown) + ")")
    if short:
        out.append("Short of components: " + ", ".join(short))
    return "\n".join(out)


def queue_list(components: dict[Key, float], have: dict[Key, float]) -> dict[Key, float]:
    """What still has to be made: needed minus what the ship already holds."""
    return {k: v - have.get(k, 0.0) for k, v in components.items() if v - have.get(k, 0.0) > 0.5}
