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

K2_TICKETING_SYSTEM = """You decompose an approved plan into tickets for agents a1 and a2.
Reply with ONE JSON object. First character is {. No markdown. No English.
{"tickets":[{"ticket_id":"t1","title":"...","description":"...","assigned_agent":"a1","files_owned":["src/a.py"],"lane":"parallel","depends_on":[]}]}
Rules: every ticket owns >=1 file; assigned_agent is a1 or a2; prefer parallel; sequential + depends_on only if the same file must wait. At most 8 tickets. Short strings."""


@dataclass
class TicketingResult:
    tickets: list[Ticket]
    adjustments: list[Adjustment]


def _build_user_prompt(plan: FinalPlan, step_owner: dict[str, str] | None) -> str:
    steps = []
    for step in plan.steps[:12]:
        owner = (step_owner or {}).get(step.step_id) or "a1"
        steps.append(
            {
                "id": step.step_id,
                "title": (step.title or "")[:80],
                "files": [str(p) for p in (step.files_touched or [])[:8]],
                "agent": owner if owner in ("a1", "a2") else "a1",
            }
        )
    payload = {"plan_id": plan.plan_id, "steps": steps}
    return (
        json.dumps(payload, separators=(",", ":"))
        + '\nReturn {"tickets":[...]} only. Start with {.'
    )


def tickets_from_plan(plan: FinalPlan, step_owner: dict[str, str] | None) -> list[Ticket]:
    """Deterministic tickets when K2 returns prose instead of JSON.

    One ticket per plan step, files from files_touched, owner from the
    agent that proposed the step. The validator still demotes overlaps.
    """
    tickets: list[Ticket] = []
    for i, step in enumerate(plan.steps, start=1):
        files = [str(p) for p in step.files_touched if p] or [f"src/{step.step_id}.py"]
        owner = (step_owner or {}).get(step.step_id)
        if owner not in ("a1", "a2"):
            owner = "a1" if i % 2 else "a2"
        tickets.append(
            Ticket(
                ticket_id=f"t{i}",
                plan_id=plan.plan_id,
                title=step.title or step.step_id,
                description=step.description or "",
                assigned_agent=owner,
                files_owned=files,
                depends_on=[],
                lane="parallel",
                status="pending",
            )
        )
    if not tickets:
        tickets.append(
            Ticket(
                ticket_id="t1",
                plan_id=plan.plan_id,
                title=(plan.summary or "execute plan")[:80],
                description=plan.summary or "",
                assigned_agent="a1",
                files_owned=["src/app.py"],
                depends_on=[],
                lane="parallel",
                status="pending",
            )
        )
    return tickets


def _to_ticket(raw: dict, plan_id: str) -> Ticket | None:
    files = raw.get("files_owned") or []
    if not isinstance(files, list):
        files = []
    files = [str(p) for p in files if p]
    agent = str(raw.get("assigned_agent") or "a1")
    if agent not in ("a1", "a2"):
        agent = "a1"
    ticket_id = str(raw.get("ticket_id") or "").strip() or "t0"
    lane = "sequential" if str(raw.get("lane") or "") == "sequential" else "parallel"
    depends = raw.get("depends_on") or []
    if not isinstance(depends, list):
        depends = []
    try:
        return Ticket(
            ticket_id=ticket_id,
            plan_id=plan_id,
            title=str(raw.get("title") or ticket_id),
            description=str(raw.get("description") or ""),
            assigned_agent=agent,
            files_owned=files,
            depends_on=[str(x) for x in depends],
            lane=lane,
            status="pending",
        )
    except Exception:  # noqa: BLE001 — skip a malformed ticket, keep the rest
        return None


def _call_and_parse(user: str, chat_fn) -> dict:
    """One JSON-repair retry, same pattern as k2.analyze: if K2's reply
    doesn't parse, ask once more for corrected JSON before giving up."""
    raw = chat_fn(system=K2_TICKETING_SYSTEM, user=user, provider=moderator_provider(), max_tokens=4096)
    try:
        return extract_object(raw)
    except ValueError:
        raw = chat_fn(
            system=K2_TICKETING_SYSTEM,
            user='{"tickets":[{"ticket_id":"t1","title":"x","description":"x","assigned_agent":"a1","files_owned":["src/a.py"],"lane":"parallel","depends_on":[]}]}',
            provider=moderator_provider(),
            max_tokens=2048,
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

    If K2 never returns JSON, fall back to one ticket per plan step so
    approval still produces tickets_created.

    `chat_fn` defaults to providers.chat and is overridable in tests so
    this never makes a real model call unless the caller wants it to.
    """
    user = _build_user_prompt(plan, step_owner)

    for attempt in range(max_regenerations + 1):
        try:
            data = _call_and_parse(user, chat_fn)
        except ValueError:
            # Already retried once inside _call_and_parse. Don't burn
            # another regeneration on English restatements — fall back.
            break
        raw_tickets = data.get("tickets") if isinstance(data, dict) else None
        if not isinstance(raw_tickets, list):
            raw_tickets = []
        tickets = [
            t
            for t in (_to_ticket(item, plan.plan_id) for item in raw_tickets if isinstance(item, dict))
            if t is not None
        ]

        if not tickets:
            if attempt >= max_regenerations:
                break
            continue

        try:
            fixed, adjustments = validate_and_fix_tickets(tickets)
            return TicketingResult(tickets=fixed, adjustments=adjustments)
        except EmptyFilesOwnedError as exc:
            if attempt >= max_regenerations:
                break
            user = (
                user
                + f"\n\nYour last reply (tickets {exc.ticket_ids}) left files_owned "
                "empty on some tickets. Every ticket must own at least one file. "
                "Regenerate the full ticket list as JSON:\n"
                + json.dumps({"tickets": raw_tickets})
            )

    fallback = tickets_from_plan(plan, step_owner)
    fixed, adjustments = validate_and_fix_tickets(fallback)
    return TicketingResult(tickets=fixed, adjustments=adjustments)
