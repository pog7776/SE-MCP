from __future__ import annotations

from se_mcp import naming
from se_mcp.bridge import Bridge
from se_mcp.tools import create_server

from .fake_bridge import TOKEN, FakeGame, default_handlers
from .test_tools import call_tool


def b(id: str, name: str, kind: str = "Ion Thruster") -> dict:
    return {"id": id, "name": name, "displayType": kind}


def names(changes) -> list[str]:
    return [new for _, new in changes]


def test_prefix_is_added_once():
    blocks = [b("1", "Ion Thruster"), b("2", "[Constructor] Ion Thruster 2")]
    assert names(naming.plan(blocks, prefix="[Constructor]")) == ["[Constructor] Ion Thruster"]
    assert names(naming.plan(blocks, prefix="RS-")) == ["RS-Ion Thruster", "RS-[Constructor] Ion Thruster 2"]


def test_strip_find_template_suffix_in_order():
    blocks = [b("1", "[Old] Ion Thruster 7"), b("2", "[Old] Ion Thruster 3"), b("3", "Battery", "Battery")]
    changes = naming.plan(blocks, strip_prefix="[old]", template="{type} {n}", suffix="(Red)")
    assert [(blk["id"], new) for blk, new in changes] == [
        ("2", "Ion Thruster 1 (Red)"), ("1", "Ion Thruster 2 (Red)"), ("3", "Battery 1 (Red)")]
    assert names(naming.plan(blocks, find="ion thruster", replace="Ion")) == ["[Old] Ion 7", "[Old] Ion 3"]
    assert names(naming.plan(blocks, template="{name} #{i} {unknown}")) == [
        "[Old] Ion Thruster 3 #1 {unknown}", "[Old] Ion Thruster 7 #2 {unknown}", "Battery #3 {unknown}"]


def test_template_numbers_in_natural_order():
    blocks = [b("a", "Thruster"), b("b", "Thruster 10"), b("c", "Thruster 2")]
    assert [(blk["id"], new) for blk, new in naming.plan(blocks, template="T{n}")] == [
        ("a", "T1"), ("c", "T2"), ("b", "T3")]


async def test_rename_blocks_previews_then_renames_by_id_in_one_call():
    handlers = default_handlers()
    handlers["block.rename_many"] = lambda p: {
        "renamed": [{"id": r["blockId"], "before": "?", "after": r["name"]} for r in p["renames"]], "failed": []}
    async with FakeGame(handlers) as game:
        server = create_server(Bridge(port=game.port, token=TOKEN))
        preview = await call_tool(server, "rename_blocks", type="Thrust", prefix="[Constructor]", dry_run=True)
        assert not any(m == "block.rename_many" for m, _ in game.calls)
        done = await call_tool(server, "rename_blocks", type="Thrust", prefix="[Constructor]")
        refused = await call_tool(server, "rename_blocks", prefix="[X]")

    calls = [p for m, p in game.calls if m == "block.rename_many"]
    assert len(calls) == 1
    assert {r["name"] for r in calls[0]["renames"]} >= {"[Constructor] Hydrogen Thruster"}
    assert all("blockId" in r for r in calls[0]["renames"])
    assert preview.startswith("Would rename ") and "Hydrogen Thruster -> [Constructor] Hydrogen Thruster" in preview
    assert done.startswith("Renamed ") and "-> [Constructor] Hydrogen Thruster" in done
    assert refused.startswith("Choose blocks")
