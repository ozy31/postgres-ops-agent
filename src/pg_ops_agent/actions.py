"""Actions the agent may propose and only a human may run.

The MCP server's identity cannot change the cluster (deploy/rbac.yaml). The
agent's one way to suggest a change is propose_action, which writes a
proposal file and nothing else. A human reviews it with
`pg-ops-agent proposals` and runs it with `pg-ops-agent approve <id>`, using
their own kubeconfig. Preconditions are checked again at approval time,
because the cluster may have changed since the agent looked.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIONS = {
    "switchover": "Planned switchover to target_instance (kubectl cnpg promote).",
    "backup": "On-demand base backup through the Barman Cloud plugin.",
}


class ActionError(ValueError):
    pass


@dataclass
class Proposal:
    id: str
    action: str
    reason: str
    namespace: str
    cluster: str
    target_instance: str | None = None
    status: str = "pending"
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    decided: str | None = None
    result: str | None = None


def check_preconditions(action: str, target: str | None, status: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ActionError(f"unknown action {action!r}; allowed: {sorted(ACTIONS)}")
    if action == "switchover":
        replicas = [p["name"] for p in status["pods"] if p["role"] == "replica" and p["ready"]]
        if target not in replicas:
            raise ActionError(f"target_instance must be a ready replica, one of {replicas}")
        if status.get("currentPrimary") != status.get("targetPrimary"):
            raise ActionError("a switchover or failover is already in progress")
    elif target is not None:
        raise ActionError(f"{action} takes no target_instance")


class ProposalStore:
    def __init__(self, directory: Path):
        self.dir = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, pid: str) -> Path:
        if not pid.isalnum():
            raise ActionError(f"bad proposal id {pid!r}")
        return self.dir / f"{pid}.json"

    def create(self, **kw: Any) -> Proposal:
        p = Proposal(id=secrets.token_hex(4), **kw)
        self.save(p)
        return p

    def save(self, p: Proposal) -> None:
        self._path(p.id).write_text(json.dumps(asdict(p), indent=2) + "\n")

    def get(self, pid: str) -> Proposal:
        path = self._path(pid)
        if not path.exists():
            raise ActionError(f"no proposal {pid}")
        return Proposal(**json.loads(path.read_text()))

    def list(self) -> list[Proposal]:
        return sorted(
            (Proposal(**json.loads(f.read_text())) for f in self.dir.glob("*.json")),
            key=lambda p: p.created,
        )


def backup_manifest(p: Proposal) -> str:
    name = f"{p.cluster}-agent-{p.id}"
    return json.dumps(
        {
            "apiVersion": "postgresql.cnpg.io/v1",
            "kind": "Backup",
            "metadata": {"name": name, "namespace": p.namespace},
            "spec": {
                "cluster": {"name": p.cluster},
                "method": "plugin",
                "pluginConfiguration": {"name": "barman-cloud.cloudnative-pg.io"},
            },
        }
    )


Runner = Callable[[list[str], str | None], str]


def kubectl(args: list[str], stdin: str | None = None) -> str:
    """kubectl from the operator's PATH, with the operator's kubeconfig."""
    binary = shutil.which("kubectl")
    if binary is None:
        raise ActionError("kubectl not found on PATH")
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [binary, *args], input=stdin, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def execute(p: Proposal, status: dict[str, Any], run: Runner = kubectl) -> str:
    """Run an approved proposal as the human operator."""
    check_preconditions(p.action, p.target_instance, status)
    if p.action == "switchover":
        return run(["cnpg", "promote", p.cluster, p.target_instance, "-n", p.namespace], None)
    if p.action == "backup":
        return run(["apply", "-f", "-"], backup_manifest(p))
    raise ActionError(p.action)
