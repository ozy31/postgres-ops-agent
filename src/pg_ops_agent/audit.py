"""Append-only JSON-lines record of every tool call the agent makes."""

from __future__ import annotations

import functools
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Audit:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields: Any) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), **fields}
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def wrap(self, fn):
        """Record name, arguments, outcome, duration and result size of each
        call. functools.wraps keeps the signature MCP builds its schema from."""

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                self.record(
                    tool=fn.__name__,
                    args=kwargs,
                    ok=False,
                    error=repr(e)[:300],
                    ms=round((time.monotonic() - start) * 1000),
                )
                raise
            self.record(
                tool=fn.__name__,
                args=kwargs,
                ok=True,
                ms=round((time.monotonic() - start) * 1000),
                result_bytes=len(json.dumps(result, default=str)),
            )
            return result

        return wrapper
