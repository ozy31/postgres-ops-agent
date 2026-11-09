from types import SimpleNamespace as NS

import pytest

from pg_ops_agent.redact import redact_sql, redact_text
from pg_ops_agent.sql import DSN, QUERIES, SQLReader


class FakeCursor:
    def __init__(self, conn, rows, cols):
        self.conn, self.rows, self.description = conn, rows, [NS(name=c) for c in cols]

    def execute(self, sql, params=None):
        self.conn.statements.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.statements, self.read_only, self.rolled_back = [], False, False

    def cursor(self):
        assert self.read_only, "read_only must be set before any statement"
        return FakeCursor(self, self.rows, self.cols)

    def rollback(self):
        self.rolled_back = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


DSN_ = DSN("pooler", 5432, "orders", "pg_ops_agent", "x")


def reader(rows, cols):
    conn = FakeConn(rows, cols)
    return SQLReader(DSN_, connect=lambda dsn: conn), conn


def test_runs_read_only_with_timeouts_and_rolls_back():
    r, conn = reader([(1, 0)], ["archived_count", "failed_count"])
    assert r.run("archiver") == [{"archived_count": 1, "failed_count": 0}]
    assert conn.read_only and conn.rolled_back
    stmts = [s for s, _ in conn.statements]
    assert stmts[0] == "set local statement_timeout = '3s'"
    assert stmts[1] == "set local lock_timeout = '1s'"
    assert stmts[2].startswith("select archived_count")


def test_query_text_is_redacted():
    r, _ = reader(
        [(42, "app", "active", None, None, 120, "update users set email='a@b.com' where id=7")],
        ["pid", "usename", "state", "wait_event_type", "wait_event", "xact_seconds", "query"],
    )
    row = r.run("long_transactions", {"min_seconds": 60})[0]
    assert row["query"] == "update users set email='?' where id=?"
    assert row["pid"] == 42  # numbers outside SQL text are untouched


def test_unknown_query_is_impossible():
    r, _ = reader([], [])
    with pytest.raises(KeyError):
        r.run("drop table users")


def test_queries_are_all_selects():
    for name, sql in QUERIES.items():
        assert sql.strip().lower().startswith("select"), name


@pytest.mark.parametrize(
    "raw, want",
    [
        ("select * from t where name = 'O''Brien'", "select * from t where name = '?'"),
        ("select $$secret body$$, 3.14", "select $?$, ?"),
        ("select * from t where id = $1", "select * from t where id = $1"),
        ("insert into t values (10, -2)", "insert into t values (?, ?)"),
        ("select col1, t2.x from t2", "select col1, t2.x from t2"),
        ("alter role app password 'hunter2'", "alter role app password '?'"),
    ],
)
def test_redact_sql(raw, want):
    assert redact_sql(raw) == want


def test_redact_text_keeps_numbers_drops_secrets():
    msg = "password=hunter2 failed for user 'app' after 3 tries, mail ops@example.com"
    assert redact_text(msg) == (
        "password=<redacted> failed for user '?' after 3 tries, mail <email>"
    )
