"""Item names: game ids (type + subtype) <-> what a player would say ("iron ingots", "ice", "steel plates")."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .resolve import ResolveError

# Short type (as the plugin reports it) -> words a player uses for it.
TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "Ore": ("ore", "ores"),
    "Ingot": ("ingot", "ingots", "bar", "bars"),
    "Component": ("component", "components", "comp", "comps"),
    "AmmoMagazine": ("ammo", "magazine", "magazines", "mag", "mags", "rounds"),
    "PhysicalGunObject": ("tool", "tools", "gun", "guns", "rifle", "weapon", "weapons"),
    "OxygenContainerObject": ("bottle", "bottles", "oxygen bottle"),
    "GasContainerObject": ("bottle", "bottles", "hydrogen bottle"),
    "ConsumableItem": ("consumable", "consumables", "food", "medical", "medkit"),
}

# Subtypes the game names differently from what players say.
ALIASES: dict[str, tuple[str, str]] = {
    "gravel": ("Ingot", "Stone"),
    "uranium": ("Ingot", "Uranium"),
    "ice": ("Ore", "Ice"),
}


def words(text: str) -> list[str]:
    """'SteelPlate' / 'steel plates' -> ['steel', 'plate']."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|[_\-]", " ", text)
    out = []
    for w in re.findall(r"[a-z0-9]+", spaced.lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def display(type_: str, subtype: str) -> str:
    """Human name: Ingot/Iron -> 'Iron Ingot', Component/SteelPlate -> 'Steel Plate', Ore/Ice -> 'Ice'."""
    name = re.sub(r"(?<=[a-z])(?=[A-Z])|_", " ", subtype).strip()
    if type_ == "Ore" and subtype not in ("Ice", "Stone", "Scrap"):
        return f"{name} Ore"
    if type_ == "Ingot":
        return "Gravel" if subtype == "Stone" else f"{name} Ingot"
    if type_ == "PhysicalGunObject" and name.endswith(" Item"):
        return name[:-5]
    return name


def amount_text(amount: float, type_: str) -> str:
    if type_ in ("Ore", "Ingot"):
        return f"{amount:,.0f} kg" if amount >= 10 else f"{amount:.2f} kg"
    return f"{amount:,.0f}"


def match(query: str, known: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Item types (type, subtype) matching a player's phrase, best first. `known` is what's actually around."""
    known = sorted(set(known))
    q = query.strip().lower()
    if "/" in q:  # exact "Ingot/Iron"
        t, s = query.split("/", 1)
        exact = [(kt, ks) for kt, ks in known if kt.lower() == t.strip().lower() and ks.lower() == s.strip().lower()]
        if exact:
            return exact
    if q in ALIASES and ALIASES[q] in known:
        return [ALIASES[q]]
    qw = words(query)
    type_hints = {t for t, ws in TYPE_WORDS.items() if any(w in qw for w in (words(" ".join(ws))))}
    scored = []
    for t, s in known:
        sw = words(s)
        name_words = [w for w in qw if w not in {x for ws in TYPE_WORDS.values() for x in words(" ".join(ws))}]
        if not name_words:
            hit = bool(type_hints) and t in type_hints  # "ammo", "ingots": everything of that type
        else:
            hit = all(any(n == w or (len(n) >= 3 and w.startswith(n)) for w in sw) for n in name_words)
        if hit and (not type_hints or t in type_hints):
            scored.append(((len(sw) - len(name_words)), t, s))
    return [(t, s) for _, t, s in sorted(scored)]


def resolve_one(query: str, known: Iterable[tuple[str, str]], where: str) -> tuple[str, str]:
    """Exactly one item type, or a ResolveError saying what was meant / what's there."""
    known = list(set(known))
    found = match(query, known)
    if len(found) == 1:
        return found[0]
    if not found:
        listing = ", ".join(sorted(display(t, s) for t, s in known)[:25]) or "nothing"
        raise ResolveError(f"No '{query}' in {where}. There is: {listing}")
    names = ", ".join(f"{display(t, s)} ({t}/{s})" for t, s in found[:10])
    raise ResolveError(f"'{query}' could mean: {names}. Be more specific, e.g. 'iron ore' vs 'iron ingot'.")


def item_keys(items: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(i["type"], i["subtype"]) for i in items}
