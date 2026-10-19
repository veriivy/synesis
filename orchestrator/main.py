"""FastAPI app — CLAUDE.md's "### Orchestrator endpoints".

    POST /rooms                   -> { room_id, admin_token }
    POST /rooms/{id}/goal         admin: the room's fixed north star (goal.py)
    POST /rooms/{id}/weights      admin: per-user influence (influence.py)
    POST /rooms/{id}/participants
    POST /rooms/{id}/context
    POST /rooms/{id}/tasks
    POST /rooms/{id}/negotiate    -> 202
    POST /rooms/{id}/messages
    POST /rooms/{id}/plan/approve
    POST /rooms/{id}/execute      -> 202
    GET  /rooms/{id}/stream       -> SSE
    GET  /rooms/{id}/files
    GET  /rooms/{id}/files/{path}

Negotiate: if the room has submitted tasks (POST /tasks for at least the
two joined participants), both agents draft their OPENING PoA live from
those tasks (agents.draft_poa). Otherwise — the original demo path, kept
so nothing that already worked stops working — it falls back to the fixture
PoAs. Either way: K2 publishes an `analysis` each round (humans and agents
both see it), each advocate rewrites its own PoA from that comparison in
parallel, up to MAX_ROUNDS. Once the loop ends, a FinalPlan is built from
whatever the two PoAs currently say and published as `plan_proposed`. Both
users approving decomposes it into tickets (ticketing.py + the disjoint-
ownership validator.py); POST /execute then runs them (execution.py).

a1 is Gemini, a2 is ChatGPT, moderator is K2 — missing/failed keys fall
back to IFM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import db
from .agents import draft_poa, revise_poa
from .events import EventBus, utc_now
from .execution import agent_implementer, execute_room
from .goal import Goal, anchor_strength, goal_block
from .influence import DEFAULT_WEIGHT, MAX_WEIGHT, stance_block, weigh_deadlock
from .interjections import (
    MAX_INTERJECTION_CHARS,
    Interjection,
    for_advocate,
    for_moderator,
    for_transcript,
)
from .k2 import analyze, blocking_ids, has_converged, load_fixture
from .providers import provider_key_for, provider_status
from .rooms import Phase, Room, RoomStore, room_snapshot
from .schemas import FinalPlan, Participant, Provider as ProviderName, Resolution, Step, Task, Ticket
from .ticketing import decompose_tickets
from .workspace import file_tree

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("synesis.orchestrator")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3") or "3")
STOP_ON_CONVERGE = os.getenv("STOP_ON_CONVERGE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}

bus = EventBus()
rooms = RoomStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(
        "orchestrator up — max_rounds=%s stop_on_converge=%s providers=%s",
        MAX_ROUNDS,
        STOP_ON_CONVERGE,
        provider_status(),
    )
    yield


app = FastAPI(title="Synesis orchestrator", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]
    or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_room(room_id: str) -> Room:
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"no such room: {room_id}")
    return room


def _check_owner(room: Room, user_id: str, token: str | None) -> None:
    """Security fix: POST /participants, /tasks, /messages, and
    /plan/approve used to trust a client-supplied user_id outright, so
    anyone who knew a room_id could re-register an existing participant
    (overwriting their provider/api_key — a real hijack once BYO keys are
    wired into real outbound calls), rewrite their task list, or post
    messages/approvals under their name.

    A token is issued the first time a user_id joins (add_participant) and
    must be presented on every subsequent call that acts as that user_id.
    Before a user_id has ever joined, there's nothing to protect yet, so
    calls for an as-yet-unclaimed user_id pass through unchecked — this
    keeps the fixture-fallback demo path (which never calls /participants
    at all) working exactly as before.
    """
    expected = room.participant_tokens.get(user_id)
    if expected is None:
        return
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(403, f"invalid or missing participant_token for {user_id!r}")


def _check_admin(room: Room, token: str | None) -> None:
    """The room's governance (goal, weights) is set by whoever created the
    room — they got admin_token back from POST /rooms. Without this, any
    participant could hand themselves a 10x weight: influence a user can
    assign to themselves is no constraint at all."""
    if not token or not secrets.compare_digest(token, room.admin_token):
        raise HTTPException(403, "invalid or missing admin_token for this room")


def _check_unlocked(room: Room, what: str) -> None:
    if room.governance_locked:
        raise HTTPException(409, f"{what} is fixed once negotiation has started")


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "rooms": len(rooms),
        "max_rounds": MAX_ROUNDS,
        "stop_on_converge": STOP_ON_CONVERGE,
        "providers": provider_status(),
    }


@app.post("/rooms")
async def create_room() -> dict:
    room = rooms.create()
    await db.save_room(room_snapshot(room))
    # admin_token: returned exactly once, here. Required by /goal and
    # /weights — the creator configures the room, participants don't.
    return {"room_id": room.room_id, "admin_token": room.admin_token}


class GoalIn(Goal):
    admin_token: str | None = None


@app.post("/rooms/{room_id}/goal")
async def set_goal(room_id: str, body: GoalIn) -> dict:
    """The room's north star (goal.py). Re-settable during setup so a typo
    isn't permanent; frozen the moment negotiation starts."""
    room = _get_room(room_id)
    _check_admin(room, body.admin_token)
    _check_unlocked(room, "the goal")
    room.goal = Goal(statement=body.statement, success_criteria=body.success_criteria)
    await bus.publish(room_id, {"type": "goal_set", "goal": room.goal.public(), "ts": utc_now()})
    return {"goal": room.goal.public()}


