"""Guiding the player: the detour search with fake geometry, and the guide_to / stop_guide tools."""

from __future__ import annotations

from typing import Any

from se_mcp import guide
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import SHIP_ID, STATION_ID, TOKEN, FakeGame, default_handlers


def sphere_blocker(center: list[float], radius: float, name: str = "Rock", entity_id: str = "999"):
    """blocked(a, b) for one sphere in the way: the first point where the segment enters it."""

    async def blocked(a: list[float], b: list[float]) -> dict[str, Any] | None:
        d = guide.sub(b, a)
        length = guide.norm(d)
        u = guide.unit(d)
        f = guide.sub(a, center)
        bq = sum(f[i] * u[i] for i in range(3))
        c = sum(x * x for x in f) - radius * radius
        disc = bq * bq - c
        if disc < 0:
            return None
        t = -bq - disc ** 0.5
        if t < 0 or t > length:
            return None
        return {"entity": name, "entityId": entity_id, "position": guide.add(a, u, t)}

    return blocked


async def test_straight_line_when_clear():
    route, obstacle, checks = await guide.plan_route([0, 0, 0], [0, 0, 100], sphere_blocker([50, 0, 50], 5))
    assert route == [[0, 0, 100]] and obstacle is None and checks == 1


async def test_detour_round_an_obstacle():
    blocked = sphere_blocker([0, 0, 50], 10)
    route, obstacle, _ = await guide.plan_route([0, 0, 0], [0, 0, 100], blocked)
    assert obstacle["entity"] == "Rock"
    assert route is not None and len(route) == 2 and route[-1] == [0, 0, 100]
    # Every leg is clear, and the detour isn't absurdly long.
    here = [0.0, 0.0, 0.0]
    for p in route:
        assert await blocked(here, p) is None
        here = p
    assert guide.path_length([0, 0, 0], route) < 140


async def test_no_route_when_boxed_in():
    async def wall(a, b):
        return {"entity": "Wall", "position": guide.add(a, guide.unit(guide.sub(b, a)), 1)}

    route, obstacle, checks = await guide.plan_route([0, 0, 0], [0, 0, 100], wall, max_checks=20)
    assert route is None and obstacle["entity"] == "Wall" and checks <= 21


async def test_avoid_off_is_one_check():
    route, obstacle, checks = await guide.plan_route([0, 0, 0], [0, 0, 100], sphere_blocker([0, 0, 50], 10), depth=0)
    assert route is None and obstacle is not None and checks == 1


def test_positions_from_either_shape():
    assert guide.as_vec({"x": 1, "y": 2, "z": 3}) == [1.0, 2.0, 3.0]
    assert guide.as_vec([4, 5, 6]) == [4.0, 5.0, 6.0]
    assert guide.distance_text(1500) == "1.5 km" and guide.distance_text(85.4) == "85 m"


def test_obstacle_names():
    assert guide.obstacle_name({"entity": "Red Ship", "block": {"name": "MyObjectBuilder_CubeBlock/LargeBlockArmorCorner2Base"}}) == "Red Ship"
    assert guide.obstacle_name({"entity": "Red Ship", "block": {"name": "Cargo Main"}}) == "Red Ship (Cargo Main)"
    assert guide.obstacle_name({"entity": "Rock", "voxel": True}) == "an asteroid"


def handlers(calls: list, los, controlling: bool = False, path=None) -> dict[str, Any]:
    h = default_handlers(controlling=controlling)
    h["path.find"] = path or (lambda p: {"state": "PathNotFound", "found": False, "retry": False, "points": [], "length": 0})
    drawn: dict[str, Any] = {}

    def set_(p):
        calls.append(p)
        for e in p["elements"]:
            drawn[e["id"]] = e
        return {"set": [e["id"] for e in p["elements"]], "total": len(drawn)}

    h.update({
        "overlay.set": set_,
        "overlay.remove": lambda p: {"removed": sum(drawn.pop(i, None) is not None for i in p["ids"]), "total": len(drawn)},
        "gps.list": lambda p: {"gps": [{"name": "Ice Field", "coords": [1, 2, 503], "description": "", "visible": True}]},
        "grid.motion": lambda p: {"center": [1, 2, 203], "radius": 40},
        "los.check": los,
    })
    return h


async def call(tool: str, args: dict[str, Any], calls: list, los=lambda p: {"clear": True, "distance": 1},
               controlling: bool = False, path=None) -> str:
    async with FakeGame(handlers(calls, los, controlling, path)) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool(tool, args)
            return "\n".join(c.text for c in result.content)
        finally:
            await bridge.close()


async def test_guide_to_gps_draws_a_route():
    calls: list = []
    text = await call("guide_to", {"to": "Ice Field"}, calls)
    element = calls[0]["elements"][0]
    assert element["kind"] == "route" and element["id"] == "guide" and element["route"] == [[1, 2, 503]]
    assert element["name"] == "Ice Field" and element["arriveRadius"] == 3
    assert "Guiding you to Ice Field: 500 m in a straight line" in text


async def test_guide_to_a_ship_stops_at_its_hull():
    calls: list = []

    def los(p):
        # The station's hull is 10 m short of its centre, facing the player.
        return {"clear": False, "distance": 190, "hit": {"entity": "Home Base", "entityId": STATION_ID, "position": [1, 2, 193]}}

    text = await call("guide_to", {"to": "Home Base", "arrive_m": 4}, calls, los)
    route = calls[0]["elements"][0]["route"]
    assert route == [[1.0, 2.0, 189.0]]
    assert "Home Base" in text and "straight line" in text


