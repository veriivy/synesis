"""K2's per-round structural diff of the two PoAs.

CLAUDE.md step 3b: "K2 produces the analysis: similarities, differences,
severity per difference." Not a summary — CLAUDE.md is explicit that K2
"does not summarize without listing concrete similarities and differences."
"""

from __future__ import annotations

import json

from ..jsonx import ChatProvider, call_model_json
from ..schemas import Analysis, PoA

ANALYSIS_SYSTEM = """You are K2, the moderator of a design negotiation between two AI \
agents, each advocating for a different user. You do not pick a winner. You structurally \
diff their two Plans of Action.

Return ONLY a JSON object:
{
  "round": <int>,
  "similarities": [{ "topic": "...", "detail": "..." }],
  "differences": [{ "issue_id": "d1", "topic": "...",
                     "positions": { "<agent_id>": "...", "<agent_id>": "..." },
                     "severity": "blocking" }],
  "converged": false
}

Rules:
- issue_id values are d1, d2, d3, ... unique within this round.
- positions keys are exactly the two agent_ids given to you.
- severity is "blocking" or "minor". A conflict between two must-haves is blocking.
- converged is true only if there are no blocking differences.
- Cite files_touched when two agents claim the same path.
- Paths listed under already_resolved_paths were settled in an earlier round by an
  explicit resolution — treat agreement on those as a similarity, not a new difference."""


def _build_prompt(poa_a: PoA, poa_b: PoA, round_: int, resolved_paths: set[str]) -> str:
    payload = {
        "round": round_,
        "poa_a": poa_a.model_dump(),
        "poa_b": poa_b.model_dump(),
        "resolved_paths": sorted(resolved_paths),
    }
    lines = [
        f"Round {round_}.",
        f"Agent {poa_a.agent_id}'s PoA and agent {poa_b.agent_id}'s PoA follow.",
        "CONTEXT_JSON: " + json.dumps(payload),
        "END_CONTEXT_JSON",
        "Diff these two PoAs.",
    ]
    return "\n".join(lines)


async def analyze(
    *,
    poa_a: PoA,
    poa_b: PoA,
    round_: int,
    resolved_paths: set[str],
    provider: ChatProvider,
    model: str,
    api_key: str | None = None,
) -> Analysis:
    messages = [{"role": "user", "content": _build_prompt(poa_a, poa_b, round_, resolved_paths)}]
    raw = await call_model_json(
        provider, messages=messages, system=ANALYSIS_SYSTEM, model=model, api_key=api_key
    )
    raw = dict(raw)
    raw.setdefault("round", round_)
    return Analysis(**raw)
