"""One K2 call: diff the two fixture PoAs and print an analysis JSON.

Reads IFM_API_KEY / IFM_BASE_URL / IFM_MODEL from .env. No secrets in this file.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
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
- converged is true only if there are no blocking differences.
- Cite files_touched when two agents claim the same path.
"""


def load_json(path: Path) -> object:
    if not path.is_file():
        sys.exit(f"missing fixture: {path}")
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


def main() -> int:
    load_dotenv(ROOT / ".env")

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
        sys.exit(
            f"missing {', '.join(missing)} — copy .env.example to .env and fill them in"
        )

    poa1 = load_json(FIXTURES / "PoA1.json")
    poa2 = load_json(FIXTURES / "PoA2.json")
    context = load_json(FIXTURES / "context.json")
    tasks = load_json(FIXTURES / "tasks.json")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=1)

    user = (
        "Shared context:\n"
        + json.dumps(context, indent=2)
        + "\n\nTask lists:\n"
        + json.dumps(tasks, indent=2)
        + "\n\nPoA1:\n"
        + json.dumps(poa1, indent=2)
        + "\n\nPoA2:\n"
        + json.dumps(poa2, indent=2)
        + "\n\nDiff these two PoAs. Round is 1."
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    analysis = extract_object(raw)
    print(json.dumps(analysis, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
