"""A compact "HUD" for agents: one ship's systems at a glance, with warnings for states that trip things up
(thrusters off, dampeners off, batteries on recharge, no fuel...)."""

from __future__ import annotations

from typing import Any


def _km(m: float) -> str:
    return f"{m / 1000:g} km" if m >= 1000 else f"{m:.0f} m"


def warnings(h: dict[str, Any]) -> list[str]:
    w: list[str] = []
    parked = bool(h["docked"]) or h["gearLocked"] > 0 or h["isStatic"]
    t = h["thrusters"]
    if t["count"] and t["on"] < t["count"]:
        off = t["count"] - t["on"]
        w.append(f"{off}/{t['count']} thrusters OFF" + (" (normal while parked; dock/undock turn them on to fly)" if parked
                                                        else ": the ship can't manoeuvre that way"))
    elif t["on"] > t["working"]:
        w.append(f"{t['on'] - t['working']} thrusters on but not working (no power, fuel or damaged)")
    if not h["isStatic"] and h["dampeners"] is False and not parked:
        w.append("Dampeners OFF: the ship will drift" + (f" (moving {h['speed']:.1f} m/s)" if h["speed"] > 0.1 else ""))
    if h["gyros"]["count"] and h["gyros"]["on"] == 0 and not h["isStatic"]:
        w.append("All gyroscopes OFF: the ship can't turn")
    if h["gyros"]["overriding"] or h["thrustOverrides"]:
        w.append(f"Overrides active: {h['gyros']['overriding']} gyros, {h['thrustOverrides']} thrusters (a script or autopilot is flying?)")
    if h["autopilot"]:
        w.append("Autopilot ON: " + ", ".join(h["autopilot"]))
    b, r = h["batteries"], h["reactors"]
    if b["count"]:
        fill = 100 * b["storedMWh"] / b["maxMWh"] if b["maxMWh"] else 0
        if b["recharge"] == b["count"] and not r["working"] and not h["solar"]["count"]:
            w.append("All batteries on Recharge and nothing else makes power: no output")
        elif fill < 15:
            w.append(f"Batteries low ({fill:.0f}%)")
    if r["count"] and r["uraniumKg"] <= 0:
        w.append("Reactors have no uranium")
    if not b["working"] and not r["working"] and not h["solar"]["outMW"] and (b["count"] or r["count"]):
        w.append("NO POWER")
    if h["hydrogen"] and h["hydrogen"]["fill"] < 15:
        w.append(f"Hydrogen low ({h['hydrogen']['fill']:.0f}%)")
    if h["hydrogen"] and h["hydrogen"]["stockpile"]:
        w.append(f"{h['hydrogen']['stockpile']} hydrogen tank(s) on Stockpile (they won't supply thrusters)")
    if h["cargo"]["containers"] and h["cargo"]["fill"] > 90:
        w.append(f"Cargo nearly full ({h['cargo']['fill']:.0f}%)")
    if h["damaged"]:
        w.append(f"{h['damaged']} damaged or unfinished block(s): see damage_report")
    wpn = h["weapons"]
    if wpn["count"] and wpn["working"] < wpn["count"]:
        w.append(f"{wpn['count'] - wpn['working']}/{wpn['count']} weapons not working")
    if wpn["shooting"]:
        w.append(f"{wpn['shooting']} weapon(s) FIRING")
    if h["antennas"] and not any(a["broadcasting"] and a["working"] for a in h["antennas"]) and not h["beacons"]:
        w.append("Not broadcasting: invisible on radio (and out of remote reach in fair mode)")
    return w


def hud_text(h: dict[str, Any]) -> str:
    kind = "station" if h["isStatic"] else f"{h['size']} ship"
    # Static grids have no meaningful physics mass.
    mass = "" if h["isStatic"] else f", {h['mass'] / 1000:,.1f} t, {h['speed']:.1f} m/s"
    lines = [f"{h['name'].upper()}: {kind}{mass}, {_km(h['distance'])} away"
             + (f", {h['grids']} grids" if h["grids"] > 1 else "")]
    for s in h.get("subgrids") or []:
        j = s.get("joint")
        if not j:
            how = "attached (joint not found)"
        elif j["kind"] == "piston":
            how = f"on piston '{j['name']}' at {j['extensionM']:g}/{j['maxM']:g} m"
        else:
            how = f"on {j['kind']} '{j['name']}' at {j['angleDeg']:g} deg" + (", locked" if j.get("locked") else "")
        if j and j.get("onGrid") not in (None, h["name"]):
            how += f" (mounted on {j['onGrid']})"
        if j and not j["working"]:
            how += " (joint not working)"
        lines.append(f"  subgrid {s['name']} ({s['size']}, {s['blocks']} blocks) {how}")
    if h["docked"]:
        lines.append("Docked: " + "; ".join(f"{d['connector']} -> {d['to']} on {d['ship']}" for d in h["docked"]))
    if h["gear"]:
        lines.append(f"Landing gear: {h['gearLocked']}/{h['gear']} locked")
    if not h["isStatic"]:
        t, g = h["thrusters"], h["gyros"]
        damp = {True: "ON", False: "OFF", None: "-"}[h["dampeners"]]
        lines.append(f"Flight: thrusters {t['working']}/{t['count']} working ({t['on']} on), gyros {g['working']}/{g['count']}, "
                     f"dampeners {damp}, pilot {h['pilot'] or 'none'}, autopilot {'ON' if h['autopilot'] else 'off'}")
    b, r = h["batteries"], h["reactors"]
    power = []
    if b["count"]:
        fill = 100 * b["storedMWh"] / b["maxMWh"] if b["maxMWh"] else 0
        power.append(f"batteries {fill:.0f}% ({b['storedMWh']:.1f} MWh, in {b['inMW']:.1f}/out {b['outMW']:.1f} MW)")
    if r["count"]:
        power.append(f"reactors {r['working']}/{r['count']} at {r['outMW']:.1f}/{r['maxMW']:.1f} MW, {r['uraniumKg']:g} kg uranium")
    if h["solar"]["count"]:
        power.append(f"solar {h['solar']['outMW']:.2f} MW")
    if power:
        lines.append("Power: " + ", ".join(power))
    gas = [f"{label} {g['fill']:.0f}% ({g['count']} tanks)" for label, g in (("H2", h["hydrogen"]), ("O2", h["oxygen"])) if g]
    if gas:
        lines.append("Gas: " + ", ".join(gas))
    misc = []
    if h["cargo"]["containers"]:
        misc.append(f"cargo {h['cargo']['fill']:.0f}% ({h['cargo']['containers']} containers)")
    wpn = h["weapons"]
    if wpn["count"]:
        misc.append(f"weapons {wpn['working']}/{wpn['count']} working" + (f", {wpn['targeting']} turrets on target" if wpn["targeting"] else ""))
    radio = [f"{a['name']} {_km(a['radius'])}{'' if a['broadcasting'] and a['working'] else ' (quiet)'}" for a in h["antennas"]]
    radio += [f"beacon {_km(x['radius'])}{'' if x['working'] else ' (off)'}" for x in h["beacons"]]
    if radio:
        misc.append("radio: " + ", ".join(radio))
    if misc:
        lines.append(", ".join(misc).capitalize())
    lines.append("Damage: none" if not h["damaged"] else f"Damage: {h['damaged']} blocks")
    warn = warnings(h)
    if warn:
        lines.append("Warnings:")
        lines += [f"! {x}" for x in warn]
    return "\n".join(lines)
