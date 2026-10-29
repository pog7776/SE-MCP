"""Compact, readable text for the model. Raw plugin JSON is verbose; these keep the useful parts."""

from __future__ import annotations

from typing import Any


def _num(value: float | int | None, unit: str = "") -> str:
    if value is None:
        return "?"
    if abs(value) >= 1_000_000:
        text = f"{value / 1_000_000:.2f}M"
    elif abs(value) >= 10_000:
        text = f"{value / 1000:.1f}k"
    elif float(value).is_integer():
        text = f"{int(value)}"
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def _amount(value: float) -> str:
    """Item counts: whole numbers with separators, fractions only for small amounts (ore and ingots are kg)."""
    if value >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _pos(p: dict[str, float] | None) -> str:
    return f"({p['x']:.0f}, {p['y']:.0f}, {p['z']:.0f})" if p else "?"


def grid_line(g: dict[str, Any]) -> str:
    kind = "station" if g.get("isStatic") else f"{g.get('size')} ship"
    if not g.get("accessible", True):
        return f"- {g['name']} [{kind}, {g.get('relation')}] {_num(g.get('distance'), ' m')} away, no access (id {g['id']})"
    moving = f", {g['speed']} m/s" if g.get("speed") else ""
    return (
        f"- {g['name']} [{kind}, {g.get('blocks')} blocks, {g.get('relation')}] "
        f"{_num(g.get('distance'), ' m')} away{moving} (id {g['id']})"
    )


def grids(listing: dict[str, Any]) -> str:
    items = listing.get("grids") or []
    if not items:
        return f"No grids within {_num(listing.get('range'), ' m')}."
    return f"{len(items)} grid(s) within {_num(listing.get('range'), ' m')}:\n" + "\n".join(grid_line(g) for g in items)


def ship(status: dict[str, Any]) -> str:
    s = status["summary"]
    kind = "station" if s.get("isStatic") else f"{s.get('size')} ship"
    lines = [f"{s['name']} ({kind}, id {s['id']})"]

    subgrids = status.get("grids") or []
    total_blocks = sum(g.get("blocks", 0) for g in subgrids)
    sub = f" across {len(subgrids)} grids" if len(subgrids) > 1 else ""
    lines.append(f"Blocks: {total_blocks}{sub}, {status.get('terminalBlocks')} terminal ({status.get('functionalCount')} functional)")
    lines.append(f"Mass: {_num(status.get('mass'), ' kg')}, speed {s.get('speed', 0)} m/s, position {_pos(s.get('position'))}")
    if status.get("docked"):
        lines.append("Docked: " + ", ".join(f"{d['name']} ({d['id']})" for d in status["docked"]))

    power = status.get("power") or {}
    b = power.get("batteries") or {}
    if b.get("count"):
        lines.append(
            f"Batteries: {b['count']}x, {b['fill']}% ({b['storedMWh']}/{b['maxMWh']} MWh), "
            f"in {b['inputMW']} MW, out {b['outputMW']} MW"
        )
    for p in power.get("producers") or []:
        lines.append(f"{p['type']}: {p['working']}/{p['count']} working, {p['outputMW']}/{p['maxMW']} MW")

    gas = status.get("gas") or {}
    for name in ("hydrogen", "oxygen"):
        g = gas.get(name) or {}
        if g.get("count"):
            lines.append(f"{name.capitalize()} tanks: {g['count']}x, {g['fill']}% ({_num(g['storedL'], ' L')}/{_num(g['capacityL'], ' L')})")

    c = status.get("cargo") or {}
    if c.get("containers"):
        lines.append(f"Cargo: {c['fill']}% full ({_num(c['volumeL'], ' L')}/{_num(c['maxVolumeL'], ' L')}) in {c['containers']} containers")

    d = status.get("damage") or {}
    if d.get("count"):
        lines.append(f"Damaged blocks: {d['count']}")
        for blk in d.get("blocks") or []:
            missing = ", ".join(f"{n}x {k}" for k, n in (blk.get("missing") or {}).items())
            lines.append(f"  - {blk['name']}: {blk['integrity']}%" + (f" (needs {missing})" if missing else ""))
    else:
        lines.append("No damage.")
    return "\n".join(lines)


