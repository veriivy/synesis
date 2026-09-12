"""An agent's response to K2's per-round differences.

CLAUDE.md step 3d: each agent must respond to every blocking issue with
exactly one of concede / hold (with reason) / propose a compromise, citing
the issue_ids it addresses. This module makes that one call per agent;
k2/negotiation.py runs both agents' calls in parallel via asyncio.gather.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..jsonx import ChatProvider, call_model_json
from ..schemas import Difference, PoA

REPLY_SYSTEM = """You are an AI agent, advocating for your user in a shared coding \
session — not a neutral assistant. K2 has found differences between your Plan of Action \
and the other agent's. Respond to EVERY difference given to you with exactly one stance: \
"concede", "hold", or "compromise". You may NOT concede a must-have requirement without \
your user's explicit instruction — if it's a must-have for your user, hold or propose a \
compromise instead of conceding.

Return ONLY a JSON object:
{
  "content": "one message covering every issue below, citing each issue_id",
  "addresses_issues": ["d1", "d2"],
  "stances": { "d1": "concede" | "hold" | "compromise" }
}"""


@dataclass
class AgentReply:
    content: str
    addresses_issues: list[str] = field(default_factory=list)
    stances: dict[str, str] = field(default_factory=dict)


def _build_prompt(
    agent_id: str, other_agent_id: str, my_poa: PoA, differences: list[Difference]
) -> str:
    payload = {
        "agent_id": agent_id,
        "other_agent_id": other_agent_id,
        "my_poa": my_poa.model_dump(),
        "differences": [d.model_dump() for d in differences],
    }
    lines = [
        f"You are {agent_id}. K2's differences this round:",
        *(f"- {d.issue_id} ({d.severity}): {d.topic}" for d in differences),
        "CONTEXT_JSON: " + json.dumps(payload),
        "END_CONTEXT_JSON",
        "Respond to every difference above.",
    ]
    return "\n".join(lines)


async def respond_to_analysis(
    *,
    agent_id: str,
    other_agent_id: str,
    my_poa: PoA,
    differences: list[Difference],
    provider: ChatProvider,
    model: str,
    api_key: str | None = None,
) -> AgentReply:
    if not differences:
        return AgentReply(content="No differences addressed to me this round.")

    messages = [
        {"role": "user", "content": _build_prompt(agent_id, other_agent_id, my_poa, differences)}
    ]
    raw = await call_model_json(
        provider, messages=messages, system=REPLY_SYSTEM, model=model, api_key=api_key
    )
    raw = dict(raw)
    return AgentReply(
        content=raw.get("content", ""),
        addresses_issues=list(raw.get("addresses_issues", [])),
        stances={str(k): str(v) for k, v in dict(raw.get("stances", {})).items()},
    )
