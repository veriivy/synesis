"""After K2 publishes an analysis, each advocate revises its own PoA in isolation.

They see the comparison (and the other plan's last published version, via the
analysis positions). They do not see the other agent's in-progress rewrite.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .k2 import extract_object
from .providers import agent_provider, chat

ADVOCATE_SYSTEM = """\
You are {agent_id}, the engineering advocate for {display_name} (user id `{user_id}`).
You are not a neutral assistant. You represent THIS user's requirements only.

THEIR REQUIREMENTS
{requirements_block}

K2 just published a structural comparison of your plan vs the other agent's plan.
You and the humans can both see it. Your job is to WRITE A NEW PLAN OF ACTION that
responds to that comparison — not a chat reply, a revised PoA.

HOW YOU REVISE
1. For EVERY blocking issue_id, your new plan must embody exactly one named move:
   CONCEDE, HOLD, or COMPROMISE. Name the move in `content`.
   COMPROMISE means a concrete third design (specific files, signatures, ownership).
   "We could support both", "make it configurable", and "either works" are not plans.
2. You may NOT drop a requirement marked must. If two musts collide, HOLD that part
   of the plan and COMPROMISE on file ownership / interfaces where you can.
3. Keep similarities K2 already found — do not reopen settled agreement.
4. The new PoA must be a full plan (summary, steps with files_touched, assumptions),
   not a delta.

Return JSON only, no markdown:
{{
  "content": "short speech: named move per blocking issue_id (under 200 words)",
  "addresses_issues": ["d1", "d2"],
  "poa": {{
    "poa_id": "...",
    "agent_id": "{agent_id}",
    "user_id": "{user_id}",
    "summary": "...",
    "steps": [{{
      "step_id": "s1",
      "title": "...",
      "description": "...",
      "files_touched": ["src/..."],
      "rationale": "..."
    }}],
    "assumptions": ["..."]
  }}
}}
"""


def _user_block(tasks: Any, user_id: str) -> tuple[str, str]:
    display = user_id
    lines: list[str] = []
    for entry in (tasks or {}).get("tasks_by_user") or []:
        if entry.get("user_id") != user_id:
            continue
        display = entry.get("display_name") or user_id
        for task in entry.get("tasks") or []:
            lines.append(f"- [{task.get('priority')}] {task.get('text')}")
    return display, "\n".join(lines) or "(none)"


def _normalize_poa(raw: Any, previous: dict, round_index: int) -> dict:
    poa = {
        "poa_id": previous.get("poa_id") or "poa",
        "agent_id": previous.get("agent_id"),
        "user_id": previous.get("user_id"),
        "summary": previous.get("summary") or "",
        "steps": list(previous.get("steps") or []),
        "assumptions": list(previous.get("assumptions") or []),
    }
    if not isinstance(raw, dict):
        return poa
    if raw.get("summary"):
        poa["summary"] = str(raw["summary"]).strip()
    if isinstance(raw.get("steps"), list) and raw["steps"]:
        steps = []
        for i, step in enumerate(raw["steps"], 1):
            if not isinstance(step, dict):
                continue
            files = step.get("files_touched") or []
            if not isinstance(files, list):
                files = [str(files)]
            steps.append(
                {
                    "step_id": str(step.get("step_id") or f"s{i}"),
                    "title": str(step.get("title") or f"Step {i}").strip(),
                    "description": str(step.get("description") or "").strip(),
                    "files_touched": [str(p) for p in files],
                    "rationale": str(step.get("rationale") or "").strip(),
                }
            )
        if steps:
            poa["steps"] = steps
    if isinstance(raw.get("assumptions"), list):
        poa["assumptions"] = [str(a) for a in raw["assumptions"]]
    base_id = str(previous.get("poa_id") or "poa").split("_r")[0]
    poa["poa_id"] = f"{base_id}_r{round_index}"
    poa["agent_id"] = previous.get("agent_id")
    poa["user_id"] = previous.get("user_id")
    return poa


def revise_poa(
    *,
    poa: dict,
    tasks: Any,
    analysis: dict,
    round_index: int,
) -> dict:
    """Revise one PoA from K2's comparison. Blocking — call from asyncio.to_thread."""
    agent_id = str(poa.get("agent_id") or "")
    user_id = str(poa.get("user_id") or "")
    display, reqs = _user_block(tasks, user_id)
    diffs = [d for d in analysis.get("differences") or [] if isinstance(d, dict)]
    blocking = [d for d in diffs if str(d.get("severity", "")).lower() == "blocking"]
    issue_ids = [str(d.get("issue_id")) for d in blocking if d.get("issue_id")]
    system = ADVOCATE_SYSTEM.format(
        agent_id=agent_id,
        display_name=display,
        user_id=user_id,
        requirements_block=reqs,
    )
    user = (
        f"Round {round_index}. Here is K2's comparison (humans see this too).\n"
        "Revise YOUR plan. Do not copy the other plan wholesale.\n\n"
        "Your current PoA:\n"
        + json.dumps(poa, indent=2)
        + "\n\nK2 analysis:\n"
        + json.dumps(
            {
                "round": analysis.get("round"),
                "similarities": analysis.get("similarities"),
                "differences": diffs,
                "converged": analysis.get("converged"),
            },
            indent=2,
        )
    )
    try:
        raw = chat(
            system=system,
            user=user,
            provider=agent_provider(agent_id),
            max_tokens=4096,
        )
        data = extract_object(raw)
        content = str(data.get("content") or "").strip() or "(revised plan, no speech)"
        addressed = data.get("addresses_issues")
        if not isinstance(addressed, list):
            addressed = re.findall(r"\bd\d+\b", content)
        addressed = [str(x) for x in addressed]
        new_poa = _normalize_poa(data.get("poa"), poa, round_index)
    except Exception as exc:  # noqa: BLE001 — keep the last plan rather than abort
        content = f"[{agent_id} could not revise this round: {exc}]"
        addressed = []
        new_poa = dict(poa)

    return {
        "agent_id": agent_id,
        "user_id": user_id,
        "content": content,
        "addresses_issues": addressed or issue_ids,
        "poa": new_poa,
    }
