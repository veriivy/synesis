"""K2's plan -> tickets step.

CLAUDE.md step 5/6: after both approvals, decompose the FinalPlan into
tickets (emit tickets_created), each with disjoint files_owned so parallel
agents can't collide. The actual collision-freedom guarantee lives in
validator.py; this module is just the prompt + retry-on-bad-ticket loop
around it.
"""

from __future__ import annotations

import json

from ..jsonx import ChatProvider, call_model_json
from ..schemas import FinalPlan, Ticket
from .validator import Adjustment, EmptyFilesOwnedError, validate_and_fix_tickets

K2_TICKETING_SYSTEM = """You are K2, moderating a shared coding session between two AI \
agents. You are given an approved FinalPlan's steps. Decompose them into tickets, one \
or more per step, so the two agents can execute as much as possible in parallel.

Return ONLY a JSON array of ticket objects, each with exactly these fields:
- ticket_id: string, unique
- title: string
- description: string
- assigned_agent: string, one of the agent ids implied by the plan (e.g. "a1", "a2")
- files_owned: non-empty list of file paths this ticket alone may write
- lane: "parallel" or "sequential"
- depends_on: list of ticket_id this ticket must wait on (usually empty)

Every ticket MUST own at least one file — never return an empty files_owned. Prefer \
"parallel"; only mark a ticket "sequential" if it genuinely cannot start until another \
ticket finishes. Do not let two tickets claim the same file unless you intend one to \
depend on the other."""


def _build_user_prompt(plan: FinalPlan, step_owner: dict[str, str] | None) -> str:
    lines = [f"Plan summary: {plan.summary}", "Steps:"]
    for step in plan.steps:
        files = ", ".join(step.files_touched) or "unspecified"
        lines.append(f"- {step.step_id}: {step.title} — {step.description} (files: {files})")
    # CONTEXT_JSON: structured mirror of the above, for the template
    # provider (providers/templating.py:TemplateTicketingProvider) to parse
    # without needing to understand English. Real providers read the lines
    # above and ignore this block.
    context = {
        "steps": [
            {
                "step_id": s.step_id,
                "title": s.title,
                "files_touched": s.files_touched,
                "owner": (step_owner or {}).get(s.step_id),
            }
            for s in plan.steps
        ]
    }
    lines.append("CONTEXT_JSON: " + json.dumps(context))
    lines.append("END_CONTEXT_JSON")
    return "\n".join(lines)


def _to_ticket(raw: dict, plan_id: str) -> Ticket:
    raw = dict(raw)
    raw.setdefault("lane", "parallel")
    raw.setdefault("depends_on", [])
    raw["plan_id"] = plan_id
    raw["status"] = "pending"
    return Ticket(**raw)


async def decompose_plan_to_tickets(
    plan: FinalPlan,
    provider: ChatProvider,
    *,
    model: str,
    api_key: str | None = None,
    max_regenerations: int = 1,
    step_owner: dict[str, str] | None = None,
) -> tuple[list[Ticket], list[Adjustment]]:
    """Ask K2 to break `plan` into tickets, then run the code-level
    disjointness validator. If K2 leaves a ticket with no files_owned,
    reject and ask it to regenerate (bounded by max_regenerations) instead
    of guessing ownership on its behalf.

    `step_owner` (step_id -> agent_id) is optional context for the template
    provider, which has no way to infer authorship from prose; a real model
    can usually infer it from the plan summary/rationale alone.
    """
    messages = [{"role": "user", "content": _build_user_prompt(plan, step_owner)}]

    for attempt in range(max_regenerations + 1):
        raw_tickets = await call_model_json(
            provider,
            messages=messages,
            system=K2_TICKETING_SYSTEM,
            model=model,
            api_key=api_key,
        )
        tickets = [_to_ticket(raw, plan.plan_id) for raw in raw_tickets]

        try:
            return validate_and_fix_tickets(tickets)
        except EmptyFilesOwnedError as exc:
            if attempt >= max_regenerations:
                raise
            messages = messages + [
                {"role": "assistant", "content": str(raw_tickets)},
                {
                    "role": "user",
                    "content": (
                        f"Tickets {exc.ticket_ids} have empty files_owned. Every ticket "
                        "must own at least one file. Regenerate the full ticket list."
                    ),
                },
            ]

    raise AssertionError("unreachable")  # loop always returns or raises above
