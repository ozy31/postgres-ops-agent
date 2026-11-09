"""Every tool against a healthy cluster, through MCP, as the agent's identity."""

import json
from pathlib import Path

import psycopg
import pytest
from harness import PORT, ROOT, mcp_calls, psql_admin


def call(env, name, args=None):
    _, (result,) = mcp_calls(env, (name, args or {}))
    return result


def test_server_exposes_the_expected_tools(env):
    tools, _ = mcp_calls(env)
    assert sorted(tools) == sorted(
        [
            "cluster_status",
            "recent_backups",
            "warning_events",
            "replication",
            "wal_archiving",
            "connections",
            "long_transactions",
            "blocking_locks",
            "top_queries",
            "propose_action",
        ]
    )


def test_cluster_status(env):
    s = call(env, "cluster_status")
    assert s["readyInstances"] == 3
    assert s["currentPrimary"] == s["targetPrimary"]
    roles = sorted(p["role"] for p in s["pods"])
    assert roles == ["primary", "replica", "replica"]
    conds = {c["type"]: c["status"] for c in s["conditions"]}
    assert conds["Ready"] == "True"
    assert conds["ContinuousArchiving"] == "True"


def test_replication_two_quorum_standbys(env):
    rows = call(env, "replication")
    assert [r["state"] for r in rows] == ["streaming", "streaming"]
    assert {r["sync_state"] for r in rows} == {"quorum"}


def test_wal_archiving(env):
    psql_admin("select pg_switch_wal()")
    a = call(env, "wal_archiving")
    assert a["archived_count"] > 0
    assert a["archive_timeout"] == "1min"


def test_connections(env):
    c = call(env, "connections")
    assert c["max_connections"] == 100
    assert c["total"] > 0


def test_top_queries_are_redacted(env):
    psql_admin("select count(*) from pg_class where relname = 'customer-4111111111111111'")
    rows = call(env, "top_queries", {"limit": 25})
    text = json.dumps(rows)
    assert "4111111111111111" not in text
    assert rows, "pg_stat_statements returned nothing"


def test_audit_log_records_the_calls(env):
    call(env, "cluster_status")
    last = (ROOT / ".state/audit.jsonl").read_text().splitlines()[-1]
    assert json.loads(last)["tool"] == "cluster_status"


def test_database_role_cannot_read_data_or_write(env):
    psql_admin("create table if not exists customers (id int, email text)")
    pw = Path(env["PG_OPS_PASSWORD_FILE"]).read_text().strip()
    dsn = dict(host="127.0.0.1", port=PORT, dbname="orders", user="ops_agent", password=pw)
    with psycopg.connect(**dsn, prepare_threshold=None, autocommit=True) as conn:
        for sql in (
            "select * from customers",
            "insert into customers values (1, 'x')",
            "create table agent_was_here (id int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