Weight = Annotated[float, Field(gt=0, le=MAX_WEIGHT)]


class WeightsIn(BaseModel):
    weights: dict[str, Weight]
    admin_token: str | None = None


@app.post("/rooms/{room_id}/weights")
async def set_weights(room_id: str, body: WeightsIn) -> dict:
    """Merge user_id -> weight into the room (influence.py). A user_id
    may be weighted before it joins. Frozen once negotiation starts."""
    room = _get_room(room_id)
    _check_admin(room, body.admin_token)
    _check_unlocked(room, "influence weighting")
    room.weights.update(body.weights)
    shares = room.user_shares()
    await bus.publish(
        room_id,
        {"type": "weights_updated", "weights": dict(room.weights), "shares": shares, "ts": utc_now()},
    )
    return {"weights": dict(room.weights), "shares": shares}


class ParticipantIn(BaseModel):
    user_id: str
    display_name: str
    provider: ProviderName
    model: str
    api_key: str | None = None
    # Required to re-register an already-claimed user_id (proves you're
    # the one who joined it, not someone guessing/sharing the room_id).
    # Omit on first join — the response hands back the token to use from
    # then on.
    participant_token: str | None = None


@app.post("/rooms/{room_id}/participants", status_code=201)
async def add_participant(room_id: str, body: ParticipantIn) -> dict:
    room = _get_room(room_id)
    _check_owner(room, body.user_id, body.participant_token)
    token = room.participant_tokens.setdefault(body.user_id, secrets.token_urlsafe(32))

    room.participants[body.user_id] = Participant(
        user_id=body.user_id,
        display_name=body.display_name,
        provider=body.provider,
        model=body.model,
        api_key=body.api_key,
    )
    await bus.publish(
        room_id,
        {
            "type": "participant_joined",
            "user_id": body.user_id,
            "provider": body.provider,
            "model": body.model,
            "weight": room.weights.get(body.user_id, DEFAULT_WEIGHT),
            "ts": utc_now(),
        },
    )
    return {"ok": True, "participant_token": token}


class ContextIn(BaseModel):
    project_context: str
    admin_token: str | None = None


@app.post("/rooms/{room_id}/context")
async def set_context(room_id: str, body: ContextIn) -> dict:
    """Room configuration, same as the goal: the shared context goes into
    every advocate's, K2's and every coder's prompt, so it used to be an
    open prompt-injection point for anyone holding a room_id — at any time,
    mid-execution included. Admin-only, fixed once negotiation starts."""
    room = _get_room(room_id)
    _check_admin(room, body.admin_token)
    _check_unlocked(room, "the shared context")
    room.context_version += 1
    room.project_context = body.project_context
    await bus.publish(
        room_id,
        {
            "type": "context_updated",
            "version": room.context_version,
            "content": body.project_context,
            "ts": utc_now(),
        },
    )
    return {"version": room.context_version}


class TasksIn(BaseModel):
    user_id: str
    tasks: list[Task]
    participant_token: str | None = None


@app.post("/rooms/{room_id}/tasks")
async def set_tasks(room_id: str, body: TasksIn) -> dict:
    room = _get_room(room_id)
    _check_owner(room, body.user_id, body.participant_token)
    room.tasks_by_user[body.user_id] = body.tasks
    return {"ok": True}


