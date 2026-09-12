"""Resilience wrapper around a single K2/agent model call.

CLAUDE.md: "Every model call: timeout, one retry, and a JSON-repair fallback.
Model output is untrusted input." This module is that guarantee, factored out
so both PoA generation and K2's ticket decomposition can share it.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol


class ChatProvider(Protocol):
    """The one interface every provider (and the stub) implements.

    Matches CLAUDE.md: chat(messages, system, model, api_key=None) -> str.
    Real provider clients are out of scope here — this is just the seam.
    """

    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        system: str,
        model: str,
        api_key: str | None = None,
    ) -> str: ...


class ModelOutputError(Exception):
    """Raised when a model call fails, or its output can't be coerced to JSON,
    even after the one retry CLAUDE.md requires."""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_loose(text: str) -> Any:
    """Best-effort JSON parse of raw model output.

    Handles the common failure shapes: markdown code fences, and leading/
    trailing prose around the actual JSON object/array.
    """
    text = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON object/array found in model output")
    start = min(starts)
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i != -1]
    end = (max(ends) + 1) if ends else len(text)

    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not repair model JSON output: {exc}") from exc


async def call_model_json(
    provider: ChatProvider,
    *,
    messages: list[dict[str, str]],
    system: str,
    model: str,
    api_key: str | None = None,
    timeout_s: float = 10.0,
) -> Any:
    """Call `provider.chat`, coercing the response to JSON.

    One retry total (per CLAUDE.md), covering both a failed/timed-out call
    and a call that returned unparseable JSON — in the latter case the retry
    tells the model its last output was invalid and asks for corrected JSON.
    """
    last_error: Exception | None = None
    attempt_messages = messages

    for attempt in range(2):
        try:
            raw = await asyncio.wait_for(
                provider.chat(
                    messages=attempt_messages,
                    system=system,
                    model=model,
                    api_key=api_key,
                ),
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - untrusted call, must not raise raw
            last_error = exc
            continue

        try:
            return parse_json_loose(raw)
        except ValueError as exc:
            last_error = exc
            attempt_messages = attempt_messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON. Reply with ONLY the "
                        "corrected JSON — no prose, no markdown fences."
                    ),
                },
            ]

    raise ModelOutputError(f"model call failed after retry: {last_error}") from last_error
