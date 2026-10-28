"""A fake SEMcp plugin: speaks the same TCP protocol with canned data, for tests without the game."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

TOKEN = "test-token"

Handler = Callable[[dict[str, Any]], Any]


class FakeGameError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def default_handlers(session_loaded: bool = True) -> dict[str, Handler]:
    def ping(_: dict[str, Any]) -> dict[str, Any]:
        return {
            "host": "0.1.0.0",
            "generation": 1,
            "game": "1.210.014",
            "runtime": ".NET Framework 4.8",
            "sessionLoaded": session_loaded,
            "world": "Test World" if session_loaded else None,
            "player": "Engineer" if session_loaded else None,
        }

    return {"ping": ping}


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
