"""Client for the in-game SEMcp plugin: newline-delimited JSON-RPC 2.0 over TCP on 127.0.0.1."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 10.0
# Replies can carry images (base64 JPEG/PNG), far over asyncio's 64 KB default line limit.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


def config_path() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "SpaceEngineers" / "SEMcp" / "config.json"


def load_settings() -> tuple[int, str | None]:
    """Port and token from the plugin's config.json; SE_MCP_PORT / SE_MCP_TOKEN override."""
    port, token = 8765, None
    try:
        config = json.loads(config_path().read_text(encoding="utf-8-sig"))
        port = int(config.get("port", port))
        token = config.get("token")
    except (OSError, ValueError):
        pass
    port = int(os.environ.get("SE_MCP_PORT", port))
    token = os.environ.get("SE_MCP_TOKEN", token)
    return port, token


class BridgeError(Exception):
    """An error the game reported, or a connection problem. `kind` matches protocol.md."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


NOT_RUNNING = "Space Engineers isn't running, or the SEMcp plugin isn't loaded (check it's enabled in Pulsar)."


class Bridge:
    """One persistent connection. Connects (and re-auths) lazily, so the game can restart freely."""

    def __init__(self, host: str = "127.0.0.1", port: int | None = None, token: str | None = None):
        self.host = host
        self._port = port
        self._token = token
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        await self._ensure_connected()
        return await self._request(method, params or {}, timeout)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._read_task is not None:
            self._read_task.cancel()
        self._fail_pending(BridgeError("disconnected", "Connection closed"))
        self._reader = self._writer = self._read_task = None

    async def _ensure_connected(self) -> None:
        async with self._connect_lock:
            if self.connected:
                return
            await self.close()

            port, token = self._port, self._token
            if port is None or token is None:
                file_port, file_token = load_settings()
                port, token = port or file_port, token or file_token
            if not token:
                raise BridgeError("not_running", f"No token found. {NOT_RUNNING} Expected config at {config_path()}")

            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, port, limit=MAX_MESSAGE_BYTES), timeout=3
                )
            except (OSError, asyncio.TimeoutError) as e:
                raise BridgeError("not_running", f"{NOT_RUNNING} ({e.__class__.__name__} on port {port})") from None

            self._read_task = asyncio.create_task(self._read_loop(self._reader))
            try:
                await self._request("auth", {"token": token}, timeout=5)
            except BridgeError:
                await self.close()
                raise

    async def _request(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        assert self._writer is not None
        request_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            self._writer.write((json.dumps(message) + "\n").encode())
            await self._writer.drain()
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise BridgeError("timeout", f"'{method}' timed out after {timeout:g}s (game paused or busy?)") from None
        except (ConnectionError, OSError) as e:
            await self.close()
            raise BridgeError("disconnected", f"Lost connection to the game: {e}") from None
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                future = self._pending.get(message.get("id"))
                if future is None or future.done():
                    continue  # notifications (later) or late responses
                if "error" in message:
                    error = message["error"] or {}
                    kind = (error.get("data") or {}).get("kind", "game_error")
                    future.set_exception(BridgeError(kind, error.get("message", "Unknown error")))
                else:
                    future.set_result(message.get("result"))
        except (ConnectionError, OSError):
            pass
        finally:
            self._fail_pending(BridgeError("disconnected", "Lost connection to the game (world closed or plugin reloaded?)"))
            if self._writer is not None:
                self._writer.close()

    def _fail_pending(self, error: BridgeError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
