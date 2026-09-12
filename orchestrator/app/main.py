"""The FastAPI orchestrator — CLAUDE.md's "### Orchestrator endpoints".

In-memory only (see rooms.py's docstring for why Mongo isn't wired here).
Real agent providers aren't wired either — every room runs on the
deterministic template providers unless IFM_API_KEY is set, in which case
K2's own calls (analysis, ticketing) go to the real IFM API; see
app/config.py.
"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import get_k2_ticketing_provider, get_settings
from .events import (
    ApprovalUpdated,
    ContextUpdated,
    ErrorEvent,
    ParticipantJoined,
    PlanApproved,
    TicketsCreated,
    UserMessage,
    to_sse_line,
)
from .k2.execution import execute_room
from .k2.negotiation import run_negotiation
from .k2.ticketing import decompose_plan_to_tickets
from .rooms import Room, RoomNotFoundError, RoomStore, default_store
from .schemas import Participant, Provider, Task
from .workspace import file_tree


class CreateRoomResponse(BaseModel):
    room_id: str


class ParticipantIn(BaseModel):
    user_id: str
    display_name: str
    provider: Provider
    model: str
    api_key: str | None = None


class ContextIn(BaseModel):
    project_context: str


class TasksIn(BaseModel):
    user_id: str
    tasks: list[Task]


class MessageIn(BaseModel):
    user_id: str
    content: str


class ApprovalIn(BaseModel):
    user_id: str
    approved: bool
    notes: str | None = None


def create_app(store: RoomStore | None = None) -> FastAPI:
    """Factory instead of a bare module-level app so tests can pass a fresh
    RoomStore and never share state with other tests or the dev server."""
    app = FastAPI(title="synesis orchestrator")
    app.state.store = store or default_store()

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_room(room_id: str) -> Room:
        try:
            return app.state.store.get(room_id)
        except RoomNotFoundError:
            raise HTTPException(status_code=404, detail=f"no such room: {room_id}") from None

    @app.post("/rooms", status_code=201)
    async def create_room() -> CreateRoomResponse:
        room_id = f"room_{secrets.token_hex(4)}"
        app.state.store.create(room_id)
        return CreateRoomResponse(room_id=room_id)

    @app.post("/rooms/{room_id}/participants", status_code=201)
    async def add_participant(room_id: str, body: ParticipantIn) -> dict:
        room = get_room(room_id)
        room.participants[body.user_id] = Participant(
            user_id=body.user_id,
            display_name=body.display_name,
            provider=body.provider,
            model=body.model,
            api_key=body.api_key,
        )
        await room.bus.publish(
            ParticipantJoined(
                room_id=room_id, user_id=body.user_id, provider=body.provider, model=body.model
            )
        )
        return {"ok": True}

    @app.post("/rooms/{room_id}/context")
    async def set_context(room_id: str, body: ContextIn) -> dict:
        room = get_room(room_id)
        room.context_version += 1
        room.project_context = body.project_context
        await room.bus.publish(
            ContextUpdated(
                room_id=room_id, version=room.context_version, content=body.project_context
            )
        )
        return {"version": room.context_version}

    @app.post("/rooms/{room_id}/tasks")
    async def set_tasks(room_id: str, body: TasksIn) -> dict:
        room = get_room(room_id)
        room.tasks_by_user[body.user_id] = body.tasks
        return {"ok": True}

    @app.post("/rooms/{room_id}/negotiate", status_code=202)
    async def negotiate(room_id: str) -> dict:
        room = get_room(room_id)
        if len(room.participants) != 2:
            raise HTTPException(status_code=409, detail="negotiation needs exactly 2 participants")
        if room.negotiate_started:
            raise HTTPException(status_code=409, detail="negotiation already started")
        room.negotiate_started = True

        async def _run() -> None:
            try:
                await run_negotiation(room)
            except Exception as exc:  # noqa: BLE001 - must reach the client as an event, not crash silently
                await room.bus.publish(
                    ErrorEvent(room_id=room_id, where="negotiate", detail=str(exc))
                )

        room.background_task = asyncio.create_task(_run())
        return {"ok": True}

    @app.post("/rooms/{room_id}/messages")
    async def post_message(room_id: str, body: MessageIn) -> dict:
        room = get_room(room_id)
        room.pending_messages.append(body.content)
        await room.bus.publish(
            UserMessage(
                room_id=room_id,
                round=max(room.current_round, 1),
                user_id=body.user_id,
                content=body.content,
            )
        )
        return {"ok": True}

    @app.post("/rooms/{room_id}/plan/approve")
    async def approve_plan(room_id: str, body: ApprovalIn) -> dict:
        room = get_room(room_id)
        if room.plan is None:
            raise HTTPException(status_code=409, detail="no plan proposed yet")

        room.plan.approvals[body.user_id] = body.approved
        await room.bus.publish(
            ApprovalUpdated(room_id=room_id, user_id=body.user_id, approved=body.approved)
        )

        if not (body.approved and room.plan.approvals and all(room.plan.approvals.values())):
            return {"ok": True}

        # Second approval: CLAUDE.md step 5 — update shared context, then decompose.
        room.plan.status = "approved"
        room.context_version += 1
        await room.bus.publish(PlanApproved(room_id=room_id, plan_id=room.plan.plan_id))
        await room.bus.publish(
            ContextUpdated(
                room_id=room_id, version=room.context_version, content=room.plan.summary
            )
        )

        provider, model = get_k2_ticketing_provider()
        try:
            tickets, _adjustments = await decompose_plan_to_tickets(
                room.plan, provider, model=model, step_owner=room.step_owner
            )
        except Exception as exc:  # noqa: BLE001
            await room.bus.publish(ErrorEvent(room_id=room_id, where="ticketing", detail=str(exc)))
            return {"ok": True}

        room.tickets = tickets
        await room.bus.publish(TicketsCreated(room_id=room_id, tickets=tickets))
        return {"ok": True}

    @app.post("/rooms/{room_id}/execute", status_code=202)
    async def execute(room_id: str) -> dict:
        room = get_room(room_id)
        if room.plan is None or room.plan.status != "approved":
            raise HTTPException(status_code=409, detail="plan is not approved")
        if not room.tickets:
            raise HTTPException(status_code=409, detail="no tickets to execute")
        if room.execute_started:
            raise HTTPException(status_code=409, detail="execution already started")
        room.execute_started = True

        async def _run() -> None:
            try:
                await execute_room(room)
            except Exception as exc:  # noqa: BLE001
                await room.bus.publish(ErrorEvent(room_id=room_id, where="execute", detail=str(exc)))

        room.background_task = asyncio.create_task(_run())
        return {"ok": True}

    @app.get("/rooms/{room_id}/stream")
    async def stream(room_id: str) -> StreamingResponse:
        room = get_room(room_id)
        backlog, queue = await room.bus.subscribe()

        async def gen():
            try:
                for event in backlog:
                    yield to_sse_line(event)
                while True:
                    event = await queue.get()
                    yield to_sse_line(event)
            finally:
                room.bus.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/rooms/{room_id}/files")
    async def list_files(room_id: str) -> dict:
        room = get_room(room_id)
        return {"tree": file_tree(list(room.files.keys()))}

    @app.get("/rooms/{room_id}/files/{path:path}")
    async def get_file(room_id: str, path: str) -> dict:
        room = get_room(room_id)
        f = room.files.get(path)
        if f is None:
            raise HTTPException(status_code=404, detail=f"no such file: {path}")
        return {"path": path, "content": f.content, "last_written_by": f.last_written_by}

    return app


app = create_app()
