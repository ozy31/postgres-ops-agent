"""pg-ops-agent command line.

`serve` is what the agent's client starts. The other commands are for the
human: they run with the human's kubeconfig, not the server's identity.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from .actions import ActionError, ProposalStore, execute
from .config import Settings


def _store(settings: Settings) -> ProposalStore:
    return ProposalStore(settings.state_dir / "proposals")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pg-ops-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the MCP server on stdio")
    sub.add_parser("proposals", help="list proposed actions")
    approve = sub.add_parser("approve", help="run a pending proposal as you")
    approve.add_argument("id")
    reject = sub.add_parser("reject", help="reject a pending proposal")
    reject.add_argument("id")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "serve":
        from .server import main as serve

        serve()
        return 0

    store = _store(settings)
    if args.command == "proposals":
        for p in store.list():
            target = f" -> {p.target_instance}" if p.target_instance else ""
            print(f"{p.id}  {p.status:9}  {p.action}{target}  {p.cluster}  {p.created}")
            print(f"          reason: {p.reason}")
        return 0

    try:
        p = store.get(args.id)
        if p.status != "pending":
            raise ActionError(f"proposal {p.id} is {p.status}")
        p.decided = datetime.now(UTC).isoformat(timespec="seconds")
        if args.command == "reject":
            p.status = "rejected"
            store.save(p)
            print(f"rejected {p.id}")
            return 0
        from .kube import KubeReader

        kube = KubeReader.from_kubeconfig(p.namespace, p.cluster)
        p.result = execute(p, kube.cluster_status())
        p.status = "executed"
        store.save(p)
        print(p.result)
        return 0
    except (ActionError, Exception) as e:  # noqa: BLE001 - report and exit non-zero
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
