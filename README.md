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
| `replication` | standbys streaming from the primary, sync state, bytes and seconds behind |
| `wal_archiving` | last archived / failed WAL segment and how long ago |
| `connections` | backends by user and state, against `max_connections` |
| `long_transactions` | transactions open longer than N seconds, with redacted SQL |
| `blocking_locks` | who is waiting on whom (`pg_blocking_pids`) |
| `top_queries` | `pg_stat_statements` by total time, normalised and redacted |

### How the SQL tools stay read-only

The agent never sends SQL. Each tool runs one fixed query from
[`sql.py`](src/pg_ops_agent/sql.py), and three independent layers keep it
harmless:

1. **The database role** has only `pg_monitor`. Postgres itself refuses
   writes and reads of table data.
2. **Every query runs in a `READ ONLY` transaction** with
   `statement_timeout = 3s` and `lock_timeout = 1s`, rolled back at the end,
   so a slow catalog query cannot pile up during an incident.
3. **Query text is redacted** before it leaves the server: string and
   numeric literals, dollar-quoted bodies, emails and anything shaped like
   `password=...` are replaced. The agent sees the shape of
   `update users set email='?' where id=?`, not the customer's address.

The server is bound to one namespace and one cluster by its environment
(`PG_OPS_NAMESPACE`, `PG_OPS_CLUSTER`); no tool takes a namespace or cluster
argument, so the agent cannot point it somewhere else.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make check     # ruff + pytest
```
