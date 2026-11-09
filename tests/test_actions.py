import json

import pytest
from fakes import healthy_cluster

from pg_ops_agent.actions import ActionError, ProposalStore, check_preconditions, execute


def status(**over):
    s = {
        "currentPrimary": "orders-db-1",
        "targetPrimary": "orders-db-1",
        "pods": [
            {"name": "orders-db-1", "role": "primary", "ready": True},
            {"name": "orders-db-2", "role": "replica", "ready": True},
            {"name": "orders-db-3", "role": "replica", "ready": False},
        ],
    }
    s.update(over)
    return s


def test_switchover_needs_a_ready_replica():
    check_preconditions("switchover", "orders-db-2", status())
    for bad in ("orders-db-1", "orders-db-3", None, "other-db-1"):
        with pytest.raises(ActionError):
            check_preconditions("switchover", bad, status())


def test_no_switchover_during_a_failover():
    with pytest.raises(ActionError, match="in progress"):
        check_preconditions("switchover", "orders-db-2", status(targetPrimary="orders-db-2"))


def test_approve_rechecks_and_runs_as_the_operator(tmp_path):
    store = ProposalStore(tmp_path)
    p = store.create(
        action="switchover",
        reason="r",
        namespace="orders",
        cluster="orders-db",
        target_instance="orders-db-2",
    )
    ran = []
    execute(p, status(), run=lambda argv, stdin: ran.append(argv) or "ok")
    assert ran == [["cnpg", "promote", "orders-db", "orders-db-2", "-n", "orders"]]

    # The replica became unready between proposal and approval.
    stale = status(pods=[{"name": "orders-db-2", "role": "replica", "ready": False}])
    with pytest.raises(ActionError):
        execute(p, stale, run=lambda argv, stdin: pytest.fail("must not run"))


def test_backup_creates_a_plugin_backup(tmp_path):
    p = ProposalStore(tmp_path).create(
        action="backup", reason="r", namespace="orders", cluster="orders-db"
    )
    seen = {}
    execute(p, status(), run=lambda argv, stdin: seen.update(argv=argv, body=stdin) or "ok")
    assert seen["argv"] == ["apply", "-f", "-"]
    body = json.loads(seen["body"])
    assert body["kind"] == "Backup"
    assert body["metadata"] == {"name": f"orders-db-agent-{p.id}", "namespace": "orders"}
    assert body["spec"]["method"] == "plugin"


def test_proposal_ids_cannot_escape_the_store(tmp_path):
    with pytest.raises(ActionError):
        ProposalStore(tmp_path).get("../../etc/passwd")


def test_healthy_cluster_fixture_is_consistent():
    assert healthy_cluster()["status"]["currentPrimary"] == "orders-db-1"
