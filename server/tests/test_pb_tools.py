from __future__ import annotations

from typing import Any

import pytest

from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers
from .test_tools import call_tool

PB_ID = "70000001"
OLD_CODE = 'public void Main(string argument) { Echo("old"); }'


class FakePb:
    """A PB that 'compiles' by checking for a Main method and 'runs' by echoing its argument."""

    def __init__(self, is_server: bool = True):
        self.code = OLD_CODE
        self.output = "old"
        self.is_server = is_server
        self.pending: str | None = None  # client mode: output that arrives on the next poll

    def state(self, include_code: bool = True) -> dict[str, Any]:
        if self.pending is not None and not self.output:
            self.output, self.pending = self.pending, None
        return {
            "id": PB_ID, "name": "PB Status", "enabled": True, "working": True, "scriptsEnabled": True,
            "isServer": self.is_server, "hasCompileErrors": ("Main" not in self.code) if self.is_server else None,
            "terminalArgument": "", "codeLength": len(self.code), "code": self.code if include_code else None,
            "output": self.output,
        }

    def _set_output(self, text: str) -> None:
        if self.is_server:
            self.output = text
        else:
            self.output, self.pending = "", text

    def handlers(self) -> dict[str, Any]:
        handlers = default_handlers()
        blocks_list = handlers["blocks.list"]

        def blocks(p: dict) -> dict:
            result = blocks_list(p)
            if "pb" in (p.get("nameContains") or "").lower():
                pb = {"id": PB_ID, "name": "PB Status", "type": "MyProgrammableBlock", "displayType": "Programmable block"}
                result = {"total": 1, "offset": 0, "blocks": [pb]}
            return result

        def get(p: dict) -> dict:
            return self.state(p.get("includeCode", True))

        def set_code(p: dict) -> dict:
            self.code = p["code"]
            self._set_output("" if "Main" in self.code else "Error: no Main method")
            return self.state(False)

        def run(p: dict) -> dict:
            self._set_output(f"ran with {p['argument']}")
            return self.state(False)

        handlers.update({"blocks.list": blocks, "pb.get": get, "pb.set_code": set_code, "pb.run": run,
                         "pb.recompile": lambda p: self.state(False)})
        return handlers


@pytest.fixture
def backups(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_MCP_PB_BACKUPS", str(tmp_path))
    return tmp_path


async def run(fake: FakePb, tool: str, **args: Any) -> tuple[str, FakeGame]:
    async with FakeGame(fake.handlers()) as game:
        bridge = Bridge(port=game.port, token=TOKEN)
        try:
            return await call_tool(create_server(bridge), tool, **args), game
        finally:
            await bridge.close()


async def test_read_shows_code_and_output():
    text, _ = await run(FakePb(), "read_pb_code", block=PB_ID)
    assert "compiled OK" in text
    assert OLD_CODE in text
    assert text.endswith("Output:\nold")


async def test_write_backs_up_old_code_and_reports_compile_errors(backups):
    fake = FakePb()
    text, game = await run(fake, "write_pb_script", block=PB_ID, code="broken", run_argument="go")
    assert "compile errors" in text
    assert "no Main method" in text
    assert not any(m == "pb.run" for m, _ in game.calls)  # didn't run a broken script
    [saved] = list(backups.iterdir())
    assert saved.read_text(encoding="utf-8") == OLD_CODE


async def test_write_then_run(backups):
    code = 'public void Main(string argument) { Echo("ran with " + argument); }'
    text, game = await run(FakePb(), "write_pb_script", block=PB_ID, code=code, run_argument="go")
    assert "compiled OK" in text
    assert ("pb.run", {"blockId": PB_ID, "argument": "go"}) in game.calls
    assert "ran with go" in text


async def test_run_waits_for_output_on_a_multiplayer_client():
    text, _ = await run(FakePb(is_server=False), "run_pb", block=PB_ID, argument="status")
    assert text.endswith("ran with status")


async def test_primer_resource():
    async with FakeGame() as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        contents = await server.read_resource("se://pb-primer")
    text = "".join(getattr(c, "content", "") or getattr(c, "text", "") for c in contents)
    assert "GridTerminalSystem" in text
