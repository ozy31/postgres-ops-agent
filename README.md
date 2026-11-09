# postgres-ops-agent

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent
(Claude Code, Codex, Cursor, or your own loop) diagnose a
[CloudNativePG](https://cloudnative-pg.io) cluster, **without being able to
change it**.

Every tool is read-only, and the server's Kubernetes identity has no write
verbs at all. The agent finds the problem and says what to do; a human does
it.

## Using it from Claude Code

1. Copy [`.mcp.json.example`](.mcp.json.example) to `.mcp.json` and set
   `PG_OPS_KUBECONFIG` and `PG_OPS_PASSWORD_FILE` in your shell
   (`eval "$(hack/agent-env.sh)"` does it for the e2e cluster). No
   credential goes in the file.
2. The [`postgres-triage`](.claude/skills/postgres-triage/SKILL.md) skill
   loads when you ask about the database. It tells the agent to start from
   `cluster_status`, follow the evidence instead of calling every tool, not
   to trust the operator's `phase` alone, and to answer as
   `Diagnosis / Evidence / Impact / Action for a human`.
3. Ask: *"orders is slow, check the database"*.

The same server works with any MCP client; the skill is plain Markdown that
other agents can take as a system prompt (the evals do exactly that).

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

## Least privilege, checked

| Layer | The agent can | The agent cannot |
|---|---|---|
| Kubernetes ([`deploy/rbac.yaml`](deploy/rbac.yaml)) | get/list CNPG clusters, backups, poolers, pods, events in one namespace | read Secrets, exec into pods, read pod logs, change anything, see other namespaces |
| Postgres ([`deploy/postgres-role.yaml`](deploy/postgres-role.yaml)) | read statistics views (`pg_monitor`) | read table data, write, create objects |
| MCP server | call fixed tools bound to one cluster | send SQL, choose a namespace or cluster, run an action |

[`e2e/rbac.sh`](e2e/rbac.sh) checks the Kubernetes column on a real cluster
with `kubectl auth can-i --as` for every allowed and denied verb, including
the subresources (`pods/exec`, `pods/log`, `clusters/status`).

The e2e environment ([`hack/e2e-up.sh`](hack/e2e-up.sh)) is a kind cluster
with CloudNativePG, the Barman Cloud plugin, local S3 and a three-instance
cluster. Every credential in it is generated at setup time; nothing secret
is in the repository. [`hack/agent-env.sh`](hack/agent-env.sh) writes a
kubeconfig with a short-lived token for the agent's service account, so
the server is tested as the agent, not as an admin.

## Tested over MCP, as the agent

[`e2e/test_tools.py`](e2e/test_tools.py) starts the real server over stdio,
the way Claude Code or any MCP client does, with the agent's own kubeconfig
and database role, and calls every tool against the kind cluster:

- the cluster shows three ready instances, two `quorum` standbys streaming,
  and `ContinuousArchiving` true;
- a query run with a card-number-like literal shows up in `top_queries`
  without the number;
- every call lands in the audit log;
- logged in as the agent's role directly, `select` from a table, `insert`
  and `create table` are all refused by Postgres.

The pooler is published on `127.0.0.1:15432` through a kind port mapping
rather than `kubectl port-forward`: PgBouncer resets connections when a
client disconnects, and port-forward exits on the first reset.

## Faults, and proof the evidence is there

[`e2e/scenarios.py`](e2e/scenarios.py) breaks the real cluster in four ways
and undoes it; [`e2e/test_faults.py`](e2e/test_faults.py) checks that each
fault is visible through the tools:

| Scenario | Injected | Visible in |
|---|---|---|
| long transaction | a session writes a row and sleeps inside its transaction | `long_transactions` (the row's value is redacted) |
| lock contention | `ACCESS EXCLUSIVE` lock held, three readers waiting | `blocking_locks`: one blocker, three waiters |
| replica down | one standby fenced (`cnpg.io/fencedInstances`) | `cluster_status` (2 of 3 ready), `replication` (one standby) |
| WAL archiving failure | object store endpoint pointed at a host that does not exist | `wal_archiving` (`failed_count`, last failed segment) |

These are the oracle for the LLM evals below: when they pass, a wrong
diagnosis is the model's, not the tooling's.

One thing the replica scenario surfaced: with a standby stopped, the
operator's `phase` still reads **"Cluster in healthy state"**, and
`readyInstances` only drops 30-40 s later. An agent (or a dashboard) that
trusts `phase` misses it. The triage skill says so.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make check     # ruff + unit tests
make e2e-up    # kind + CNPG + test cluster
make rbac      # the agent's service account can read and cannot write
make e2e       # RBAC + every tool over MCP against the cluster
```
