"""MCP server: the tools an agent sees."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .config import Settings
from .kube import KubeReader

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)

INSTRUCTIONS = """\
You are diagnosing one PostgreSQL cluster run by the CloudNativePG operator.
All tools are read-only. Start with cluster_status, then follow the evidence.
Report what you found and what a human should do; you cannot change anything."""


def build_server(settings: Settings, kube: KubeReader) -> MCPServer:
    server = MCPServer(
        name="pg-ops-agent",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    @server.tool(annotations=READ_ONLY)
    def cluster_status() -> dict[str, Any]:
        """Operator view of the cluster: phase, primary, timeline, conditions
        (Ready, ContinuousArchiving) and each instance pod with its role,
        readiness, restarts and node."""
        return kube.cluster_status()

    @server.tool(annotations=READ_ONLY)
    def recent_backups(limit: int = 5) -> list[dict[str, Any]]:
        """The most recent base backups with phase, age and error, newest first."""
        return kube.backups(limit=max(1, min(limit, 20)))

    @server.tool(annotations=READ_ONLY)
    def warning_events(since_minutes: int = 30) -> list[dict[str, Any]]:
        """Kubernetes Warning events in the cluster's namespace (failed
        scheduling, probe failures, volume problems), newest first."""
        return kube.warning_events(since_minutes=max(1, min(since_minutes, 240)))

    return server


def main() -> None:
    settings = Settings.from_env()
    kube = KubeReader.from_kubeconfig(settings.namespace, settings.cluster)
    build_server(settings, kube).run("stdio")
