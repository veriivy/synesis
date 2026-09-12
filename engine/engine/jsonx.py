"""Model output is untrusted input.

Models wrap JSON in prose, in ``` fences, in both, or emit something that is nearly
JSON. Every structured read from a model goes through `extract_json`, which never
raises — it returns None and lets the caller fall back to a safe default. A malformed
moderator response must cost us one round, not the demo.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json|jsonc)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _balanced_span(text: str, open_ch: str, close_ch: str) -> str | None:
    """The first balanced {...} / [...] run, ignoring braces inside strings."""
    start = text.find(open_ch)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def extract_json(text: str) -> Any | None:
    """Best-effort parse of the first JSON value in `text`. Never raises.

    Tries, in order: the whole string, each ``` fence, then the first balanced object
    or array found anywhere. Each candidate is retried once with trailing commas
    stripped, which is the single most common way models produce invalid JSON.
    """
    if not text or not text.strip():
        return None

    candidates: list[str] = [text.strip()]
    candidates.extend(m.strip() for m in _FENCE.findall(text))
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        span = _balanced_span(text, open_ch, close_ch)
        if span:
            candidates.append(span)

    for candidate in candidates:
        for attempt in (candidate, _strip_trailing_commas(candidate)):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, ValueError):
                continue

    log.warning("no parseable JSON in model output (%d chars)", len(text))
    return None


def extract_object(text: str) -> dict | None:
    """`extract_json` restricted to objects. Returns None for anything else."""
    value = extract_json(text)
    return value if isinstance(value, dict) else None
