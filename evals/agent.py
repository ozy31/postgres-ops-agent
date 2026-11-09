"""A minimal tool-using agent loop over the MCP server.

Any OpenAI-compatible chat endpoint works: Ollama or LiteLLM locally (no key),
or a hosted provider. The agent gets the postgres-triage skill as its system
prompt and the MCP tools as functions, and must end with a diagnosis.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / ".claude/skills/postgres-triage/SKILL.md").read_text().split("---", 2)[2]
TASK = (
    "On-call page: 'orders service is slow and some requests fail, please check the "
    "database'. Investigate orders-db with the tools, then answer in the reporting "
    "shape from your instructions."
)
DIAGNOSIS = re.compile(r"Diagnosis:\s*\**\s*([a-z_]+)", re.IGNORECASE)


@dataclass
class Run:
    diagnosis: str | None = None
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    proposals: int = 0
    seconds: float = 0.0
    error: str | None = None


def _function_schema(tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or "")[:1000],
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }


def _text(result) -> str:
    if result.is_error:
        return json.dumps({"error": " ".join(getattr(c, "text", "") for c in result.content)})
    if result.structured_content is not None:
        return json.dumps(result.structured_content, default=str)[:6000]
    return " ".join(getattr(c, "text", "") for c in result.content)[:6000]


async def run_agent(env: dict[str, str], model: str, max_steps: int = 10) -> Run:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=os.environ.get("EVAL_BASE_URL", "http://localhost:11434/v1"),
        # Ollama ignores the key; hosted providers read it from the environment.
        api_key=os.environ.get("EVAL_API_KEY", "unused"),
    )
    params = StdioServerParameters(
        command=str(ROOT / ".venv/bin/pg-ops-agent"), args=["serve"], env=env, cwd=str(ROOT)
    )
    run, start = Run(), time.monotonic()
    async with (
        client,
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = [_function_schema(t) for t in (await session.list_tools()).tools]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SKILL},
            {"role": "user", "content": TASK},
        ]
        for _ in range(max_steps):
            resp = await client.chat.completions.create(
                model=model, messages=messages, tools=tools, temperature=0
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                run.answer = msg.content or ""
                break
            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await session.call_tool(call.function.name, args)
                run.tool_calls.append({"tool": call.function.name, "args": args})
                if call.function.name == "propose_action":
                    run.proposals += 1
                messages.append({"role": "tool", "tool_call_id": call.id, "content": _text(result)})
        else:
            run.error = f"no answer after {max_steps} steps"
    run.seconds = round(time.monotonic() - start, 1)
    # Thinking models may wrap reasoning in <think>; grade the final answer.
    final = re.sub(r"<think>.*?</think>", "", run.answer, flags=re.DOTALL)
    if m := DIAGNOSIS.search(final):
        run.diagnosis = m.group(1).lower()
    return run