class MessageIn(BaseModel):
    user_id: str
    content: str = Field(max_length=MAX_INTERJECTION_CHARS)
    participant_token: str | None = None


@app.post("/rooms/{room_id}/messages")
async def post_message(room_id: str, body: MessageIn) -> dict:
    """Publishes to the chat, and queues the message for the next round's
    K2 call and both advocates (interjections.py) — but only from someone
    who joined the room. _check_owner lets an unclaimed user_id through
    (the token-free fixture demo path); that's tolerable for a chat line,
    not for steering two agents, so `queued` says which one happened."""
    room = _get_room(room_id)
    _check_owner(room, body.user_id, body.participant_token)
    queued = body.user_id in room.participant_tokens and bool(body.content.strip())
    if queued:
        room.pending_messages.append(
            Interjection(
                user_id=body.user_id,
                display_name=room.display_name(body.user_id),
                content=body.content,
            )
        )
    await bus.publish(
        room_id,
        {
            "type": "user_message",
            "round": max(room.current_round, 1),
            "user_id": body.user_id,
            "content": body.content,
            "ts": utc_now(),
        },
    )
    return {"ok": True, "queued": queued}


@app.get("/rooms/{room_id}/stream")
async def stream(room_id: str) -> StreamingResponse:
    _get_room(room_id)
    return StreamingResponse(
        bus.stream(room_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/rooms/{room_id}/negotiate", status_code=202)
async def negotiate(room_id: str) -> dict:
    room = _get_room(room_id)
    if room.task is not None and not room.task.done():
        raise HTTPException(409, "negotiation already running")
    # Re-negotiating after a REJECTED plan is the point (it resumes from the
    # latest PoAs with the rejection notes as input). Re-negotiating an
    # APPROVED one would swap room.plan out from under its tickets — and
    # write_file refuses everything once the plan isn't approved, so a
    # running execution would start failing mid-ticket.
    if room.plan is not None and room.plan.status == "approved":
        raise HTTPException(409, "the plan is already approved")
    if room.tasks_by_user and len(room.participants) != 2:
        raise HTTPException(409, "negotiation with live tasks needs exactly 2 participants")
    room.task = asyncio.create_task(_run_loop(room))
    return {"ok": True}


class ApprovalIn(BaseModel):
    user_id: str
    approved: bool
    notes: str | None = None
    participant_token: str | None = None


@app.post("/rooms/{room_id}/plan/approve")
async def approve_plan(room_id: str, body: ApprovalIn) -> dict:
    room = _get_room(room_id)
    if room.plan is None:
        raise HTTPException(409, "no plan proposed yet")
    if room.task is not None and not room.task.done():
        # A re-negotiation is about to replace this plan; a vote on the
        # outgoing one would decompose a plan nobody is arguing over anymore.
        raise HTTPException(409, "a negotiation is running; vote on the plan it proposes")
    _check_owner(room, body.user_id, body.participant_token)
    if body.user_id not in room.plan.approvals:
        # Without this, any user_id that never joined (so _check_owner lets
        # it through) could add itself as a voter, vote no, and block the
        # plan for good.
        raise HTTPException(403, f"{body.user_id!r} is not an approver on this plan")

    if room.plan.status == "approved":
        if room.tickets:
            # Idempotent, not a re-trigger: a second "yes" (a double click,
            # the demo peer's auto-approve racing a reconnect) used to
            # decompose again and replace room.tickets under a running
            # execution.
            raise HTTPException(409, "the plan is already approved")
        # Approved but ticketing failed last time: a fresh approve retries
        # the decomposition below without reopening the vote.
    else:
        room.plan.approvals[body.user_id] = body.approved
        await bus.publish(
            room_id,
            {
                "type": "approval_updated",
                "user_id": body.user_id,
                "approved": body.approved,
                "ts": utc_now(),
            },
        )
        if not body.approved and body.notes and body.notes.strip():
            await _queue_rejection(room, body.user_id, body.notes.strip()[:MAX_INTERJECTION_CHARS])

        if not (body.approved and room.plan.approvals and all(room.plan.approvals.values())):
            return {"ok": True}

        # Second approval: CLAUDE.md step 5 — update shared context, then decompose.
        room.plan.status = "approved"
        room.phase = Phase.AWAITING_APPROVAL
        room.context_version += 1
        await bus.publish(room_id, {"type": "plan_approved", "plan_id": room.plan.plan_id, "ts": utc_now()})
        await bus.publish(
            room_id,
            {
                "type": "context_updated",
                "version": room.context_version,
                "content": room.plan.summary,
                "ts": utc_now(),
            },
        )

    try:
        result = await asyncio.to_thread(
            decompose_tickets, room.plan, step_owner=room.step_owner
        )
    except Exception as exc:  # noqa: BLE001 — must reach the client as an event
        log.exception("ticketing failed for %s", room_id)
        await bus.publish(room_id, {"type": "error", "where": "ticketing", "detail": str(exc), "ts": utc_now()})
        return {"ok": True}

    room.tickets = result.tickets
    await bus.publish(
        room_id,
        {"type": "tickets_created", "tickets": [t.model_dump() for t in result.tickets], "ts": utc_now()},
    )
    await db.save_room(room_snapshot(room))
    return {"ok": True}


async def _queue_rejection(room: Room, user_id: str, notes: str) -> None:
    """A "no" with a reason used to drop the reason on the floor (`notes`
    was accepted and never read). Now it is published as that user's chat
    message and queued as an interjection, so the next POST /negotiate —
    which resumes from the latest PoAs — argues against the objection. Only
    for a verified participant, same rule as POST /messages."""
    if user_id not in room.participant_tokens:
        return
    room.pending_messages.append(
        Interjection(
            user_id=user_id,
            display_name=room.display_name(user_id),
            content=notes,
            source="rejection",
        )
    )
    await bus.publish(
        room.room_id,
        {
            "type": "user_message",
            "round": max(room.current_round, 1),
            "user_id": user_id,
            "content": f"Rejected the plan: {notes}",
            "ts": utc_now(),
        },
    )


@app.post("/rooms/{room_id}/execute", status_code=202)
async def execute(room_id: str) -> dict:
    room = _get_room(room_id)
    if room.plan is None or room.plan.status != "approved":
        raise HTTPException(409, "plan is not approved")
    if not room.tickets:
        raise HTTPException(409, "no tickets to execute")
    if room.execute_task is not None and not room.execute_task.done():
        raise HTTPException(409, "execution already running")

    room.phase = Phase.EXECUTING

    async def _run() -> None:
        try:
            # The coder for each ticket runs on the provider/key of the
            # participant who owns that agent — same BYO threading the
            # negotiation loop uses (_agent_provider_and_key).
            await execute_room(
                room,
                bus,
                implement=agent_implementer(lambda agent_id: _agent_provider_and_key(room, agent_id)),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("execute failed for %s", room_id)
            room.phase = Phase.FAILED
            await bus.publish(room_id, {"type": "error", "where": "execute", "detail": str(exc), "ts": utc_now()})
        finally:
            await db.save_room(room_snapshot(room))

    room.execute_task = asyncio.create_task(_run())
    return {"ok": True}


@app.get("/rooms/{room_id}/files")
async def list_files(room_id: str) -> dict:
    room = _get_room(room_id)
    return {"tree": file_tree(list(room.files.keys()))}


@app.get("/rooms/{room_id}/files/{path:path}")
async def get_file(room_id: str, path: str) -> dict:
    room = _get_room(room_id)
    f = room.files.get(path)
    if f is None:
        raise HTTPException(404, f"no such file: {path}")
    return {"path": path, "content": f.content, "last_written_by": f.last_written_by}


def _publish_analysis(analysis: dict, goal_anchor: float | None = None) -> dict:
    return {
        "type": "analysis",
        "round": analysis.get("round", 1),
        "similarities": analysis.get("similarities") or [],
        "differences": analysis.get("differences") or [],
        "converged": has_converged(analysis),
        # How hard this round leaned on the room's goal (goal.anchor_strength);
        # null when the room has none. Lets a client show the goal fading.
        "goal_anchor": goal_anchor,
        "ts": utc_now(),
    }


def _goal_for(room: Room, strength: float) -> str:
    return goal_block(room.goal, strength)


def _stance_for(room: Room, agent_id: str) -> str:
    """The advocate's influence section, or "" if the room never assigned
    weights — an unweighted room prompts exactly as it did before."""
    if not room.weights:
        return ""
    agent_shares = room.agent_shares()
    user_id = room.agent_users.get(agent_id, "")
    return stance_block(room.display_name(user_id), agent_shares.get(agent_id, 0.0), len(agent_shares))


def _agent_provider_and_key(room: Room, agent_id: str) -> tuple[str | None, str | None]:
    """BYO threading (CLAUDE.md: "Keys: BYO with server fallback"). If the
    participant who owns `agent_id` (Room.agent_users) joined with a
    provider choice, use it — and their pasted api_key if they gave one —
    instead of the static AGENT_A1_PROVIDER/AGENT_A2_PROVIDER env default.
    Returns (None, None) when there's no such participant, so callers fall
    through to providers.agent_provider(agent_id) unchanged.
    """
    user_id = room.agent_users.get(agent_id)
    participant = room.participants.get(user_id) if user_id else None
    if participant is None:
        return None, None
    return provider_key_for(participant.provider), participant.api_key


def _project_brief(room: Room, context: dict) -> str:
    """What the advocates are shown about the project: the shared context
    text plus the files that exist right now. They are told to name real
    file paths; until this they had never been shown any."""
    text = str(context.get("project_context") or context.get("content") or "").strip()
    files = "\n".join(f"- {p}" for p in sorted(room.files))
    parts = [text] if text else []
    if files:
        parts.append(f"Files in the repository now:\n{files}")
    return "\n\n".join(parts)


async def _draft_opening_poas(room: Room, project_brief: str) -> tuple[dict, dict]:
    """Live path: both agents draft from the room's own submitted tasks.
    Agent id assignment is by join order — the first participant is a1,
    the second a2. Which real provider serves each seat is resolved by
    _agent_provider_and_key (the participant's own BYO choice) with
    providers.agent_provider's static env default as the fallback.

    In parallel, each in a worker thread (CLAUDE.md step 2). This used to
    call draft_poa twice, in sequence, directly on the event loop — two
    blocking model calls during which every SSE stream and every request
    to the server stalled."""
    user_ids = list(room.participants.keys())
    room.agent_users = {"a1": user_ids[0], "a2": user_ids[1]}
    tasks = room.tasks_payload()
    goal_text = _goal_for(room, anchor_strength("draft"))

    def draft(agent_id: str):
        provider, key = _agent_provider_and_key(room, agent_id)
        return asyncio.to_thread(
            draft_poa,
            agent_id=agent_id,
            user_id=room.agent_users[agent_id],
            tasks=tasks,
            provider=provider,
            api_key=key,
            goal_block=goal_text,
            stance_block=_stance_for(room, agent_id),
            project_context=project_brief,
        )

    poa1, poa2 = await asyncio.gather(draft("a1"), draft("a2"))
    return poa1, poa2


def _files_of(poa: dict) -> set[str]:
    return {str(p) for step in poa.get("steps") or [] for p in step.get("files_touched") or []}


def _build_final_plan(room: Room, poa1: dict, poa2: dict, analysis: dict, rounds_used: int) -> FinalPlan:
    # A blocking issue still open at the round cap goes through the
    # influence tiebreak (influence.weigh_deadlock): weight share plus a
    # bonus for the position K2 judged closer to the goal. A clear leader
    # becomes the default; a close call stays with the humans. Either way
    # it is only a proposal — both users still have to approve the plan.
    agent_shares = room.agent_shares() or {"a1": 0.5, "a2": 0.5}
    touched = {"a1": _files_of(poa1), "a2": _files_of(poa2)}
    # agent_id -> paths taken out of its steps because a default went the
    # other way. The merged plan contains BOTH sides' steps; without this
    # the loser's step stays in, ticketing serializes it after the
    # winner's, and the loser's coder writes the contested file last.
    stripped: dict[str, set[str]] = {"a1": set(), "a2": set()}
    resolutions: list[Resolution] = []
    defaulted = 0
    for diff in (analysis or {}).get("differences") or []:
        if str(diff.get("severity", "")).lower() != "blocking":
            continue
        positions = diff.get("positions") or {}
        verdict = weigh_deadlock(diff, agent_shares)
        if verdict.winner is not None:
            defaulted += 1
            losers = [a for a in verdict.scores if a != verdict.winner]
            # Only paths K2 tied to this issue AND the winner actually
            # writes — never strip a file nobody would then implement.
            contested = set(diff.get("files") or []) & touched.get(verdict.winner, set())
            handed_over = []
            for loser in losers:
                taken = contested & touched.get(loser, set())
                stripped.setdefault(loser, set()).update(taken)
                if taken:
                    handed_over.append(f"{loser} no longer writes {', '.join(sorted(taken))}")
            outcome = (
                f"Defaults to {verdict.winner}'s position after {rounds_used} round(s): "
                f"{positions.get(verdict.winner, '(position not stated)')}"
            )
            names = [room.display_name(room.agent_users.get(a, a)) for a in losers]
            rationale = (
                f"Influence tiebreak — {verdict.describe(agent_shares)}. "
                + (f"In the plan: {'; '.join(handed_over)}. " if handed_over else "")
                + f"A default, not a mandate: {', '.join(names) or 'the other side'} can "
                "still reject the plan."
            )
        else:
            outcome = (
                f"Unresolved after {rounds_used} round(s) — both sides held. "
                "Needs an explicit user decision."
            )
            rationale = (
                f"Final positions: {positions}. "
                f"Influence tiebreak too close to call ({verdict.describe(agent_shares)})."
            )
        resolutions.append(
            Resolution(issue_id=str(diff.get("issue_id") or ""), outcome=outcome, rationale=rationale)
        )

    steps: list[Step] = []
    step_owner: dict[str, str] = {}
    for owner, poa in (("a1", poa1), ("a2", poa2)):
        for raw_step in poa.get("steps") or []:
            original = [str(p) for p in raw_step.get("files_touched") or []]
            files = [p for p in original if p not in stripped.get(owner, set())]
            if original and not files:
                continue  # every file it touched went to the other side
            new_id = f"s{len(steps) + 1}"
            step_owner[new_id] = owner
            steps.append(
                Step(
                    step_id=new_id,
                    title=str(raw_step.get("title") or ""),
                    description=str(raw_step.get("description") or ""),
                    files_touched=files,
                    rationale=str(raw_step.get("rationale") or ""),
                )
            )

    u1, u2 = poa1.get("user_id") or "u1", poa2.get("user_id") or "u2"
    undecided = len(resolutions) - defaulted
    plan = FinalPlan(
        plan_id=f"plan_{room.room_id}",
        rounds_used=rounds_used,
        summary=(
            f"Merged plan from a1 ({u1}) and a2 ({u2}) after {rounds_used} round(s); "
            f"{undecided} blocking issue(s) still need a decision"
            + (f", {defaulted} defaulted by the influence tiebreak." if defaulted else ".")
        ),
        steps=steps,
        resolutions=resolutions,
        approvals={u1: False, u2: False},
        status="proposed",
    )
    room.step_owner = step_owner
    return plan


async def _publish_poas(room: Room, *poas: dict) -> None:
    for poa in poas:
        await bus.publish(
            room.room_id,
            {
                "type": "poa_generated",
                "agent_id": poa.get("agent_id"),
                "user_id": poa.get("user_id"),
                "poa": poa,
                "ts": utc_now(),
            },
        )


async def _run_loop(room: Room) -> None:
    room.phase = Phase.NEGOTIATING
    try:
        if room.tasks_by_user:
            context = {"room_id": room.room_id, "project_context": room.project_context}
            tasks = room.tasks_payload()
        else:
            # Original demo path: no tasks submitted, negotiate on the
            # fixture PoAs exactly as before.
            context = load_fixture("context.json")
            tasks = load_fixture("tasks.json")
        brief = _project_brief(room, context)

        if "a1" in room.poas and "a2" in room.poas:
            # A re-negotiation (the last plan was rejected): pick the
            # argument up where it stopped. Redrafting would throw away
            # every concession already made and relitigate them all.
            poa1, poa2 = room.poas["a1"], room.poas["a2"]
            queued = len(room.pending_messages)
            await bus.publish(
                room.room_id,
                {
                    "type": "moderator_message",
                    "round": room.current_round + 1,
                    "content": (
                        "Re-negotiating from the latest plans"
                        + (f", with {queued} human interjection(s) on the table." if queued else ".")
                    ),
                    "ts": utc_now(),
                },
            )
        else:
            if room.tasks_by_user:
                poa1, poa2 = await _draft_opening_poas(room, brief)
            else:
                poa1 = load_fixture("PoA1.json")
                poa2 = load_fixture("PoA2.json")
                room.agent_users = {
                    str(poa1.get("agent_id")): str(poa1.get("user_id")),
                    str(poa2.get("agent_id")): str(poa2.get("user_id")),
                }
            room.poas = {"a1": poa1, "a2": poa2}
            await _publish_poas(room, poa1, poa2)

        transcript_parts: list[str] = []
        analysis: dict = {}
        # Round numbers keep counting across a re-negotiation, so the chat
        # never shows two different "round 1"s — and the goal's anchor, which
        # clamps at the last round, stays at tiebreaker strength from then on.
        first_round = room.current_round + 1
        last_round = room.current_round + MAX_ROUNDS
        round_index = room.current_round
        unseen_by_advocates: list[Interjection] = []
        for round_index in range(first_round, last_round + 1):
            room.current_round = round_index
            # Step 3a: everything the humans said since the last drain is
            # input to THIS round — K2's diff and both advocates' revisions.
            batch = room.drain_interjections()
            # The goal's pull weakens round over round (goal.anchor_strength):
            # the agents lean on it hardest while plans are still abstract.
            anchor = anchor_strength("negotiate", round_index, MAX_ROUNDS)
            goal_text = _goal_for(room, anchor)
            analysis = await asyncio.to_thread(
                analyze,
                context=context,
                tasks=tasks,
                poa1=poa1,
                poa2=poa2,
                round_index=round_index,
                transcript="\n\n".join(transcript_parts),
                goal_block=goal_text,
                interjections=for_moderator(batch),
            )
            await bus.publish(
                room.room_id, _publish_analysis(analysis, anchor if room.goal else None)
            )

            stop = STOP_ON_CONVERGE and (has_converged(analysis) or not blocking_ids(analysis))
            if stop or round_index >= last_round:
                log.info("room %s stopped at round %d", room.room_id, round_index)
                # No advocate turn follows, so they never saw this batch.
                unseen_by_advocates = batch
                break

            shares = room.user_shares() if room.weights else None
            if batch:
                transcript_parts.append(for_transcript(batch, round_index))

            def revise(agent_id: str, poa: dict):
                provider, key = _agent_provider_and_key(room, agent_id)
                return asyncio.to_thread(
                    revise_poa,
                    poa=poa,
                    tasks=tasks,
                    analysis=analysis,
                    round_index=round_index,
                    provider=provider,
                    api_key=key,
                    goal_block=goal_text,
                    stance_block=_stance_for(room, agent_id),
                    interjections=for_advocate(
                        batch, my_user_id=room.agent_users.get(agent_id, ""), user_shares=shares
                    ),
                    project_context=brief,
                )

            replies = await asyncio.gather(revise("a1", poa1), revise("a2", poa2))
            by_id = {reply["agent_id"]: reply for reply in replies}
            if "a1" in by_id:
                poa1 = by_id["a1"]["poa"]
            if "a2" in by_id:
                poa2 = by_id["a2"]["poa"]
            room.poas = {"a1": poa1, "a2": poa2}

            for reply in replies:
                await bus.publish(
                    room.room_id,
                    {
                        "type": "agent_message",
                        "round": round_index,
                        "agent_id": reply["agent_id"],
                        "user_id": reply["user_id"],
                        "content": reply["content"],
                        "addresses_issues": reply["addresses_issues"],
                        "ts": utc_now(),
                    },
                )
                await _publish_poas(room, reply["poa"])
                transcript_parts.append(
                    f"--- round {round_index} · {reply['agent_id']} ---\n"
                    f"{reply['content']}\n\nRevised PoA:\n"
                    + json.dumps(reply["poa"], indent=2)
                )
            await bus.publish(
                room.room_id,
                {"type": "round_complete", "round": round_index, "ts": utc_now()},
            )

        # K2 read the final round's interjections, but no advocate turn came
        # after: put them back at the front of the queue so a re-negotiation
        # (if this plan is rejected) hands them to the advocates first.
        if unseen_by_advocates:
            room.pending_messages[:0] = unseen_by_advocates

        room.phase = Phase.ANALYZED
        plan = _build_final_plan(room, poa1, poa2, analysis, rounds_used=round_index)
        room.plan = plan
        room.phase = Phase.AWAITING_APPROVAL
        await bus.publish(room.room_id, {"type": "plan_proposed", "plan": plan.model_dump(), "ts": utc_now()})
        await db.save_room(room_snapshot(room))
    except Exception as exc:  # noqa: BLE001 — a crashed room must still report
        log.exception("negotiate failed for %s", room.room_id)
        room.phase = Phase.FAILED
        await bus.publish(
            room.room_id,
            {
                "type": "error",
                "where": "k2.loop",
                "detail": str(exc),
                "ts": utc_now(),
            },
        )
