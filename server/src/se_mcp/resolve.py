"""Turn what the model says ("my ship", "Refinery 2", an id) into entity ids the plugin understands."""

from __future__ import annotations

import difflib
from typing import Any

from .bridge import Bridge, BridgeError


class ResolveError(Exception):
    pass


def _is_id(text: str) -> bool:
    return text.strip().isdigit() and len(text.strip()) > 6


def _pick(query: str, candidates: list[dict[str, Any]], what: str) -> dict[str, Any]:
    """Exact name, then unique substring, else an error listing matches or close names."""
    q = query.strip().lower()
    exact = [c for c in candidates if (c.get("name") or "").lower() == q]
    if len(exact) == 1:
        return exact[0]
    partial = exact or [c for c in candidates if q in (c.get("name") or "").lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        listed = ", ".join(f"'{c['name']}' ({c['id']})" for c in partial[:10])
        raise ResolveError(f"'{query}' matches {len(partial)} {what}s: {listed}. Use a more specific name or the id.")
    names = [c.get("name") or "" for c in candidates]
    close = difflib.get_close_matches(query, names, n=5, cutoff=0.4)
    hint = f" Did you mean: {', '.join(repr(n) for n in close)}?" if close else ""
    raise ResolveError(f"No {what} named '{query}'.{hint}")


async def current_grid(bridge: Bridge) -> dict[str, Any]:
    """The grid the player is controlling, else the nearest usable one."""
    context = await bridge.call("player.context")
    controlled = context.get("controlled") or {}
    if controlled.get("gridId"):
        return {"id": controlled["gridId"], "name": controlled.get("gridName")}
    nearest = context.get("nearestGrid")
    if nearest:
        return {"id": nearest["gridId"], "name": nearest.get("gridName")}
    raise ResolveError("You're not in or near a grid you own. Name the grid (see list_grids).")


async def grid(bridge: Bridge, query: str | None) -> dict[str, Any]:
    """Returns {"id", "name"}. None or "" means the current grid."""
    if not query or query.strip().lower() in ("current", "this", "my ship", "this ship"):
        return await current_grid(bridge)
    if _is_id(query):
        return {"id": query.strip(), "name": None}
    listing = await bridge.call("grids.list", {})
    return _pick(query, listing["grids"], "grid")


async def block(bridge: Bridge, query: str, grid_query: str | None = None) -> dict[str, Any]:
    """Returns a block summary dict. Ids pass through; names are searched on the given (or current) grid."""
    if _is_id(query):
        return {"id": query.strip(), "name": None}
    target = await grid(bridge, grid_query)
    listing = await bridge.call("blocks.list", {"gridId": target["id"], "nameContains": query.strip(), "limit": 50})
    if listing["blocks"]:
        return _pick(query, listing["blocks"], "block")
    # No substring match: offer fuzzy suggestions from the whole grid.
    everything = await bridge.call("blocks.list", {"gridId": target["id"], "limit": 1000})
    return _pick(query, everything["blocks"], f"block on '{target.get('name') or target['id']}'")


__all__ = ["ResolveError", "BridgeError", "grid", "block", "current_grid"]
