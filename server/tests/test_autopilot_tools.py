from __future__ import annotations

from typing import Any

from se_mcp.autopilot import parse_coords
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, FakeGameError, default_handlers

RC_ID = "80000001"


class FakeAutopilot:
    def __init__(self, rcs: int = 1):
        self.rcs = rcs
        self.rc: dict[str, Any] = {
            "id": RC_ID, "name": "Remote Control", "gridId": "1001", "gridName": "Miner", "working": True,
            "autopilot": False, "flightMode": "Patrol", "direction": "Forward", "speedLimit": 100.0,
            "collisionAvoidance": False, "dockingMode": False, "position": [0, 0, 0], "speed": 0.0, "gravityG": 0,
            "currentWaypoint": None, "waypoints": [], "remoteControlled": False,
        }
        self.gps = [{"hash": 7, "name": "Home Base", "description": "", "coords": [1000, 0, 0], "distance": 1000,
                     "showOnHud": True, "color": "#FFFFFF"}]

    def handlers(self) -> dict[str, Any]:
        handlers = default_handlers()
        blocks_list = handlers["blocks.list"]

        def blocks(p: dict) -> dict:
            if p.get("type") == "RemoteControl":
                rcs = [{"id": str(int(RC_ID) + i), "name": f"Remote Control {i}", "type": "RemoteControl"} for i in range(self.rcs)]
                return {"total": len(rcs), "offset": 0, "blocks": rcs}
            return blocks_list(p)

        def fly_to(p: dict) -> dict:
            name, coords = p["name"], p["target"]
            self.rc.update(autopilot=True, flightMode="OneWay", speedLimit=p["speedLimit"],
                           collisionAvoidance=p["collisionAvoidance"], dockingMode=p["precision"],
                           waypoints=[{"name": name, "coords": coords}],
                           currentWaypoint={"name": name, "coords": coords, "distance": 1000.0})
            return self.rc

        def stop(p: dict) -> dict:
            self.rc["autopilot"] = False
            return self.rc

        def gps_remove(p: dict) -> dict:
            match = [g for g in self.gps if g["hash"] == p["hash"]]
            if not match:
                raise FakeGameError("not_found", "No such GPS entry")
            self.gps.remove(match[0])
            return {"removed": match[0]["name"]}

        handlers.update({
            "blocks.list": blocks, "rc.get": lambda p: self.rc, "rc.fly_to": fly_to, "rc.stop": stop,
            "gps.list": lambda p: {"gps": self.gps}, "gps.remove": gps_remove,
            "gps.add": lambda p: {"hash": 9, "name": p["name"], "description": p["description"], "coords": p["coords"],
                                  "distance": 0, "showOnHud": True, "color": "#FFFFFF"},
        })
        return handlers


async def run(fake: FakeAutopilot, tool: str, **args: Any) -> tuple[str, FakeGame]:
    async with FakeGame(fake.handlers()) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
            return "\n".join(c.text for c in result.content if hasattr(c, "text")), game
        finally:
            await bridge.close()


def test_parse_coords():
    assert parse_coords("GPS:Base:1.5:-2:3e2:#FF75C9F1:") == ("Base", [1.5, -2.0, 300.0])
    assert parse_coords("10, 20, -30") == (None, [10.0, 20.0, -30.0])
    assert parse_coords("(1 2 3)") == (None, [1.0, 2.0, 3.0])
    assert parse_coords("Home Base") is None


async def test_fly_to_a_gps_entry_with_the_only_rc():
    text, game = await run(FakeAutopilot(), "fly_to", target="home", speed_limit=20)
    [params] = [p for m, p in game.calls if m == "rc.fly_to"]
    assert params == {"blockId": RC_ID, "target": [1000, 0, 0], "name": "Home Base", "speedLimit": 20,
                      "collisionAvoidance": True, "precision": False}
    assert "Heading to Home Base: 1000 m away" in text


async def test_fly_to_coordinates():
    _, game = await run(FakeAutopilot(), "fly_to", target="1, 2, 3")
    [params] = [p for m, p in game.calls if m == "rc.fly_to"]
    assert params["target"] == [1.0, 2.0, 3.0]


async def test_unknown_gps_lists_the_options():
    text, game = await run(FakeAutopilot(), "fly_to", target="Moon")
    assert "No GPS entry matching 'Moon'" in text and "Home Base" in text
    assert not any(m == "rc.fly_to" for m, _ in game.calls)


async def test_several_rcs_must_be_named():
    text, _ = await run(FakeAutopilot(rcs=2), "autopilot_status")
    assert "Several Remote Controls" in text


async def test_arrival_is_reported():
    fake = FakeAutopilot()
    fake.rc["currentWaypoint"] = {"name": "Home Base", "coords": [1000, 0, 0], "distance": 2.0}
    text, _ = await run(fake, "autopilot_status")
    assert "Arrived at Home Base" in text


async def test_gps_add_and_remove():
    text, _ = await run(FakeAutopilot(), "add_gps", name="Ore", coords="GPS:x:5:6:7:")
    assert text.startswith("Added Ore") and "GPS:Ore:5.00:6.00:7.00:" in text
    fake = FakeAutopilot()
    text, _ = await run(fake, "remove_gps", name="home base")
    assert text == "Removed GPS 'Home Base'." and not fake.gps
