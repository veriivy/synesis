"""K2 analysis — the same call k2mod.py uses, imported by the orchestrator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .providers import chat, moderator_provider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

SYSTEM = """\
You are K2, the moderator of a design negotiation. You do not advocate for either user.
You structurally diff two Plans of Action. You do not pick a winner.

Return ONE compact JSON object. No markdown, no commentary, no truncated strings.
{
  "round": 1,
  "similarities": [{ "topic": "...", "detail": "..." }],
  "differences": [{
    "issue_id": "d1",
    "topic": "...",
    "positions": { "a1": "...", "a2": "..." },
    "severity": "blocking"
  }],
  "converged": false
}

Rules:
- At most 4 similarities and 4 differences. Each detail/position is ONE short sentence.
- issue_id values are d1, d2, d3, ...
- positions keys are the agent_ids from the PoAs.
- severity is "blocking" or "minor". Two must-haves colliding is blocking.
- converged is true only if there are no blocking differences.
- When a prior-round transcript is present, diff the LATEST PoAs, not the opening drafts.
- Finish the JSON. Never cut a string mid-word.
"""


def load_fixture(name: str) -> Any:
    path = FIXTURES / name
    if not path.is_file():
        raise FileNotFoundError(f"missing fixture: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _repair_truncated_object(text: str) -> str | None:
    """Close a JSON object that was cut off mid-generation (token cap)."""
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in s:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    if in_str:
        s += '"'
    s += "".join(reversed(stack))
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    try:
        json.loads(s)
    except json.JSONDecodeError:
        return None
    return s


def extract_object(text: str) -> dict:
    """Model output is untrusted. Recover a JSON object or fail clearly."""
    text = text.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    repaired = _repair_truncated_object(text)
    if repaired:
        candidates.append(repaired)

    last_error: Exception | None = None
    for raw in candidates:
        for variant in (raw, re.sub(r",(\s*[}\]])", r"\1", raw)):
            try:
                data = json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(data, dict):
                return data
    raise ValueError(f"K2 did not return JSON ({last_error}):\n{text[:800]}")


def blocking_ids(analysis: dict) -> list[str]:
    ids = []
    for diff in analysis.get("differences") or []:
        if not isinstance(diff, dict):
            continue
        if str(diff.get("severity", "")).lower() == "blocking":
            issue_id = str(diff.get("issue_id") or "").strip()
            if issue_id:
                ids.append(issue_id)
    return ids


def has_converged(analysis: dict) -> bool:
    """Blocking diffs win over a model's converged:true tick."""
    if blocking_ids(analysis):
        return False
    return bool(analysis.get("converged"))


def analyze(
    *,
    context: Any,
    tasks: Any,
    poa1: Any,
    poa2: Any,
    round_index: int = 1,
    transcript: str = "",
) -> dict:
    """One K2 call. Blocking — run in a thread from the FastAPI event loop."""
    extra = ""
    if transcript.strip():
        clipped = transcript.strip()
        if len(clipped) > 6000:
            clipped = clipped[-6000:]
        extra = (
            "\n\nPrior-round transcript (latest PoAs matter; ignore fluff):\n" + clipped
        )
    user = (
        "Shared context:\n"
        + json.dumps(context, indent=2)
        + "\n\nTask lists:\n"
        + json.dumps(tasks, indent=2)
        + "\n\nPoA1:\n"
        + json.dumps(poa1, indent=2)
        + "\n\nPoA2:\n"
        + json.dumps(poa2, indent=2)
        + extra
        + f"\n\nDiff the current positions. Round is {round_index}. "
        "Compact JSON only. Finish the object."
    )
    raw = chat(
        system=SYSTEM,
        user=user,
        provider=moderator_provider(),
        max_tokens=8192,
    )
    try:
        analysis = extract_object(raw)
    except ValueError:
        try:
            raw = chat(
                system=SYSTEM,
                user=(
                    f"Round {round_index}. Reply with compact valid JSON only "
                    "(similarities + differences + converged). No markdown."
                ),
                provider=moderator_provider(),
                max_tokens=2048,
            )
            analysis = extract_object(raw)
        except ValueError:
            # Do not kill the room. Round 3 often truncates; keep going.
            analysis = {
                "round": round_index,
                "similarities": [],
                "differences": [],
                "converged": False,
                "note": "moderator JSON truncated; treating round as not converged",
            }
    analysis["round"] = round_index
    analysis.setdefault("similarities", [])
    analysis.setdefault("differences", [])
    analysis["converged"] = has_converged(analysis)
    return analysis
