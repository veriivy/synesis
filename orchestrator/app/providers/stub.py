"""Deterministic fake provider for dev/tests — no network calls.

Implements the same chat(messages, system, model, api_key=None) -> str
interface real providers will (see app/jsonx.py:ChatProvider). Scripted with
a queue of canned responses so tests can exercise K2's retry/regeneration
paths without a live model.
"""

from __future__ import annotations

import json


class StubChatProvider:
    def __init__(self, script: list[str] | None = None):
        self._script = list(script or [])
        self.calls: list[dict] = []

    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        system: str,
        model: str,
        api_key: str | None = None,
    ) -> str:
        self.calls.append({"messages": messages, "system": system, "model": model})
        if self._script:
            return self._script.pop(0)
        return json.dumps([])
