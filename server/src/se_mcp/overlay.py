"""HUD overlay: ready-made panels built from overlay elements (see overlay.set in protocol.md)."""

from __future__ import annotations

from typing import Any

# What a ship status panel can show: (label, element fields). Bars read ship.hud; {path} fills text.
GAUGES: dict[str, dict[str, Any]] = {
    "hydrogen": {"kind": "bar", "valuePath": "hydrogen.fill", "max": 100, "text": "H2 {pct}%", "color": "#4FC3F7", "lowBelow": 25},
    "oxygen": {"kind": "bar", "valuePath": "oxygen.fill", "max": 100, "text": "O2 {pct}%", "color": "#81D4FA", "lowBelow": 25},
    "battery": {"kind": "bar", "valuePath": "batteries.storedMWh", "maxPath": "batteries.maxMWh",
                "text": "Battery {pct}%  {batteries.outMW} MW out", "color": "#FFD54F", "lowBelow": 20},
    "uranium": {"kind": "text", "text": "Uranium {reactors.uraniumKg} kg  ({reactors.outMW}/{reactors.maxMW} MW)"},
    "cargo": {"kind": "bar", "valuePath": "cargo.fill", "max": 100, "text": "Cargo {pct}%", "color": "#A5D6A7"},
    "speed": {"kind": "text", "text": "Speed {speed} m/s"},
}
DEFAULT_SHOW = ["hydrogen", "battery", "uranium"]


def ship_panel(prefix: str, grid_id: str, title: str, show: list[str], x: float, y: float,
               width: float = 0.17, scale: float = 0.6) -> list[dict[str, Any]]:
    """A titled panel of live gauges for one ship, element ids '<prefix>-...'."""
    unknown = [s for s in show if s not in GAUGES]
    if unknown:
        raise ValueError(f"Unknown gauge(s) {', '.join(unknown)}. Choose from: {', '.join(GAUGES)}")
    bind = {"method": "ship.hud", "params": {"gridId": grid_id}, "every": 1}
    row, pad = 0.033, 0.007
    elements: list[dict[str, Any]] = [
        {"id": f"{prefix}-panel", "kind": "rect", "x": x, "y": y, "w": width, "h": 0.035 + row * len(show), "color": "#00000090"},
        {"id": f"{prefix}-title", "kind": "text", "x": x + pad, "y": y + 0.005, "text": title.upper(), "scale": scale + 0.1, "color": "#9BD4FF"},
    ]
    for i, name in enumerate(show):
        top = y + 0.035 + row * i
        e = {"id": f"{prefix}-{name}", "x": x + pad, "y": top, "scale": scale, "bind": bind, **GAUGES[name]}
        if e["kind"] == "bar":
            e.update(w=width - 2 * pad, h=0.025)
            if "lowBelow" in e:
                e["lowColor"] = "#FF5050"
        else:
            e["y"] = top + 0.003
        elements.append(e)
    return elements
