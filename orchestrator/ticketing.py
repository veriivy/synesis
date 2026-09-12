"""K2's plan -> tickets step (CLAUDE.md steps 5/6).

Same call shape as k2.analyze and agents.revise_poa: synchronous, blocking
— call via asyncio.to_thread from the event loop. The actual collision-
freedom guarantee lives in validator.py; this module is just the prompt +
retry-on-bad-ticket loop around it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .k2 import extract_object
from .providers import chat, moderator_provider
from .schemas import FinalPlan, Ticket
from .validator import Adjustment, EmptyFilesOwnedError, validate_and_fix_tickets

K2_TICKETING_SYSTEM = """You are K2, moderating a shared coding session between two AI \
agents. You are given an approved FinalPlan's steps. Decompose them into tickets, one \
or more per step, so the two agents can execute as much as possible in parallel.

Return ONLY a JSON object, no markdown:
{"tickets": [
  {
    "ticket_id": "t1",
    "title": "...",
    "description": "...",
    "assigned_agent": "a1",
    "files_owned": ["path/to/file.py"],
    "lane": "parallel",
    "depends_on": []
  }
]}

Every ticket MUST own at least one file — never return an empty files_owned. Prefer \
"parallel"; only mark a ticket "sequential" if it genuinely cannot start until another \
ticket finishes. Do not let two tickets claim the same file unless you intend one to \
depend on the other."""


@dataclass
class TicketingResult:
    tickets: list[Ticket]
    adjustments: list[Adjustment]


def _build_user_prompt(plan: FinalPlan, step_owner: dict[str, str] | None) -> str:
    lines = [f"Plan summary: {plan.summary}", "Steps:"]
    for step in plan.steps:
        files = ", ".join(step.files_touched) or "unspecified"
        owner = (step_owner or {}).get(step.step_id)
        owner_note = f", proposed by {owner}" if owner else ""
        lines.append(
            f"- {step.step_id}: {step.title} — {step.description} (files: {files}{owner_note})"
        )
    return "\n".join(lines)


def _to_ticket(raw: dict, plan_id: str) -> Ticket:
    raw = dict(raw)
    raw.setdefault("lane", "parallel")
    raw.setdefault("depends_on", [])
    raw["plan_id"] = plan_id
    raw["status"] = "pending"
    return Ticket(**raw)


def _call_and_parse(user: str, chat_fn) -> dict:
    """One JSON-repair retry, same pattern as k2.analyze: if K2's reply
    doesn't parse, ask once more for corrected JSON before giving up."""
    raw = chat_fn(system=K2_TICKETING_SYSTEM, user=user, provider=moderator_provider(), max_tokens=4096)
    try:
        return extract_object(raw)
    except ValueError:
        raw = chat_fn(
            system=K2_TICKETING_SYSTEM,
            user=user + "\n\nYour previous reply was not valid JSON. Reply with the JSON object only.",
            provider=moderator_provider(),
            max_tokens=4096,
        )
        return extract_object(raw)


def decompose_tickets(
    plan: FinalPlan,
    *,
    step_owner: dict[str, str] | None = None,
    max_regenerations: int = 1,
    chat_fn=chat,
) -> TicketingResult:
    """Ask K2 to break `plan` into tickets, then run the code-level
    disjointness validator. If K2 leaves a ticket with no files_owned,
    reject and ask it to regenerate (bounded by max_regenerations) instead
    of guessing ownership on its behalf.

    `chat_fn` defaults to providers.chat and is overridable in tests so
    this never makes a real model call unless the caller wants it to.
    """
    user = _build_user_prompt(plan, step_owner)

    for attempt in range(max_regenerations + 1):
        data = _call_and_parse(user, chat_fn)
        raw_tickets = data.get("tickets")
        if not isinstance(raw_tickets, list):
            raw_tickets = []
        tickets = [_to_ticket(t, plan.plan_id) for t in raw_tickets if isinstance(t, dict)]

        try:
            fixed, adjustments = validate_and_fix_tickets(tickets)
            return TicketingResult(tickets=fixed, adjustments=adjustments)
        except EmptyFilesOwnedError as exc:
            if attempt >= max_regenerations:
                raise
            user = (
                user
                + f"\n\nYour last reply (tickets {exc.ticket_ids}) left files_owned "
                "empty on some tickets. Every ticket must own at least one file. "
                "Regenerate the full ticket list as JSON:\n"
                + json.dumps({"tickets": raw_tickets})
            )

    raise AssertionError("unreachable")  # loop always returns or raises above
