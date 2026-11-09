"""Read-only views of a CNPG cluster from the Kubernetes API.

Everything here is a GET or LIST. The service account the server runs as has
no other verbs (deploy/rbac.yaml), so even a bug here cannot change anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

GROUP, VERSION = "postgresql.cnpg.io", "v1"


def _age(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    seconds = int((datetime.now(UTC) - ts).total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


class KubeReader:
    def __init__(self, core, custom, namespace: str, cluster: str):
        self.core = core
        self.custom = custom
        self.ns = namespace
        self.cluster = cluster

    @classmethod
    def from_kubeconfig(cls, namespace: str, cluster: str) -> KubeReader:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return cls(client.CoreV1Api(), client.CustomObjectsApi(), namespace, cluster)

    def cluster_status(self) -> dict[str, Any]:
        obj = self.custom.get_namespaced_custom_object(
            GROUP, VERSION, self.ns, "clusters", self.cluster
        )
        spec, status = obj.get("spec", {}), obj.get("status", {})
        pods = self.core.list_namespaced_pod(
            self.ns, label_selector=f"cnpg.io/cluster={self.cluster},cnpg.io/podRole=instance"
        ).items
        return {
            "cluster": self.cluster,
            "phase": status.get("phase"),
            "phaseReason": status.get("phaseReason"),
            "instances": spec.get("instances"),
            "readyInstances": status.get("readyInstances"),
            "currentPrimary": status.get("currentPrimary"),
            "targetPrimary": status.get("targetPrimary"),
            "timeline": status.get("timelineID"),
            "image": spec.get("imageName"),
            "conditions": [
                {k: c.get(k) for k in ("type", "status", "reason", "message")}
                for c in status.get("conditions", [])
            ],
            "pods": [
                {
                    "name": p.metadata.name,
                    "role": (p.metadata.labels or {}).get("cnpg.io/instanceRole"),
                    "phase": p.status.phase,
                    "ready": all(c.ready for c in p.status.container_statuses or []),
                    "restarts": sum(c.restart_count for c in p.status.container_statuses or []),
                    "node": p.spec.node_name,
                }
                for p in sorted(pods, key=lambda p: p.metadata.name)
            ],
        }

    def backups(self, limit: int = 5) -> list[dict[str, Any]]:
        items = self.custom.list_namespaced_custom_object(
            GROUP, VERSION, self.ns, "backups", label_selector=f"cnpg.io/cluster={self.cluster}"
        ).get("items", [])
        items.sort(key=lambda b: b["metadata"]["creationTimestamp"], reverse=True)
        return [
            {
                "name": b["metadata"]["name"],
                "phase": b.get("status", {}).get("phase"),
                "method": b.get("spec", {}).get("method"),
                "age": _age(b["metadata"]["creationTimestamp"]),
                "error": b.get("status", {}).get("error"),
            }
            for b in items[:limit]
        ]

    def warning_events(self, since_minutes: int = 30, limit: int = 20) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(minutes=since_minutes)
        events = self.core.list_namespaced_event(self.ns, field_selector="type=Warning").items
        out = []
        for e in events:
            ts = e.last_timestamp or e.event_time or e.metadata.creation_timestamp
            if ts is None or ts < cutoff:
                continue
            out.append(
                {
                    "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "reason": e.reason,
                    "message": (e.message or "")[:300],
                    "count": e.count,
                    "age": _age(ts),
                    "_ts": ts,
                }
            )
        out.sort(key=lambda e: e["_ts"], reverse=True)
        for e in out:
            del e["_ts"]
        return out[:limit]
