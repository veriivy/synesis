"""Per-agent Plan of Action generation.

CLAUDE.md step 2: "Generate both PoAs in parallel." This module is one
call; the caller (k2/negotiation.py) is what runs the two in parallel via
asyncio.gather.
"""

from __future__ import annotations

import json

from ..jsonx import ChatProvider, call_model_json
from ..schemas import PoA, Task

POA_SYSTEM = """You are an AI agent drafting a Plan of Action for ONE user in a shared \
coding session. You are that user's advocate, not a neutral assistant — do not soften or \
drop their "must" requirements. A moderator (K2) will later diff your plan against the \
other user's agent's plan and may ask you to defend or compromise on specific points.

Return ONLY a JSON object:
{
  "summary": "...",
  "steps": [{ "step_id": "s1", "title": "...", "description": "...",
              "files_touched": ["path/to/file.py"], "rationale": "..." }],
  "assumptions": ["..."]
}

Every step must name concrete files_touched paths. Rationale should cite which of the \
user's tasks (and priority) the step honors."""


def _build_prompt(user_id: str, tasks: list[Task]) -> str:
    payload = {"user_id": user_id, "tasks": [t.model_dump() for t in tasks]}
    lines = [f"User {user_id}'s tasks:"]
    for t in tasks:
        lines.append(f"- ({t.priority}) {t.text}")
    lines.append("CONTEXT_JSON: " + json.dumps(payload))
    lines.append("END_CONTEXT_JSON")
    lines.append("Draft the PoA now.")
    return "\n".join(lines)


async def generate_poa(
    *,
    agent_id: str,
    user_id: str,
    tasks: list[Task],
    provider: ChatProvider,
    model: str,
    api_key: str | None = None,
) -> PoA:
    messages = [{"role": "user", "content": _build_prompt(user_id, tasks)}]
    raw = await call_model_json(
        provider, messages=messages, system=POA_SYSTEM, model=model, api_key=api_key
    )
    raw = dict(raw)
    raw["poa_id"] = f"poa_{agent_id}"
    raw["agent_id"] = agent_id
    raw["user_id"] = user_id
    return PoA(**raw)
