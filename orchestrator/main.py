"""FastAPI app — CLAUDE.md's "### Orchestrator endpoints".

    POST /rooms
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

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import db
from .agents import draft_poa, revise_poa
from .events import EventBus, utc_now
from .execution import agent_implementer, execute_room
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
    return {"room_id": room.room_id}


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
            "ts": utc_now(),
        },
    )
    return {"ok": True, "participant_token": token}


class ContextIn(BaseModel):
    project_context: str


@app.post("/rooms/{room_id}/context")
async def set_context(room_id: str, body: ContextIn) -> dict:
    room = _get_room(room_id)
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
    content: str
    participant_token: str | None = None


@app.post("/rooms/{room_id}/messages")
async def post_message(room_id: str, body: MessageIn) -> dict:
    room = _get_room(room_id)
    _check_owner(room, body.user_id, body.participant_token)
    room.pending_messages.append(body.content)
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
    return {"ok": True}


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
    _check_owner(room, body.user_id, body.participant_token)

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


def _publish_analysis(analysis: dict) -> dict:
    return {
        "type": "analysis",
        "round": analysis.get("round", 1),
        "similarities": analysis.get("similarities") or [],
        "differences": analysis.get("differences") or [],
        "converged": has_converged(analysis),
        "ts": utc_now(),
    }


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


def _draft_opening_poas(room: Room) -> tuple[dict, dict, dict]:
    """Live path: both agents draft from the room's own submitted tasks.
    Agent id assignment is by join order — the first participant is a1,
    the second a2. Which real provider serves each seat is resolved by
    _agent_provider_and_key (the participant's own BYO choice) with
    providers.agent_provider's static env default as the fallback."""
    user_ids = list(room.participants.keys())
    a1_user, a2_user = user_ids[0], user_ids[1]
    room.agent_users = {"a1": a1_user, "a2": a2_user}
    tasks = room.tasks_payload()
    provider1, key1 = _agent_provider_and_key(room, "a1")
    provider2, key2 = _agent_provider_and_key(room, "a2")
    poa1 = draft_poa(agent_id="a1", user_id=a1_user, tasks=tasks, provider=provider1, api_key=key1)
    poa2 = draft_poa(agent_id="a2", user_id=a2_user, tasks=tasks, provider=provider2, api_key=key2)
    context = {"room_id": room.room_id, "project_context": room.project_context}
    return context, poa1, poa2


def _build_final_plan(room: Room, poa1: dict, poa2: dict, analysis: dict, rounds_used: int) -> FinalPlan:
    steps: list[Step] = []
    step_owner: dict[str, str] = {}
    for owner, poa in (("a1", poa1), ("a2", poa2)):
        for i, raw_step in enumerate(poa.get("steps") or [], start=len(steps) + 1):
            new_id = f"s{i}"
            step_owner[new_id] = owner
            steps.append(
                Step(
                    step_id=new_id,
                    title=str(raw_step.get("title") or ""),
                    description=str(raw_step.get("description") or ""),
                    files_touched=[str(p) for p in raw_step.get("files_touched") or []],
                    rationale=str(raw_step.get("rationale") or ""),
                )
            )

    resolutions: list[Resolution] = []
    for diff in (analysis or {}).get("differences") or []:
        if str(diff.get("severity", "")).lower() != "blocking":
            continue
        resolutions.append(
            Resolution(
                issue_id=str(diff.get("issue_id") or ""),
                outcome=(
                    f"Unresolved after {rounds_used} round(s) — both sides held. "
                    "Needs an explicit user decision."
                ),
                rationale=f"Final positions: {diff.get('positions')}",
            )
        )

    u1, u2 = poa1.get("user_id") or "u1", poa2.get("user_id") or "u2"
    plan = FinalPlan(
        plan_id=f"plan_{room.room_id}",
        rounds_used=rounds_used,
        summary=(
            f"Merged plan from a1 ({u1}) and a2 ({u2}) after {rounds_used} round(s); "
            f"{len(resolutions)} blocking issue(s) still need a decision."
        ),
        steps=steps,
        resolutions=resolutions,
        approvals={u1: False, u2: False},
        status="proposed",
    )
    room.step_owner = step_owner
    return plan


async def _run_loop(room: Room) -> None:
    room.phase = Phase.NEGOTIATING
    try:
        if room.tasks_by_user:
            context, poa1, poa2 = _draft_opening_poas(room)
            tasks = room.tasks_payload()
        else:
            # Original demo path: no tasks submitted, negotiate on the
            # fixture PoAs exactly as before.
            poa1 = load_fixture("PoA1.json")
            poa2 = load_fixture("PoA2.json")
            context = load_fixture("context.json")
            tasks = load_fixture("tasks.json")
            room.agent_users = {
                str(poa1.get("agent_id")): str(poa1.get("user_id")),
                str(poa2.get("agent_id")): str(poa2.get("user_id")),
            }

        for poa in (poa1, poa2):
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

        transcript_parts: list[str] = []
        analysis: dict = {}
        round_index = 0
        for round_index in range(1, MAX_ROUNDS + 1):
            room.current_round = round_index
            analysis = await asyncio.to_thread(
                analyze,
                context=context,
                tasks=tasks,
                poa1=poa1,
                poa2=poa2,
                round_index=round_index,
                transcript="\n\n".join(transcript_parts),
            )
            await bus.publish(room.room_id, _publish_analysis(analysis))

            last_round = round_index >= MAX_ROUNDS
            if STOP_ON_CONVERGE and (has_converged(analysis) or not blocking_ids(analysis)):
                log.info("room %s converged at round %d", room.room_id, round_index)
                break
            if last_round:
                log.info("room %s finished %d rounds", room.room_id, MAX_ROUNDS)
                break

            provider1, key1 = _agent_provider_and_key(room, "a1")
            provider2, key2 = _agent_provider_and_key(room, "a2")
            replies = await asyncio.gather(
                asyncio.to_thread(
                    revise_poa,
                    poa=poa1,
                    tasks=tasks,
                    analysis=analysis,
                    round_index=round_index,
                    provider=provider1,
                    api_key=key1,
                ),
                asyncio.to_thread(
                    revise_poa,
                    poa=poa2,
                    tasks=tasks,
                    analysis=analysis,
                    round_index=round_index,
                    provider=provider2,
                    api_key=key2,
                ),
            )
            by_id = {reply["agent_id"]: reply for reply in replies}
            if "a1" in by_id:
                poa1 = by_id["a1"]["poa"]
            if "a2" in by_id:
                poa2 = by_id["a2"]["poa"]

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
                await bus.publish(
                    room.room_id,
                    {
                        "type": "poa_generated",
                        "agent_id": reply["agent_id"],
                        "user_id": reply["user_id"],
                        "poa": reply["poa"],
                        "ts": utc_now(),
                    },
                )
                transcript_parts.append(
                    f"--- round {round_index} · {reply['agent_id']} ---\n"
                    f"{reply['content']}\n\nRevised PoA:\n"
                    + json.dumps(reply["poa"], indent=2)
                )
            await bus.publish(
                room.room_id,
                {"type": "round_complete", "round": round_index, "ts": utc_now()},
            )
            # Step 3a: drain mid-loop interjections queued via POST
            # /messages — the endpoint already published them as they
            # arrived, this just stops them leaking into a later round.
            room.pending_messages.clear()

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
