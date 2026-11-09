"""Fixed, read-only SQL against the cluster.

The agent cannot send SQL. It picks a tool; each tool runs one query from
QUERIES below:

- as a role that only has pg_monitor (deploy/postgres-role.yaml), so the
  database itself refuses anything else;
- inside a READ ONLY transaction with a short statement_timeout, so a slow
  catalog query cannot pile up during an incident;
- through the rw pooler, so it always reaches the current primary.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .redact import redact_sql

QUERIES: dict[str, str] = {
    "replication": """
        select application_name as standby, state, sync_state,
               pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint as replay_bytes_behind,
               extract(epoch from replay_lag)::float as replay_lag_seconds,
               extract(epoch from now() - backend_start)::int as connected_seconds
        from pg_stat_replication
        order by application_name""",
    "archiver": """
        select archived_count, failed_count,
               last_archived_wal,
               extract(epoch from now() - last_archived_time)::int as seconds_since_archived,
               last_failed_wal,
               extract(epoch from now() - last_failed_time)::int as seconds_since_failure,
               current_setting('archive_timeout') as archive_timeout
        from pg_stat_archiver""",
    "connections": """
        select coalesce(usename, '(background)') as usename,
               coalesce(state, backend_type) as state, count(*)::int as n
        from pg_stat_activity
        group by 1, 2
        order by n desc""",
    "max_connections": "select current_setting('max_connections')::int as max_connections",
    "long_transactions": """
        select pid, usename, state, wait_event_type, wait_event,
               extract(epoch from now() - xact_start)::int as xact_seconds,
               query
        from pg_stat_activity
        where xact_start is not null
          and backend_type = 'client backend'
          and now() - xact_start > make_interval(secs => %(min_seconds)s)
        order by xact_start
        limit 20""",
    "blocking": """
        select blocked.pid as blocked_pid, blocked.usename as blocked_user,
               extract(epoch from now() - blocked.query_start)::int as waiting_seconds,
               blocked.query as blocked_query,
               blocker.pid as blocker_pid, blocker.usename as blocker_user,
               blocker.state as blocker_state, blocker.query as blocker_query
        from pg_stat_activity blocked
        join lateral unnest(pg_blocking_pids(blocked.pid)) as b(pid) on true
        join pg_stat_activity blocker on blocker.pid = b.pid
        order by waiting_seconds desc
        limit 20""",
    "top_queries": """
        select calls, round(total_exec_time)::bigint as total_ms,
               round(mean_exec_time::numeric, 2)::float as mean_ms, rows, query
        from pg_stat_statements
        order by total_exec_time desc
        limit %(limit)s""",
}

# Columns holding SQL text, redacted before they leave this module.
SQL_COLUMNS = {"query", "blocked_query", "blocker_query"}


@dataclass(frozen=True)
class DSN:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> DSN:
        password = os.environ.get("PG_OPS_PASSWORD")
        if password is None and (path := os.environ.get("PG_OPS_PASSWORD_FILE")):
            with open(path) as f:
                password = f.read().strip()
        return cls(
            host=os.environ.get("PG_OPS_HOST", "localhost"),
            port=int(os.environ.get("PG_OPS_PORT", "5432")),
            dbname=os.environ.get("PG_OPS_DATABASE", "postgres"),
            user=os.environ.get("PG_OPS_USER", "pg_ops_agent"),
            password=password or "",
        )


def _psycopg_connect(dsn: DSN):
    import psycopg

    return psycopg.connect(
        host=dsn.host,
        port=dsn.port,
        dbname=dsn.dbname,
        user=dsn.user,
        password=dsn.password,
        connect_timeout=5,
        application_name="pg-ops-agent",
        # Transaction pooling: no server-side prepared statements.
        prepare_threshold=None,
    )


class SQLReader:
    def __init__(self, dsn: DSN, connect: Callable[[DSN], Any] = _psycopg_connect):
        self.dsn = dsn
        self.connect = connect

    def run(self, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        sql = QUERIES[name]
        with self.connect(self.dsn) as conn:
            # psycopg opens the transaction itself on the first statement;
            # read_only makes that BEGIN READ ONLY.
            conn.read_only = True
            with conn.cursor() as cur:
                # SET LOCAL lasts for this transaction only, which is what
                # transaction pooling needs.
                cur.execute("set local statement_timeout = '3s'")
                cur.execute("set local lock_timeout = '1s'")
                cur.execute(sql, params or {})
                cols = [c.name for c in cur.description]
                rows = cur.fetchall()
            conn.rollback()
        out = []
        for row in rows:
            rec = dict(zip(cols, row, strict=True))
            for col in SQL_COLUMNS & rec.keys():
                rec[col] = redact_sql(rec[col])
            out.append(rec)
        return out
