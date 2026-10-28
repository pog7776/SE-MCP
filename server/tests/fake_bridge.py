"""A fake SEMcp plugin: speaks the same TCP protocol with canned data, for tests without the game."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

TOKEN = "test-token"

Handler = Callable[[dict[str, Any]], Any]

SHIP_ID = "1001"
STATION_ID = "2002"


class FakeGameError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _block(id: str, name: str, type: str, display: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": id, "name": name, "type": type, "subtype": type, "displayType": display, "gridId": SHIP_ID,
        "enabled": True, "functional": True, "working": True, "integrity": 100, "accessible": True, **extra,
    }


BLOCKS = [
    _block("11", "Battery", "BatteryBlock", "Battery"),
    _block("12", "Hydrogen Thruster", "Thrust", "Hydrogen Thruster", enabled=False),
    _block("13", "Hydrogen Thruster 2", "Thrust", "Hydrogen Thruster"),
    _block("14", "Basic Refinery", "Refinery", "Basic Refinery", integrity=80.5),
    _block("15", "Cargo Main", "CargoContainer", "Large Cargo Container"),
]


def _grid(id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": id, "name": name, "size": "large", "isStatic": False, "blocks": 100, "relation": "owner",
        "position": {"x": 1, "y": 2, "z": 3}, "distance": 0, "speed": 0, "constructId": id,
        "dockedGroupId": id, "accessible": True, **extra,
    }


def default_handlers(session_loaded: bool = True, controlling: bool = True) -> dict[str, Handler]:
    def need_session() -> None:
        if not session_loaded:
            raise FakeGameError("no_session", "No world is loaded")

    def ping(_: dict[str, Any]) -> dict[str, Any]:
        return {
            "host": "0.1.0.0", "generation": 1, "game": "1.210.014", "runtime": ".NET Framework 4.8",
            "sessionLoaded": session_loaded,
            "world": "Test World" if session_loaded else None,
            "player": "Engineer" if session_loaded else None,
            "onlineMode": "OFFLINE" if session_loaded else None,
            "isServer": True if session_loaded else None,
        }

    def context(_: dict[str, Any]) -> dict[str, Any]:
        need_session()
        controlled = (
            {"kind": "Cockpit", "blockId": "10", "blockName": "Cockpit", "gridId": SHIP_ID, "gridName": "Miner"}
            if controlling else {"kind": "Character"}
        )
        return {
            "player": "Engineer", "identityId": "77", "position": {"x": 1, "y": 2, "z": 3},
            "controlled": controlled,
            "lookingAt": {"kind": "grid", "distance": 3.2, "gridId": SHIP_ID, "gridName": "Miner", "relation": "owner",
                          "block": {"id": "14", "name": "Basic Refinery", "type": "Refinery", "integrity": 80.5, "accessible": True}},
            "nearestGrid": {"gridId": STATION_ID, "gridName": "Home Base", "distance": 12.0},
        }

    def grids_list(_: dict[str, Any]) -> dict[str, Any]:
        need_session()
        return {"range": 50000, "grids": [
            _grid(SHIP_ID, "Miner"),
            _grid(STATION_ID, "Home Base", isStatic=True, distance=12),
            _grid("3003", "Miner Drone", size="small", distance=40),
        ]}

    def grid_get(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        if p["gridId"] not in (SHIP_ID, STATION_ID, "3003"):
            raise FakeGameError("not_found", f"No grid with id {p['gridId']}")
        name = {SHIP_ID: "Miner", STATION_ID: "Home Base", "3003": "Miner Drone"}[p["gridId"]]
        return {
            "summary": _grid(p["gridId"], name),
            "grids": [{"id": p["gridId"], "name": name, "size": "large", "blocks": 100}],
            "docked": [],
            "mass": 120000,
            "power": {"batteries": {"count": 1, "storedMWh": 2.5, "maxMWh": 3, "fill": 83.3, "inputMW": 0, "outputMW": 0.5},
                      "producers": [{"type": "Reactor", "count": 1, "working": 1, "outputMW": 1.2, "maxMW": 15}]},
            "gas": {"hydrogen": {"count": 2, "fill": 40, "storedL": 800000, "capacityL": 2000000},
                    "oxygen": {"count": 0, "fill": 0, "storedL": 0, "capacityL": 0}},
            "cargo": {"containers": 1, "fill": 50, "volumeL": 7000, "maxVolumeL": 15625},
            "damage": {"count": 1, "blocks": [{"id": "14", "name": "Basic Refinery", "integrity": 80.5, "missing": {"SteelPlate": 4}}]},
            "functionalCount": 5,
            "terminalBlocks": 5,
        }

    def blocks_list(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        items = BLOCKS
        if p.get("nameContains"):
            items = [b for b in items if p["nameContains"].lower() in b["name"].lower()]
        if p.get("type"):
            items = [b for b in items if p["type"].lower() in (b["type"] + b["displayType"]).lower()]
        limit = p.get("limit", 200)
        return {"total": len(items), "offset": 0, "blocks": items[:limit]}

    def block_get(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        block = next((b for b in BLOCKS if b["id"] == p["blockId"]), None)
        if block is None:
            raise FakeGameError("not_found", f"No terminal block with id {p['blockId']}")
        return {
            "block": block, "gridName": "Miner", "detailedInfo": "Type: Test\nMax Required Input: 1 kW",
            "customInfo": "", "customData": "", "showInTerminal": True, "missingComponents": {},
            "properties": [{"id": "OnOff", "type": "Boolean", "value": True}, {"id": "Font", "type": "Int64", "value": 0}],
            "actions": [{"id": "OnOff_On", "name": "Toggle block On"}],
            "inventories": [],
        }

    def groups_list(_: dict[str, Any]) -> dict[str, Any]:
        need_session()
        return {"groups": [{"name": "Thrusters", "blocks": [{"id": "12", "name": "Hydrogen Thruster"}, {"id": "13", "name": "Hydrogen Thruster 2"}]}]}

    def inventory_get(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        if p.get("character"):
            return {"volumeL": 53, "maxVolumeL": 1200, "fill": 4.4, "totals": [{"type": "PhysicalGunObject", "subtype": "WelderItem", "amount": 1}], "inventories": []}
        return {"volumeL": 7000, "maxVolumeL": 15625, "fill": 44.8, "inventories": [], "totals": [
            {"type": "Ore", "subtype": "Iron", "amount": 8170.57},
            {"type": "Ore", "subtype": "Ice", "amount": 33958.71},
            {"type": "Ingot", "subtype": "Iron", "amount": 120.5},
            {"type": "Component", "subtype": "SteelPlate", "amount": 42},
        ]}

    def character_status(_: dict[str, Any]) -> dict[str, Any]:
        need_session()
        return {
            "alive": True, "health": 100, "energy": 80.5, "oxygen": 60, "hydrogen": 20, "environmentOxygen": 0,
            "jetpack": True, "dampeners": True, "helmet": True, "lights": False, "movement": "Flying",
            "position": {"x": 1, "y": 2, "z": 3}, "speed": 4.5, "gravityG": 0, "inventoryFill": 4.4,
        }

    def block_action(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        if p.get("group"):
            return {"action": p["action"], "applied": ["Hydrogen Thruster", "Hydrogen Thruster 2"], "skipped": []}
        block = next(b for b in BLOCKS if b["id"] == p["blockId"])
        return {"action": p["action"], "applied": [block["name"]], "skipped": []}

    def set_property(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        block = next(b for b in BLOCKS if b["id"] == p["blockId"])
        return {"property": p["property"], "changed": [{"block": block["name"], "before": 0, "after": p["value"]}], "skipped": []}

    def set_name(p: dict[str, Any]) -> dict[str, Any]:
        block = next(b for b in BLOCKS if b["id"] == p["blockId"])
        return {"before": block["name"], "after": p["value"]}

    def notify(p: dict[str, Any]) -> dict[str, Any]:
        need_session()
        return {"shown": True, "kind": p.get("kind", "hud")}

    def production_get(_: dict[str, Any]) -> dict[str, Any]:
        need_session()
        return {"blocks": [
            {"id": "14", "name": "Basic Refinery", "type": "Refinery", "displayType": "Basic Refinery", "enabled": True,
             "working": True, "producing": True, "mode": None, "repeating": None, "cooperative": None,
             "queue": [{"blueprint": "IronOreToIngot", "amount": 5279.78}],
             "input": {"fill": 74.6, "items": 3}, "output": {"fill": 27.5, "items": 4}},
            {"id": "16", "name": "Assembler", "type": "Assembler", "displayType": "Assembler", "enabled": True,
             "working": True, "producing": False, "mode": "Assembly", "repeating": False, "cooperative": False,
             "queue": [{"blueprint": "SteelPlate", "amount": 100}], "input": {"fill": 0, "items": 0}, "output": {"fill": 0, "items": 0}},
        ]}

    return {
        "block.action": block_action, "block.set_property": set_property, "block.set_name": set_name,
        "notify": notify, "production.get": production_get,
        "ping": ping, "player.context": context, "grids.list": grids_list, "grid.get": grid_get,
        "blocks.list": blocks_list, "block.get": block_get, "groups.list": groups_list,
        "inventory.get": inventory_get, "character.status": character_status,
    }


class FakeGame:
    def __init__(self, handlers: dict[str, Handler] | None = None, token: str = TOKEN):
        self.handlers = handlers if handlers is not None else default_handlers()
        self.token = token
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def __aenter__(self) -> FakeGame:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_: Any) -> None:
        assert self._server is not None
        self._server.close()
        # Like the game exiting: drop open connections too (Server.close() leaves them up on 3.11).
        for writer in self._writers:
            writer.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)

        def send(message: dict[str, Any]) -> None:
            writer.write((json.dumps(message) + "\n").encode())

        def error(request_id: Any, kind: str, message: str) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message, "data": {"kind": kind}}}

        authed = False
        while line := await reader.readline():
            request = json.loads(line)
            request_id, method, params = request.get("id"), request.get("method"), request.get("params") or {}
            if not authed:
                if method == "auth" and params.get("token") == self.token:
                    authed = True
                    send({"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}})
                else:
                    send(error(request_id, "access_denied", "First message must be auth with a valid token"))
                    break
            else:
                self.calls.append((method, params))
                handler = self.handlers.get(method)
                if handler is None:
                    send(error(request_id, "unknown_method", f"Unknown method '{method}'"))
                else:
                    try:
                        send({"jsonrpc": "2.0", "id": request_id, "result": handler(params)})
                    except FakeGameError as e:
                        send(error(request_id, e.kind, str(e)))
            await writer.drain()
        writer.close()
