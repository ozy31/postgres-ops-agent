import asyncio
import json

from fakes import FakeCore, FakeCustom, healthy_cluster, pod

from pg_ops_agent.config import Settings
from pg_ops_agent.kube import KubeReader
from pg_ops_agent.server import build_server
from pg_ops_agent.sql import DSN, SQLReader


def server(tmp_path, core=None, custom=None):
    core = core or FakeCore(pods=[pod("orders-db-1", "primary"), pod("orders-db-2", "replica")])
    custom = custom or FakeCustom(cluster=healthy_cluster())
    kube = KubeReader(core, custom, "orders", "orders-db")
    sql = SQLReader(DSN("h", 5432, "d", "u", "p"), connect=lambda dsn: None)
    return build_server(Settings("orders", "orders-db", tmp_path), kube, sql)


def call(srv, name, args=None):
    result = asyncio.run(srv.call_tool(name, args or {}))
    assert not result.is_error, result
    # Lists come back as {"result": [...]} in structured content.
    body = result.structured_content
    return body.get("result", body) if set(body) == {"result"} else body


def test_every_tool_but_propose_is_read_only_and_takes_no_target(tmp_path):
    tools = asyncio.run(server(tmp_path).list_tools())
    for t in tools:
        props = set(t.input_schema.get("properties", {}))
        # The agent cannot redirect the server to another namespace/cluster.
        assert not props & {"namespace", "cluster", "name"}, (t.name, props)
        assert t.annotations.destructive_hint is False, t.name
        if t.name != "propose_action":
            assert t.annotations.read_only_hint is True, t.name


def test_cluster_status_over_mcp(tmp_path):
    body = call(server(tmp_path), "cluster_status")
    assert body["currentPrimary"] == "orders-db-1"


def test_every_call_is_audited(tmp_path):
    srv = server(tmp_path)
    call(srv, "cluster_status")
    call(srv, "recent_backups", {"limit": 3})
    lines = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [(e["tool"], e["args"], e["ok"]) for e in lines] == [
        ("cluster_status", {}, True),
        ("recent_backups", {"limit": 3}, True),
    ]
    assert all(e["result_bytes"] > 0 for e in lines)


def test_propose_switchover_only_writes_a_proposal(tmp_path):
    core, custom = (
        FakeCore(pods=[pod("orders-db-1", "primary"), pod("orders-db-2", "replica")]),
        FakeCustom(cluster=healthy_cluster()),
    )
    srv = server(tmp_path, core, custom)
    out = call(
        srv,
        "propose_action",
        {"action": "switchover", "target_instance": "orders-db-2", "reason": "node drain"},
    )
    assert out["accepted"] is True
    saved = json.loads((tmp_path / "proposals" / f"{out['proposal_id']}.json").read_text())
    assert saved["status"] == "pending"
    assert saved["target_instance"] == "orders-db-2"
    # Only reads reached the Kubernetes API.
    assert {c[0] for c in core.calls} == {"list_pods"}
    assert {c[0] for c in custom.calls} == {"get"}


def test_propose_rejects_unknown_actions_and_bad_targets(tmp_path):
    srv = server(tmp_path)
    out = call(srv, "propose_action", {"action": "drop_database", "reason": "why not"})
    assert out["accepted"] is False and "unknown action" in out["error"]
    out = call(
        srv,
        "propose_action",
        {"action": "switchover", "target_instance": "orders-db-1", "reason": "x"},
    )
    assert out["accepted"] is False and "ready replica" in out["error"]
    assert not list((tmp_path / "proposals").glob("*.json"))
