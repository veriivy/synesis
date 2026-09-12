"""K2 analysis — the same call k2mod.py uses, imported by the orchestrator."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

SYSTEM = """\
You are K2, the moderator of a design negotiation. You do not advocate for either user.
You structurally diff two Plans of Action. You do not pick a winner. You do not summarize
without listing concrete similarities and differences.

Return a single JSON object, no markdown, matching this shape exactly:
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
- issue_id values are d1, d2, d3, ...
- positions keys are the agent_ids from the PoAs.
- severity is "blocking" or "minor". A conflict between two must-haves is blocking.
- converged is true only if there are no blocking differences. If a must-have on each
  side still collides, converged is false.
- Cite files_touched when two agents claim the same path.
- When a prior-round transcript is present, it includes revised PoAs. Diff those
  latest plans, not only the opening drafts.
"""


def load_fixture(name: str) -> Any:
    path = FIXTURES / name
    if not path.is_file():
        raise FileNotFoundError(f"missing fixture: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def extract_object(text: str) -> dict:
    """Model output is untrusted. Recover a JSON object or fail clearly."""
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    raise ValueError(f"K2 did not return JSON:\n{text[:800]}")


def ifm_settings() -> tuple[str, str, str]:
    api_key = os.getenv("IFM_API_KEY", "").strip()
    base_url = os.getenv("IFM_BASE_URL", "").strip()
    model = os.getenv("IFM_MODEL", "").strip()
    missing = [
        name
        for name, value in (
            ("IFM_API_KEY", api_key),
            ("IFM_BASE_URL", base_url),
            ("IFM_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)} — copy .env.example to .env and fill them in"
        )
    return api_key, base_url, model


def chat(*, system: str, user: str, max_tokens: int = 2048) -> str:
    """One completion. Advocates and K2 both go through here so the model is swappable."""
    api_key, base_url, model = ifm_settings()
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=1)
    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # Some gateways want max_tokens, some want max_completion_tokens. Prefer the
    # older name; if the gateway rejects it, retry once without a cap.
    try:
        resp = client.chat.completions.create(**kwargs, max_tokens=max_tokens)
    except Exception:
        resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


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
        extra = "\n\nPrior-round transcript (use this; do not only re-diff the original PoAs):\n" + transcript
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
        + f"\n\nDiff the current positions. Round is {round_index}."
    )
    raw = chat(system=SYSTEM, user=user, max_tokens=4096)
    analysis = extract_object(raw)
    analysis["round"] = round_index
    analysis.setdefault("similarities", [])
    analysis.setdefault("differences", [])
    analysis["converged"] = has_converged(analysis)
    return analysis
