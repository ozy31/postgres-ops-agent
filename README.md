# postgres-ops-agent

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent
(Claude Code, Codex, Cursor, or your own loop) diagnose a
[CloudNativePG](https://cloudnative-pg.io) cluster, **without being able to
change it**.

Every tool is read-only, and the server's Kubernetes identity has no write
verbs at all. The agent finds the problem and says what to do; a human does
it.

## Tools

| Tool | What it returns |
|---|---|
| `cluster_status` | phase, primary, timeline, Ready / ContinuousArchiving conditions, each instance pod's role, readiness, restarts, node |
| `recent_backups` | last base backups with phase, age, error |
| `warning_events` | Kubernetes Warning events in the namespace |

The server is bound to one namespace and one cluster by its environment
(`PG_OPS_NAMESPACE`, `PG_OPS_CLUSTER`); no tool takes a namespace or cluster
argument, so the agent cannot point it somewhere else.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make check     # ruff + pytest
```