async def test_guide_round_an_obstacle_reports_it():
    calls: list = []

    # An asteroid of radius 20 m halfway along the line (the fake game's handlers are synchronous).
    def los_sync(p):
        a, b = p["from"], p["to"]
        d = guide.sub(b, a)
        u = guide.unit(d)
        f = guide.sub(a, [1, 2, 253])
        bq = sum(f[i] * u[i] for i in range(3))
        disc = bq * bq - (sum(x * x for x in f) - 400)
        if disc >= 0:
            t = -bq - disc ** 0.5
            if 0 <= t <= guide.norm(d):
                return {"clear": False, "distance": t, "hit": {"entity": "Asteroid", "entityId": "5", "voxel": True, "position": guide.add(a, u, t)}}
        return {"clear": True, "distance": guide.norm(d)}

    text = await call("guide_to", {"to": "Ice Field"}, calls, los_sync)
    element = calls[0]["elements"][0]
    assert len(element["route"]) == 2 and element["route"][-1] == [1, 2, 503]
    assert "{step}" in element["text"]
    assert "via 1 waypoint(s) around an asteroid" in text


async def test_guide_while_piloting_looks_through_the_ship_and_checks_its_edges():
    calls: list = []
    rays: list = []

    def los(p):
        rays.append(p)
        return {"clear": True, "distance": 1}

    text = await call("guide_to", {"to": "Ice Field", "arrive_m": 200}, calls, los, controlling=True)
    assert all(r["ignoreId"] == SHIP_ID for r in rays)
    assert len(rays) == 5  # the centre line and four edges 40 m out
    assert {round(guide.norm(guide.sub(r["from"], [1, 2, 3]))) for r in rays[1:]} == {40}
    assert calls[0]["elements"][0]["arriveRadius"] == 200
    assert "Routed for Miner (40 m radius)" in text


def test_ship_detours_clear_its_radius():
    points = guide.detour_candidates([0, 0, 0], [0, 0, 1000], [0, 0, 500], clearance=40)
    assert min(guide.norm(guide.sub(p, [0, 0, p[2]])) for p in points) >= 52 - 1e-6
    assert all(p[2] <= 457 + 1e-6 for p in points)


async def test_note_rides_along_to_the_arrived_event():
    from se_mcp.runner import event_message

    calls: list = []
    text = await call("guide_to", {"to": "Ice Field", "note": "open the hangar door"}, calls)
    assert calls[0]["elements"][0]["note"] == "open the hangar door"
    assert "woken with your note" in text
    message = event_message({"type": "arrived", "text": "The player reached Ice Field.", "context": {},
                             "data": {"name": "Ice Field", "note": "open the hangar door"}})
    assert "Note from when you set the guide: open the hangar door" in message and "watch" not in message


def ship_in_the_way(p):
    """A ship 'Hauler' (id 4004) blocks any line that crosses z = 250 near x = 1; lines out past x = 40 are clear."""
    a, b = p["from"], p["to"]
    if (a[2] - 250) * (b[2] - 250) < 0 and max(abs(a[0]), abs(b[0])) < 40:
        return {"clear": False, "distance": 1, "hit": {"entity": "Hauler", "entityId": "4004", "position": [1, 2, 250]}}
    return {"clear": True, "distance": 1}


async def test_a_ship_in_the_way_is_routed_round_by_the_game_pathfinder():
    calls: list = []
    asked: list = []
    tries = iter([True, False])  # the first answer: still building its map of the ship

    def path(p):
        asked.append(p)
        if next(tries):
            return {"state": "OctreeGenerating", "found": False, "retry": True, "points": [], "length": 0}
        return {"state": "PathFound", "found": True, "retry": False, "length": 300,
                "points": [[1, 2, 4], [50, 2, 200], [50.5, 2, 201], [50, 2, 300]]}

    text = await call("guide_to", {"to": "Ice Field"}, calls, ship_in_the_way, path=path)
    assert asked[0]["gridId"] == "4004" and asked[0]["radius"] == 1 and len(asked) == 2
    # Thinned (the first point is at the start, two are 1 m apart), then the target.
    assert calls[0]["elements"][0]["route"] == [[50, 2, 200], [50, 2, 300], [1, 2, 503]]
    assert "around Hauler (the game's own pathfinder)" in text


async def test_falls_back_to_detours_when_the_game_has_no_path():
    calls: list = []
    text = await call("guide_to", {"to": "Ice Field"}, calls, ship_in_the_way)
    assert len(calls[0]["elements"][0]["route"]) >= 2
    assert "around Hauler" in text and "pathfinder" not in text


async def test_a_seat_on_a_station_is_not_piloting():
    calls: list = []
    rays: list = []

    def los(p):
        rays.append(p)
        return {"clear": True, "distance": 1}

    async with FakeGame(handlers(calls, los, controlling=True)) as game:
        game.handlers["grid.motion"] = lambda p: {"center": [1, 2, 203], "radius": 112, "isStatic": True}
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            result = await create_server(bridge).call_tool("guide_to", {"to": "Ice Field"})
            text = "\n".join(c.text for c in result.content)
        finally:
            await bridge.close()
    assert len(rays) == 1 and "ignoreId" not in rays[0]
    assert "Routed for" not in text


async def test_already_there():
    calls: list = []
    text = await call("guide_to", {"to": "Ice Field", "arrive_m": 600}, calls)
    assert text == "You're already within 600 m of Ice Field." and calls == []


async def test_stop_guide():
    calls: list = []
    assert await call("stop_guide", {}, calls) == "No guide was showing."
