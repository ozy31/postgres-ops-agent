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

## The agent proposes, a human decides

There is one tool that is not read-only: `propose_action`. It writes a
proposal file and returns its ID; nothing else happens.

```text
agent:  propose_action(action="switchover", target_instance="orders-db-2",
                       reason="orders-db-1 restarted 4 times in 10m, OOMKilled")
        -> {"accepted": true, "proposal_id": "9f3c21ab", "approve_with": "pg-ops-agent approve 9f3c21ab"}

human:  pg-ops-agent proposals
        9f3c21ab  pending    switchover -> orders-db-2  orders-db  2026-11-09T14:02:11+00:00
                  reason: orders-db-1 restarted 4 times in 10m, OOMKilled
        pg-ops-agent approve 9f3c21ab      # runs kubectl cnpg promote as *you*
```

- **Two identities.** The server runs as a service account with no write
  verbs. `approve` runs with the human's own kubeconfig, so every change in
  the Kubernetes audit log has a person's name on it.
- **Allowlisted actions** (`switchover`, `backup`), each with preconditions:
  a switchover needs a ready replica as target and no failover in progress.
- **Checked twice.** Preconditions are checked when the agent proposes and
  again when the human approves, because the cluster may have moved on.
- **Audit log.** Every tool call is appended to `.state/audit.jsonl` with
  its arguments, outcome, duration and result size.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make check     # ruff + pytest
```
