from fakes import FakeCore, FakeCustom, event, healthy_cluster, now, pod

from pg_ops_agent.kube import KubeReader


def reader(**kw):
    core = FakeCore(**{k: v for k, v in kw.items() if k in ("pods", "events", "logs")})
    custom = FakeCustom(**{k: v for k, v in kw.items() if k in ("cluster", "backups")})
    return KubeReader(core, custom, "orders", "orders-db"), core, custom


def test_cluster_status_summarises_operator_and_pods():
    r, core, _ = reader(
        cluster=healthy_cluster(),
        pods=[
            pod("orders-db-2", "replica", ready=False, restarts=4),
            pod("orders-db-1", "primary"),
        ],
    )
    s = r.cluster_status()
    assert s["currentPrimary"] == "orders-db-1"
    assert s["readyInstances"] == 3
    assert [p["name"] for p in s["pods"]] == ["orders-db-1", "orders-db-2"]
    assert s["pods"][1] == {
        "name": "orders-db-2",
        "role": "replica",
        "phase": "Running",
        "ready": False,
        "restarts": 4,
        "node": "worker",
    }
    assert {"type": "ContinuousArchiving", "status": "True", "reason": "", "message": ""} in s[
        "conditions"
    ]
    # Only instance pods of this cluster, never the whole namespace.
    assert core.calls == [
        ("list_pods", "orders", "cnpg.io/cluster=orders-db,cnpg.io/podRole=instance")
    ]


def test_backups_newest_first_and_limited():
    backups = [
        {
            "metadata": {"name": f"b{i}", "creationTimestamp": now(60 * (5 - i)).isoformat()},
            "spec": {"method": "plugin"},
            "status": {"phase": "completed" if i != 4 else "failed", "error": None},
        }
        for i in range(5)
    ]
    r, _, custom = reader(backups=backups)
    out = r.backups(limit=2)
    assert [b["name"] for b in out] == ["b4", "b3"]
    assert out[0]["phase"] == "failed"
    assert custom.calls == [("list", "backups", "orders", "cnpg.io/cluster=orders-db")]


def test_warning_events_drops_old_ones():
    r, core, _ = reader(
        events=[
            event("orders-db-3", "FailedScheduling", "0/4 nodes are available", 5),
            event("orders-db-2", "Unhealthy", "Readiness probe failed", 2),
            event("orders-db-1", "BackOff", "old news", 300),
        ]
    )
    out = r.warning_events(since_minutes=30)
    assert [e["reason"] for e in out] == ["Unhealthy", "FailedScheduling"]
    assert core.calls == [("list_events", "orders", "type=Warning")]
