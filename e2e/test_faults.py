"""Each injected fault is visible through the tools an agent would use.

This is the oracle for the LLM evals: if these pass, the evidence is there,
and a wrong diagnosis is the model's fault, not the tools'."""

import pytest
from harness import mcp_calls
from scenarios import BY_NAME


@pytest.fixture
def scenario(request):
    s = BY_NAME[request.param]
    s.inject()
    yield s
    s.cleanup()


def tools(env, *calls):
    return mcp_calls(env, *calls)[1]


@pytest.mark.parametrize("scenario", ["long-transaction"], indirect=True)
def test_long_transaction_is_visible(env, scenario):
    (rows,) = tools(env, ("long_transactions", {"min_seconds": 5}))
    tx = [r for r in rows if "pg_sleep" in (r["query"] or "")]
    assert tx, rows
    assert tx[0]["xact_seconds"] >= 5
    assert "99.5" not in tx[0]["query"]


@pytest.mark.parametrize("scenario", ["lock-contention"], indirect=True)
def test_lock_contention_is_visible(env, scenario):
    (rows,) = tools(env, ("blocking_locks", {}))
    assert len(rows) >= 3, rows
    assert len({r["blocker_pid"] for r in rows}) == 1
    assert all("stock" in r["blocked_query"] for r in rows)


@pytest.mark.parametrize("scenario", ["replica-down"], indirect=True)
def test_replica_down_is_visible(env, scenario):
    status, repl = tools(env, ("cluster_status", {}), ("replication", {}))
    victim = scenario.state["victim"]
    assert status["readyInstances"] == 2, status
    assert [p for p in status["pods"] if p["name"] == victim][0]["ready"] is False
    assert len(repl) == 1 and repl[0]["standby"] != victim


@pytest.mark.parametrize("scenario", ["wal-archiving-failure"], indirect=True)
def test_archiving_failure_is_visible(env, scenario):
    (arch,) = tools(env, ("wal_archiving", {}))
    assert arch["failed_count"] > 0
    assert arch["seconds_since_failure"] is not None
    assert arch["last_failed_wal"]
