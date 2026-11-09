"""Strip data out of text before an LLM sees it.

Query text from pg_stat_activity and log lines can carry customer data in
literals (WHERE email = 'a@b.com'). The agent needs the shape of a query, not
its values.
"""

from __future__ import annotations

import re

_STRING = re.compile(r"'(?:[^']|'')*'")
_DOLLAR = re.compile(r"\$([A-Za-z_]*)\$.*?\$\1\$", re.DOTALL)
_NUMBER = re.compile(r"(?<![\w$.])-?\d+(?:\.\d+)?(?![\w.])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\s*[=:]\s*)\S+")


def redact_sql(text: str | None, max_len: int = 300) -> str | None:
    if text is None:
        return None
    text = _DOLLAR.sub("$?$", text)
    text = _STRING.sub("'?'", text)
    text = _NUMBER.sub("?", text)
    text = _EMAIL.sub("<email>", text)
    text = _SECRET.sub(r"\1\2<redacted>", text)
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def redact_text(text: str | None, max_len: int = 500) -> str | None:
    """Free text such as log messages: keep numbers (durations, LSNs), drop
    quoted values, emails and anything that looks like a credential."""
    if text is None:
        return None
    text = _STRING.sub("'?'", text)
    text = _EMAIL.sub("<email>", text)
    text = _SECRET.sub(r"\1\2<redacted>", text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."
