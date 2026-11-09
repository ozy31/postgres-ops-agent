"""Faults injected into the real cluster, shared by the e2e tests and the
LLM evals. Each scenario knows how to break the cluster, how to undo it, and
which diagnosis category a correct agent should reach."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from harness import kubectl, psql_admin

CATEGORIES = [
    "healthy",
    "long_transaction",
    "lock_contention",
    "replica_down",
    "wal_archiving_failure",
]


def _primary() -> str:
    return kubectl(
        "get",
        "pods",
        "-l",
        "cnpg.io/cluster=orders-db,cnpg.io/instanceRole=primary",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )


def _replica() -> str:
    return kubectl(
        "get",
        "pods",
        "-l",
        "cnpg.io/cluster=orders-db,cnpg.io/instanceRole=replica",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )


def _background_psql(sql: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "kubectl",
            "-n",
            "orders",
            "exec",
            _primary(),
            "-c",
            "postgres",
            "--",
            "psql",
            "-qtAX",
            "-d",
            "orders",
            "-c",
            sql,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait(pred: Callable[[], bool], timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(2)
    raise TimeoutError(what)


@dataclass
class Scenario:
    name: str
    category: str
    summary: str
    inject: Callable[[], None]
    cleanup: Callable[[], None]
    state: dict = field(default_factory=dict)


# --- long transaction --------------------------------------------------------

_procs: dict[str, list[subprocess.Popen]] = {}


def _long_tx_inject():
    psql_admin("create table if not exists invoices (id int, total numeric)")
    _procs["long_tx"] = [
        _background_psql("begin; insert into invoices values (1, 99.5); select pg_sleep(600);")
    ]
    time.sleep(8)


def _kill(key: str):
    for p in _procs.pop(key, []):
        p.terminate()
    psql_admin(
        "select pg_terminate_backend(pid) from pg_stat_activity "
        "where query like '%pg_sleep(600)%' and pid <> pg_backend_pid()"
    )


# --- lock contention ---------------------------------------------------------


def _locks_inject():
    psql_admin("create table if not exists stock (sku text, qty int)")
    holder = _background_psql(
        "begin; lock table stock in access exclusive mode; select pg_sleep(600);"
    )
    time.sleep(3)
    waiters = [_background_psql("select count(*) from stock;") for _ in range(3)]
    _procs["locks"] = [holder, *waiters]
    time.sleep(6)


def _locks_cleanup():
    _kill("locks")
    psql_admin(
        "select pg_terminate_backend(pid) from pg_stat_activity "
        "where query like '%from stock%' and pid <> pg_backend_pid()"
    )


# --- replica down --------------------------------------------------------------


def _fence(instances: list[str]):
    value = json.dumps(instances)
    kubectl("annotate", "cluster", "orders-db", f"cnpg.io/fencedInstances={value}", "--overwrite")


def _replica_down_inject():
    victim = _replica()
    _replica_down.state["victim"] = victim
    _fence([victim])
    _wait(
        lambda: (
            psql_admin("select count(*) from pg_stat_replication where state = 'streaming'") == "1"
        ),
        120,
        "fenced replica still streaming",
    )

    # The operator notices ~30-40s later, and even then the phase stays
    # "Cluster in healthy state"; only readyInstances and the pod say otherwise.
    def operator_caught_up() -> bool:
        ready = kubectl(
            "get", "pod", victim, "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"
        )
        count = kubectl("get", "cluster", "orders-db", "-o", "jsonpath={.status.readyInstances}")
        return ready == "False" and count == "2"

    _wait(operator_caught_up, 180, "operator status did not catch up")


def _replica_down_cleanup():
    kubectl("annotate", "cluster", "orders-db", "cnpg.io/fencedInstances-")
    _wait(
        lambda: (
            kubectl("get", "cluster", "orders-db", "-o", "jsonpath={.status.readyInstances}") == "3"
        ),
        300,
        "fenced replica did not come back",
    )


# --- WAL archiving failure -------------------------------------------------------


def _patch_endpoint(url: str):
    kubectl(
        "patch",
        "objectstore",
        "orders-db-backups",
        "--type",
        "merge",
        "-p",
        json.dumps({"spec": {"configuration": {"endpointURL": url}}}),
    )


def _archiving_inject():
    before = int(psql_admin("select failed_count from pg_stat_archiver"))
    _patch_endpoint("http://seaweedfs-gone.object-store.svc:8333")

    def failed() -> bool:
        psql_admin("select pg_switch_wal()")
        return int(psql_admin("select failed_count from pg_stat_archiver")) > before

    _wait(failed, 180, "archiver did not start failing")


def _archiving_cleanup():
    _patch_endpoint("http://seaweedfs.object-store.svc:8333")

    def archived() -> bool:
        psql_admin("select pg_switch_wal()")
        return (
            psql_admin(
                "select coalesce(last_archived_time > last_failed_time, true) from pg_stat_archiver"
            )
            == "t"
        )

    _wait(archived, 300, "archiving did not recover")


_replica_down = Scenario(
    "replica-down",
    "replica_down",
    "One standby is fenced: Postgres stopped on it, the pod stays.",
    _replica_down_inject,
    _replica_down_cleanup,
)

SCENARIOS = [
    Scenario("healthy", "healthy", "Nothing is wrong.", lambda: None, lambda: None),
    Scenario(
        "long-transaction",
        "long_transaction",
        "A session opened a transaction, wrote a row and is sleeping inside it.",
        _long_tx_inject,
        lambda: _kill("long_tx"),
    ),
    Scenario(
        "lock-contention",
        "lock_contention",
        "A session holds an ACCESS EXCLUSIVE lock on a table; three readers wait.",
        _locks_inject,
        _locks_cleanup,
    ),
    _replica_down,
    Scenario(
        "wal-archiving-failure",
        "wal_archiving_failure",
        "The object store endpoint is wrong; every WAL segment fails to archive.",
        _archiving_inject,
        _archiving_cleanup,
    ),
]

BY_NAME = {s.name: s for s in SCENARIOS}