def block_line(b: dict[str, Any]) -> str:
    flags = []
    if b.get("enabled") is False:
        flags.append("OFF")
    if not b.get("functional"):
        flags.append("NOT FUNCTIONAL")
    elif not b.get("working") and b.get("enabled") is not False:
        flags.append("not working")
    if b.get("integrity", 100) < 100:
        flags.append(f"{b['integrity']}% integrity")
    if not b.get("accessible", True):
        flags.append("no access")
    state = f" [{', '.join(flags)}]" if flags else ""
    return f"- {b['name']} ({b.get('displayType') or b.get('type')}){state} id {b['id']}"


def blocks(listing: dict[str, Any], grid_name: str | None) -> str:
    items = listing.get("blocks") or []
    where = f" on {grid_name}" if grid_name else ""
    if not items:
        return f"No matching blocks{where}."
    shown = f"{len(items)} of {listing['total']}" if listing["total"] > len(items) else str(listing["total"])
    return f"{shown} block(s){where}:\n" + "\n".join(block_line(b) for b in items)


def inventory_items(totals: list[dict[str, Any]]) -> list[str]:
    by_type: dict[str, list[str]] = {}
    for t in totals:
        by_type.setdefault(t["type"], []).append(f"{t['subtype']} {_amount(t['amount'])}")
    return [f"{kind}: {', '.join(items)}" for kind, items in by_type.items()]


def inventory(inv: dict[str, Any], title: str, item_filter: str | None = None) -> str:
    totals = inv.get("totals") or []
    if item_filter:
        f = item_filter.lower()
        totals = [t for t in totals if f in t["subtype"].lower() or f in t["type"].lower()]
    if inv["maxVolumeL"] > 1e9:  # creative mode: unlimited inventory
        lines = [f"{title}: {_num(inv['volumeL'], ' L')} (unlimited capacity)"]
    else:
        lines = [f"{title}: {inv['fill']}% full ({_num(inv['volumeL'], ' L')}/{_num(inv['maxVolumeL'], ' L')})"]
    if not totals:
        lines.append(f"No items matching '{item_filter}'." if item_filter else "Empty.")
    else:
        lines += inventory_items(totals)
    return "\n".join(lines)


# Terminal properties that are noise for most questions (LCD/script styling on many blocks).
_BORING_PROPERTIES = {
    "ShowInTerminal", "ShowInInventory", "ShowInToolbarConfig", "ShowOnHUD", "Name", "Content",
    "ScriptForegroundColor", "ScriptBackgroundColor", "Font", "FontSize", "FontColor", "alignment",
    "TextPaddingSlider", "BackgroundColor", "ChangeIntervalSlider", "PreserveAspectRatio",
}


def block_detail(detail: dict[str, Any], all_properties: bool = False) -> str:
    b = detail["block"]
    lines = [block_line(b).lstrip("- "), f"Type: {b['type']}/{b['subtype']} on grid {detail.get('gridName')}"]
    if detail.get("detailedInfo"):
        lines.append("Info:\n  " + detail["detailedInfo"].strip().replace("\n", "\n  "))
    if detail.get("customInfo"):
        lines.append("Custom info:\n  " + detail["customInfo"].strip().replace("\n", "\n  "))
    if detail.get("missingComponents"):
        lines.append("Missing: " + ", ".join(f"{n}x {k}" for k, n in detail["missingComponents"].items()))

    props = [p for p in detail.get("properties") or [] if all_properties or p["id"] not in _BORING_PROPERTIES]
    if props:
        lines.append("Properties: " + ", ".join(f"{p['id']}={p['value']}" for p in props))
    actions = detail.get("actions") or []
    if actions:
        lines.append("Actions: " + ", ".join(a["id"] for a in actions))

    for inv in detail.get("inventories") or []:
        title = f"Inventory {inv['index']}" if len(detail["inventories"]) > 1 else "Inventory"
        lines.append(inventory({"fill": inv["fill"], "volumeL": inv["volume"], "maxVolumeL": inv["maxVolume"],
                                "totals": inv["items"]}, title))
    if detail.get("customData"):
        lines.append("CustomData:\n" + detail["customData"])
    return "\n".join(lines)


