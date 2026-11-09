"""Inject each fault into the kind cluster, let the agent diagnose it, grade it.

    python evals/run.py --model qwen3:8b --runs 2

Grading is deterministic: the agent's "Diagnosis:" line must name the
injected category, and it must not propose an action on a healthy cluster.
e2e/test_faults.py has already shown the evidence is visible through the
tools, so a miss here is the model's, not the tooling's.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "evals"))

from agent import run_agent  # noqa: E402
from harness import agent_env  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", nargs="*", help="scenario names")
    args = parser.parse_args()

    env = agent_env()
    rows, raw = [], []
    for s in SCENARIOS:
        if args.only and s.name not in args.only:
            continue
        print(f"== {s.name}: injecting", flush=True)
        s.inject()
        try:
            for i in range(args.runs):
                r = asyncio.run(run_agent(env, args.model))
                ok = r.diagnosis == s.category and not (s.category == "healthy" and r.proposals)
                tools = Counter(c["tool"] for c in r.tool_calls)
                print(
                    f"   run {i + 1}: {r.diagnosis!s:22} {'PASS' if ok else 'FAIL'} "
                    f"{len(r.tool_calls)} calls {r.seconds}s {r.error or ''}",
                    flush=True,
                )
                rows.append((s.name, s.category, r, ok, tools))
                raw.append({"scenario": s.name, "expected": s.category, "pass": ok, **r.__dict__})
        finally:
            print(f"== {s.name}: cleaning up", flush=True)
            s.cleanup()

    passed = sum(ok for *_, ok, _ in rows)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = args.model.replace(":", "-").replace("/", "-")
    out = ROOT / "evals/results" / f"{stamp}-{slug}.md"
    lines = [
        f"# {args.model}, {stamp}",
        "",
        f"**{passed}/{len(rows)} correct** ({args.runs} run(s) per scenario, temperature 0).",
        "",
        "| Scenario | Expected | Diagnosis | Result | Tool calls | Time |",
        "|---|---|---|---|---|---|",
    ]
    for name, cat, r, ok, tools in rows:
        used = ", ".join(f"{t}x{n}" if n > 1 else t for t, n in tools.items())
        lines.append(
            f"| {name} | {cat} | {r.diagnosis or r.error or '-'} | {'pass' if ok else 'FAIL'} "
            f"| {used or '-'} | {r.seconds}s |"
        )
    out.write_text("\n".join(lines) + "\n")
    (out.with_suffix(".raw.jsonl")).write_text(
        "\n".join(json.dumps(x, default=str) for x in raw) + "\n"
    )
    print(f"\n{passed}/{len(rows)} correct -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
