import asyncio
import json

from fakes import FakeCore, FakeCustom, healthy_cluster, pod

from pg_ops_agent.config import Settings
from pg_ops_agent.kube import KubeReader
from pg_ops_agent.server import build_server


def server():
    kube = KubeReader(
        FakeCore(pods=[pod("orders-db-1", "primary")]),
        FakeCustom(cluster=healthy_cluster()),
        "orders",
        "orders-db",
    )
    return build_server(Settings("orders", "orders-db"), kube)


def test_every_tool_is_read_only_and_takes_no_target():
    tools = asyncio.run(server().list_tools())
    assert tools
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.annotations.destructive_hint is False, t.name
        # The agent cannot redirect the server to another namespace/cluster.
        props = set(t.input_schema.get("properties", {}))
        assert not props & {"namespace", "cluster", "name"}, (t.name, props)


def test_cluster_status_over_mcp():
    result = asyncio.run(server().call_tool("cluster_status", {}))
    body = json.loads(result.content[0].text)
    assert body["currentPrimary"] == "orders-db-1"
    assert body["pods"][0]["role"] == "primary"