def suit(status: dict[str, Any]) -> str:
    if not status.get("alive"):
        return "No living character (dead or respawning)."
    on_off = lambda v: "on" if v else "off"  # noqa: E731
    return "\n".join([
        f"Health {status['health']}, energy {status['energy']}%, oxygen {status['oxygen']}%, hydrogen {status['hydrogen']}%",
        f"Outside air: {status['environmentOxygen']}% oxygen, gravity {status['gravityG']} g",
        f"Jetpack {on_off(status['jetpack'])}, dampeners {on_off(status['dampeners'])}, "
        f"helmet {'closed' if status['helmet'] else 'open'}, lights {on_off(status['lights'])}",
        f"{status['movement']}, {status['speed']} m/s at {_pos(status.get('position'))}, inventory {status['inventoryFill']}% full",
    ])


def context(ctx: dict[str, Any]) -> str:
    lines = []
    c = ctx.get("controlled") or {}
    if c.get("gridId"):
        lines.append(f"Controlling: {c.get('blockName')} ({c.get('kind')}) on {c.get('gridName')} (grid {c['gridId']})")
    else:
        lines.append("On foot.")
    near = ctx.get("nearestGrid")
    if near and near.get("gridId") != c.get("gridId"):
        lines.append(f"Nearest own grid: {near['gridName']} ({near['distance']} m, id {near['gridId']})")
    look = ctx.get("lookingAt")
    if look:
        if look["kind"] == "grid":
            blk = look.get("block") or {}
            what = f"{blk.get('name')} on " if blk else ""
            lines.append(
                f"Looking at: {what}{look['gridName']} ({look['relation']}, {look['distance']} m)"
                + (f", block id {blk['id']}" if blk.get("id") else "")
            )
        else:
            lines.append(f"Looking at: {look.get('name')} ({look['kind']}, {look['distance']} m)")
    return "\n".join(lines)


def production(data: dict[str, Any]) -> str:
    blocks = data.get("blocks") or []
    if not blocks:
        return "No production blocks."
    lines = []
    for b in blocks:
        if not b.get("enabled"):
            state = "OFF"
        elif b.get("producing"):
            state = "producing"
        elif not b.get("queue"):
            state = "idle, queue empty"
        else:
            state = "NOT producing (blocked: missing materials, full output or no power?)"
        extra = []
        if b.get("mode"):
            extra.append(b["mode"].lower())
        if b.get("repeating"):
            extra.append("repeating")
        if b.get("cooperative"):
            extra.append("cooperative")
        extra_text = f", {', '.join(extra)}" if extra else ""
        lines.append(f"- {b['name']} ({b['displayType']}{extra_text}): {state}")
        for item in b.get("queue") or []:
            lines.append(f"    {item['blueprint']} x{_amount(item['amount'])}")
        fills = [f"{label} {b[key]['fill']}%" for key, label in (("input", "input"), ("output", "output")) if b.get(key)]
        if fills:
            lines.append(f"    inventories: {', '.join(fills)}")
    return "\n".join(lines)


def change_result(result: dict[str, Any], verb: str) -> str:
    """Summary of a block.action / block.set_property reply."""
    lines = []
    if "applied" in result:
        applied = result["applied"]
        lines.append(f"{verb} on {len(applied)} block(s)" + (f": {', '.join(applied)}" if applied else ""))
    if "changed" in result:
        for c in result["changed"]:
            lines.append(f"{c['block']}: {result['property']} {c['before']} -> {c['after']}")
        if not result["changed"]:
            lines.append(f"Nothing changed ({verb}).")
    for s in result.get("skipped") or []:
        lines.append(f"Skipped {s}")
    return "\n".join(lines)
