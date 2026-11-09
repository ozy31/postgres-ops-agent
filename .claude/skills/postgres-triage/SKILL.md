---
name: postgres-triage
description: Diagnose a CloudNativePG PostgreSQL cluster with the read-only pg-ops MCP tools. Use when someone reports database errors, slowness, failover, replication lag, backup or WAL archiving problems, or asks "is the database healthy?".
---

# Postgres triage

You have the `pg-ops` MCP server: read-only tools bound to one CloudNativePG
cluster. You cannot change the cluster. Your job is to find the cause, show
the evidence, and tell a human what to do.

## Procedure

1. **Always start with `cluster_status`.** Note the phase, ready vs desired
   instances, current vs target primary, the `Ready` and
   `ContinuousArchiving` conditions, and any pod that is not ready or has
   restarts. **Do not trust `phase` alone:** with a standby stopped, it still
   says "Cluster in healthy state"; `readyInstances` and the pods tell the
   truth, and the operator itself updates them 30-40 s after the fact. If
   the page is fresh, check `replication` even when status looks green.
2. **Follow the evidence**, not a checklist. Call only the tools the first
   step points to:

   | You see | Next |
   |---|---|
   | fewer ready instances than desired, a pod not ready, restarts | `replication`, `warning_events` |
   | `currentPrimary` != `targetPrimary` | a switchover or failover is in progress; `warning_events` |
   | `ContinuousArchiving` False, or the report mentions backups / PITR | `wal_archiving`, `recent_backups` |
   | slowness, timeouts, "too many connections" | `connections`, `long_transactions`, `blocking_locks`, `top_queries` |
   | everything green but the user reports a problem | `long_transactions`, `blocking_locks`, `connections` |

3. **Stop when the evidence explains the symptom.** Five or six tool calls is
   normally enough.

## Reading the tools

- `replication`: each healthy standby appears once with `state: streaming`.
  A missing standby is the finding; lag in seconds matters only above ~30.
- `wal_archiving`: compare `seconds_since_archived` with `archive_timeout`.
  A growing `failed_count` with a recent `seconds_since_failure` means
  archiving is failing now, and point-in-time recovery is limited to the
  last archived segment.
- `long_transactions`: anything older than a few minutes holds back vacuum
  and often holds locks. Query text is redacted (`'?'`, `?`): do not ask for
  the values.
- `blocking_locks`: one `blocker_pid` with many waiters is lock contention;
  the blocker's `state` (`idle in transaction` is the classic) says why.

## Reporting

Answer in this shape:

```
Diagnosis: <one of: healthy | long_transaction | lock_contention | replica_down | wal_archiving_failure | other>
Evidence: <the specific values from the tools, with tool names>
Impact: <what users or recovery are affected by>
Action for a human: <the concrete next step, linked to the runbook if known>
```

Use `healthy` when nothing in the database explains the symptom, even if
the page says the service is slow: "the database is fine, look elsewhere" is
a diagnosis. Use `other` only for a database problem outside the list.

## Actions

If a switchover or a backup would help, call `propose_action` with the
evidence in `reason`. It only records a proposal; say so, and give the
`approve_with` command. Never propose an action for a healthy cluster, and
never claim you changed anything.
