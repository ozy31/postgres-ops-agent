"""Run the real MCP server against the kind cluster, as the agent would.

The server is started over stdio with the agent's own identity: a kubeconfig
for the pg-ops-agent service account and the ops_agent database role
(hack/agent-env.sh). Admin access is used only to set up faults. The
pooler is reached on 127.0.0.1:15432 through the kind port mapping.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
PORT = 15432


def agent_env() -> dict[str, str]:
    out = subprocess.run(
        [str(ROOT / "hack/agent-env.sh")], check=True, capture_output=True, text=True, cwd=ROOT
    ).stdout
    env = {}
    for line in out.splitlines():
        for pair in shlex.split(line.removeprefix("export ")):
            k, _, v = pair.partition("=")
            env[k] = v
    env["PG_OPS_STATE_DIR"] = str(ROOT / ".state")
    env["PATH"] = os.environ["PATH"]
    return env


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    """kubectl as the admin (current context), for setup and fault injection."""
    return subprocess.run(
        ["kubectl", "-n", "orders", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=check,
    ).stdout.strip()


def psql_admin(sql: str, db: str = "orders") -> str:
    primary = kubectl(
        "get",
        "pods",
        "-l",
        "cnpg.io/cluster=orders-db,cnpg.io/instanceRole=primary",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    return kubectl("exec", primary, "-c", "postgres", "--", "psql", "-qtAX", "-d", db, "-c", sql)


def _body(result) -> Any:
    if result.is_error:
        return {"error": " ".join(c.text for c in result.content if hasattr(c, "text"))}
    body = result.structured_content
    if body is None:
        return json.loads(result.content[0].text)
    return body.get("result", body) if set(body) == {"result"} else body


async def _session(env: dict[str, str], calls: list[tuple[str, dict[str, Any]]]):
    params = StdioServerParameters(
        command=str(ROOT / ".venv/bin/pg-ops-agent"), args=["serve"], env=env, cwd=str(ROOT)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        results = [_body(await session.call_tool(name, args)) for name, args in calls]
        return tools, results


def mcp_calls(env: dict[str, str], *calls: tuple[str, dict[str, Any]]):
    """Start the server, list its tools, make the calls, stop it."""
    return asyncio.run(_session(env, list(calls)))
