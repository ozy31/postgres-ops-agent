"""In-memory stand-ins for the Kubernetes API clients."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS


def now(minus_minutes: int = 0) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minus_minutes)


def pod(name, role, ready=True, restarts=0, node="worker", phase="Running"):
    return NS(
        metadata=NS(name=name, labels={"cnpg.io/instanceRole": role}),
        status=NS(
            phase=phase,
            container_statuses=[NS(ready=ready, restart_count=restarts)],
        ),
        spec=NS(node_name=node),
    )


def event(name, reason, message, minutes_ago, kind="Pod"):
    ts = now(minutes_ago)
    return NS(
        involved_object=NS(kind=kind, name=name),
        reason=reason,
        message=message,
        count=1,
        last_timestamp=ts,
        event_time=None,
        metadata=NS(creation_timestamp=ts),
    )


class FakeCore:
    def __init__(self, pods=(), events=(), logs=None):
        self.pods, self.events, self.logs = list(pods), list(events), logs or {}
        self.calls = []

    def list_namespaced_pod(self, ns, label_selector=None):
        self.calls.append(("list_pods", ns, label_selector))
        return NS(items=self.pods)

    def list_namespaced_event(self, ns, field_selector=None):
        self.calls.append(("list_events", ns, field_selector))
        return NS(items=self.events)

    def read_namespaced_pod_log(self, name, ns, **kwargs):
        self.calls.append(("read_log", ns, name, kwargs))
        return self.logs[name]


class FakeCustom:
    def __init__(self, cluster=None, backups=()):
        self.cluster, self.backups = cluster or {}, list(backups)
        self.calls = []

    def get_namespaced_custom_object(self, group, version, ns, plural, name):
        self.calls.append(("get", plural, ns, name))
        return self.cluster

    def list_namespaced_custom_object(self, group, version, ns, plural, label_selector=None):
        self.calls.append(("list", plural, ns, label_selector))
        return {"items": self.backups}


def healthy_cluster():
    return {
        "spec": {"instances": 3, "imageName": "ghcr.io/cloudnative-pg/postgresql:18.6"},
        "status": {
            "phase": "Cluster in healthy state",
            "readyInstances": 3,
            "currentPrimary": "orders-db-1",
            "targetPrimary": "orders-db-1",
            "timelineID": 1,
            "conditions": [
                {"type": "Ready", "status": "True", "reason": "ClusterIsReady", "message": ""},
                {"type": "ContinuousArchiving", "status": "True", "reason": "", "message": ""},
            ],
        },
    }
