"""Offscreen renders from the plugin (camera blocks, free viewpoints, the player's view without HUD) and
camera/sensor formatting."""

from __future__ import annotations

import base64
import io
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from .bridge import Bridge
from .steering import cross, normalize

Vec = list[float]

# Viewpoints around a ship, in the cockpit's frame: (forward, up, right) weights for the camera position.
SIDES: dict[str, tuple[float, float, float]] = {
    "front": (1, 0.25, 0.3), "back": (-1, 0.25, -0.3), "left": (0.2, 0.25, -1), "right": (0.2, 0.25, 1),
    "top": (0.3, 1, 0), "bottom": (0.3, -1, 0), "front-left": (0.8, 0.45, -0.8), "front-right": (0.8, 0.45, 0.8),
    "rear-left": (-0.8, 0.45, -0.8), "rear-right": (-0.8, 0.45, 0.8),
}


def captures_dir() -> Path:
    override = os.environ.get("SE_MCP_CAPTURES")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", Path.home())) / "SpaceEngineers" / "SEMcp" / "captures"


def decode(reply: dict[str, Any], quality: int = 85) -> bytes:
    """The plugin sends the game's own encoding (padded with zeros); re-encode as a clean JPEG."""
    raw = base64.b64decode(reply["image"])
    with PILImage.open(io.BytesIO(raw)) as image:
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=quality)
        return out.getvalue()


def save(data: bytes, label: str) -> Path:
    folder = captures_dir()
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]+", "-", label).strip("-").lower() or "capture"
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.jpg"
    path.write_bytes(data)
    return path


async def capture(bridge: Bridge, params: dict[str, Any], label: str | None = None) -> tuple[bytes, dict[str, Any], Path]:
    reply = await bridge.call("camera.capture", params, timeout=20)
    data = decode(reply)
    return data, reply, save(data, label or reply.get("source") or "capture")


def ship_viewpoint(motion: dict[str, Any], side: str, distance: float | None) -> dict[str, Any]:
    """Camera position and direction for a picture of the whole ship from one side."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {', '.join(SIDES)}")
    cockpit = motion.get("cockpit") or {}
    forward = cockpit.get("forward") or motion["forward"]
    up = cockpit.get("up") or motion["up"]
    right = normalize(cross(forward, up))
    f, u, r = SIDES[side]
    offset = normalize([f * forward[i] + u * up[i] + r * right[i] for i in range(3)])
    # Far enough that the bounding sphere fits a ~60 degree view, plus a margin.
    # radius is the half-diagonal of the bounding box, which overstates a long ship; 0.8 frames it snugly.
    dist = distance or max(15.0, 0.8 * motion["radius"] / math.tan(math.radians(30)))
    center = motion["center"]
    position = [center[i] + offset[i] * dist for i in range(3)]
    view_up = up if abs(sum(offset[i] * up[i] for i in range(3))) < 0.9 else forward
    return {"position": position, "forward": [-x for x in offset], "up": view_up, "fovDegrees": 60}


def scan_text(result: dict[str, Any]) -> str:
    if not result["scanned"]:
        wait = result.get("msUntilScan") or 0
        why = f"needs {wait / 1000:.1f} s more charge" if wait > 0 else "the target is outside the camera's view cone"
        prefix = "Raycast enabled on the camera just now. " if result.get("enabledRaycast") else ""
        return f"{prefix}{result['camera']} can't scan {result['distance']:.0f} m yet: {why} (charge {result['scanRangeLeft']:.0f} m)."
    hit = result.get("hit")
    if not hit:
        return f"{result['camera']}: nothing within {result['distance']:.0f} m. Charge left {result['scanRangeLeft']:.0f} m."
    return (f"{result['camera']} hit {entity_line(hit)}\n"
            f"Charge left {result['scanRangeLeft']:.0f} m.")


def entity_line(e: dict[str, Any]) -> str:
    size = "x".join(f"{s:.0f}" for s in e["size"])
    moving = f", moving {e['speed']:.1f} m/s" if e.get("speed", 0) > 0.1 else ""
    # The game prefixes player names with an icon glyph from Unicode's private-use area; drop those.
    name = re.sub("[-]", "", e.get("name") or "").strip() or e["type"]
    return f"{name} ({e['type']}, {e['relation']}) {e['distance']:.0f} m away, {size} m{moving}"


def sensors_text(listing: dict[str, Any]) -> str:
    sensors = listing["sensors"]
    if not sensors:
        return "No sensor blocks."
    lines = []
    for s in sensors:
        state = "TRIGGERED" if s["active"] else ("idle" if s["working"] else "not working")
        l, r, t, b, f, k = s["extents"]
        lines.append(f"- {s['name']}: {state}; field L{l} R{r} T{t} B{b} F{f} Bk{k} m; detects {', '.join(s['detects']) or 'nothing'} "
                     f"({', '.join(s['relations']) or 'no one'})")
        for d in s["detected"]:
            lines.append(f"    · {entity_line(d)}")
    return "\n".join(lines)
