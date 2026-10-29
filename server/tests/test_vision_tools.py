from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from PIL import Image as PILImage

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server
from se_mcp.vision import ship_viewpoint

from .fake_bridge import TOKEN, FakeGame, default_handlers

CAMERA_ID = "90000001"


def fake_jpeg() -> str:
    out = io.BytesIO()
    PILImage.new("RGB", (64, 36), (200, 30, 30)).save(out, format="JPEG")
    # The game pads its encoder's buffer with zeros; the tool has to cope.
    return base64.b64encode(out.getvalue() + b"\0" * 100).decode()


def handlers() -> dict[str, Any]:
    h = default_handlers()
    blocks_list = h["blocks.list"]

    def blocks(p: dict) -> dict:
        if "front" in (p.get("nameContains") or "").lower():
            return {"total": 1, "offset": 0, "blocks": [{"id": CAMERA_ID, "name": "Camera Front", "type": "CameraBlock"}]}
        return blocks_list(p)

    h.update({
        "blocks.list": blocks,
        "camera.list": lambda p: {"cameras": [{"id": CAMERA_ID, "name": "Camera Front", "working": True, "active": False,
                                               "raycastEnabled": True, "scanRange": 4000}]},
        "camera.capture": lambda p: {"source": "Camera Front", "format": "jpeg", "width": 64, "height": 36,
                                     "fovDegrees": 60, "position": [0, 0, 0], "image": fake_jpeg()},
        "camera.raycast": lambda p: {"camera": "Camera Front", "enabledRaycast": False, "scanned": True, "distance": p["distance"],
                                     "scanRangeLeft": 1000, "msUntilScan": 0, "hit": {
                                         "id": "5", "name": "Pirate Drone", "type": "SmallGrid", "relation": "enemy",
                                         "distance": 812.4, "size": [10, 6, 14], "speed": 31.2}},
        "grid.motion": lambda p: {"center": [0, 0, 0], "radius": 50, "forward": [0, 0, -1], "up": [0, 1, 0],
                                  "cockpit": {"name": "Helm", "forward": [1, 0, 0], "up": [0, 1, 0]}},
        "sensors.get": lambda p: {"sensors": [{"id": "6", "name": "Door Sensor", "working": True, "active": True,
                                               "extents": [5, 5, 3, 3, 5, 5], "maxRange": 50, "detects": ["players"],
                                               "relations": ["owner"], "detected": [{"name": "dingus", "type": "CharacterHuman",
                                                                                     "relation": "owner", "distance": 2.1, "size": [1, 2, 1], "speed": 0}]}]},
    })
    return h


@pytest.fixture
def captures(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_MCP_CAPTURES", str(tmp_path))
    return tmp_path


async def run(tool: str, **args: Any):
    async with FakeGame(handlers()) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            return (await create_server(bridge).call_tool(tool, args)).content, game
        finally:
            await bridge.close()


async def test_camera_view_returns_and_saves_an_image(captures):
    content, game = await run("camera_view", camera="front")
    text, image = content
    assert text.text.startswith("Camera Front: 64x36")
    assert image.mime_type == "image/jpeg"
    [saved] = list(captures.iterdir())
    assert saved.read_bytes().endswith(b"\xff\xd9")  # re-encoded, padding gone
    assert ("camera.capture", {"blockId": CAMERA_ID, "width": 1280}) in game.calls
    assert not any(m == "notify" for m, _ in game.calls)


async def test_ship_photo_frames_the_ship_from_the_cockpit_frame(captures):
    _, game = await run("ship_photo", side="front")
    [params] = [p for m, p in game.calls if m == "camera.capture"]
    # Cockpit faces +x, so "front" puts the camera out along +x looking back at the ship.
    assert params["position"][0] > 50 and params["forward"][0] < -0.9


def test_ship_viewpoint_rejects_unknown_sides():
    with pytest.raises(ValueError):
        ship_viewpoint({"center": [0, 0, 0], "radius": 1, "forward": [0, 0, -1], "up": [0, 1, 0]}, "sideways", None)


async def test_camera_scan_and_sensors():
    content, _ = await run("camera_scan", camera="front", distance_m=1000)
    assert "Pirate Drone (SmallGrid, enemy) 812 m away, 10x6x14 m, moving 31.2 m/s" in content[0].text
    content, _ = await run("sensors")
    assert "Door Sensor: TRIGGERED" in content[0].text
    assert "· dingus (CharacterHuman, owner)" in content[0].text
