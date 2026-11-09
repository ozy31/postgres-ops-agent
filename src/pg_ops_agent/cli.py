"""pg-ops-agent command line."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pg-ops-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the MCP server on stdio")
    args = parser.parse_args(argv)

    if args.command == "serve":
        from .server import main as serve

        serve()


if __name__ == "__main__":
    main()
