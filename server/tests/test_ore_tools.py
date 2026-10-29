from __future__ import annotations

from se_mcp import ore

DET = [{"id": "1010001", "name": "Ore Detector", "working": True, "range": 100, "broadcastUsingAntennas": True}]


def marker(name: str, distance: float, body: str = "BaseAsteroid") -> dict:
    return {"ore": name, "material": name + "_01", "position": [distance, 0, 0], "distance": distance, "body": body,
            "detectorId": "1010001"}


def test_scan_text_groups_by_ore_nearest_first():
    r = {"visible": True, "controlling": None, "detectors": DET, "total": 3,
         "markers": [marker("Ice", 55), marker("Iron", 61), marker("Ice", 70)]}
    text = ore.scan_text(r)
    assert "- Ice: 2 marker(s) on BaseAsteroid; nearest 55 m at 55, 0, 0" in text
    assert text.index("- Ice") < text.index("- Iron")
    assert "Nearest 15 iron markers:\n  61 m" in ore.scan_text(r, "iron")


def test_why_empty_explains():
    base = {"visible": True, "controlling": None, "total": 0, "markers": []}
    assert ore.scan_text({**base, "detectors": []}) == "This ship has no ore detector."
    quiet = [{**DET[0], "broadcastUsingAntennas": False}]
    assert "Sit in a cockpit" in ore.scan_text({**base, "detectors": quiet})
    assert ore.scan_text({**base, "detectors": DET}, "gold") == "No gold found. No ore within the detector's range (100 m)."
