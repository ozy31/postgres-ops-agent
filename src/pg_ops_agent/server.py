"""MCP server: the tools an agent sees."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .actions import ACTIONS, ActionError, ProposalStore, check_preconditions
from .audit import Audit
from .config import Settings
from .kube import KubeReader
from .sql import DSN, SQLReader

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
# Writes a proposal file on the server's side; never touches the cluster.
PROPOSE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)

INSTRUCTIONS = """\
You are diagnosing one PostgreSQL cluster run by the CloudNativePG operator.
All tools are read-only. Start with cluster_status, then follow the evidence.
Report what you found and what a human should do. You cannot change anything;
if an action would help, call propose_action and a human will review it."""


def build_server(settings: Settings, kube: KubeReader, sql: SQLReader) -> MCPServer:
    server = MCPServer(
        name="pg-ops-agent",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )
    audit = Audit(settings.state_dir / "audit.jsonl")
    proposals = ProposalStore(settings.state_dir / "proposals")

    def tool(annotations: ToolAnnotations = READ_ONLY):
        def register(fn):
            return server.tool(annotations=annotations)(audit.wrap(fn))

        return register

    @tool()
    def cluster_status() -> dict[str, Any]:
        """Operator view of the cluster: phase, primary, timeline, conditions
        (Ready, ContinuousArchiving) and each instance pod with its role,
        readiness, restarts and node."""
        return kube.cluster_status()

    @tool()
    def recent_backups(limit: int = 5) -> list[dict[str, Any]]:
        """The most recent base backups with phase, age and error, newest first."""
        return kube.backups(limit=max(1, min(limit, 20)))

    @tool()
    def warning_events(since_minutes: int = 30) -> list[dict[str, Any]]:
        """Kubernetes Warning events in the cluster's namespace (failed
        scheduling, probe failures, volume problems), newest first."""
        return kube.warning_events(since_minutes=max(1, min(since_minutes, 240)))

    @tool()
    def replication() -> list[dict[str, Any]]:
        """Standbys streaming from the primary (pg_stat_replication): state,
        sync_state (quorum/async), bytes and seconds behind. An expected
        standby missing from this list is not streaming."""
        return sql.run("replication")

    @tool()
    def wal_archiving() -> dict[str, Any]:
        """WAL archiver on the primary (pg_stat_archiver): last archived and
        last failed segment and how long ago. If seconds_since_archived is
        well above archive_timeout, point-in-time recovery is falling behind."""
        rows = sql.run("archiver")
        return rows[0] if rows else {}

    @tool()
    def connections() -> dict[str, Any]:
        """Backends by user and state, against max_connections."""
        rows = sql.run("connections")
        limit = sql.run("max_connections")[0]["max_connections"]
        return {"max_connections": limit, "total": sum(r["n"] for r in rows), "by_user": rows}

    @tool()
    def long_transactions(min_seconds: int = 60) -> list[dict[str, Any]]:
        """Client transactions open longer than min_seconds, oldest first,
        with redacted query text. Long transactions hold back vacuum and WAL
        removal and often hold locks."""
        return sql.run("long_transactions", {"min_seconds": max(1, min_seconds)})

    @tool()
    def blocking_locks() -> list[dict[str, Any]]:
        """Sessions waiting on a lock, with the session blocking them
        (pg_blocking_pids). Query text is redacted."""
        return sql.run("blocking")

    @tool()
    def top_queries(limit: int = 10) -> list[dict[str, Any]]:
        """Statements by total execution time (pg_stat_statements),
        normalised and redacted."""
        return sql.run("top_queries", {"limit": max(1, min(limit, 25))})

    @tool(PROPOSE)
    def propose_action(
        action: str, reason: str, target_instance: str | None = None
    ) -> dict[str, Any]:
        """Ask a human to run an action. Nothing happens until they approve it
        with `pg-ops-agent approve <id>`. Allowed actions:
        switchover (needs target_instance, a ready replica) and backup.
        Give the evidence for it in reason."""
        try:
            check_preconditions(action, target_instance, kube.cluster_status())
        except ActionError as e:
            return {"accepted": False, "error": str(e), "allowed": ACTIONS}
        p = proposals.create(
            action=action,
            reason=reason[:1000],
            namespace=settings.namespace,
            cluster=settings.cluster,
            target_instance=target_instance,
        )
        return {
            "accepted": True,
            "proposal_id": p.id,
            "status": "pending human approval",
            "approve_with": f"pg-ops-agent approve {p.id}",
        }

    return server


def main() -> None:
    settings = Settings.from_env()
    kube = KubeReader.from_kubeconfig(settings.namespace, settings.cluster)
    sql = SQLReader(DSN.from_env())
    build_server(settings, kube, sql).run("stdio")
