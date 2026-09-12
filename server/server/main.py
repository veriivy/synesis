"""FastAPI app. The five frozen endpoints, plus health.

    POST /rooms                   -> { room_id }
    POST /rooms/{id}/intents      -> { ok }
    GET  /rooms/{id}/stream       -> SSE
    POST /rooms/{id}/plan/approve -> { ok }
    GET  /rooms/{id}/files        -> { files: [...] }

Endpoint paths and response shapes are THE FROZEN CONTRACT. Do not change them without
the team agreeing out loud.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from engine.schemas import Requirement, utc_now
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import git_ops
from .config import REPO_ROOT, ServerSettings
from .db import Store
from .events import EventBus
from .orchestrator import Orchestrator
from .rooms import Phase, RoomStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("synesis.server")

settings = ServerSettings.from_env()
bus = EventBus()
store = Store(settings.mongodb_uri, settings.mongodb_db)
rooms = RoomStore(settings.workspace_root)
orchestrator = Orchestrator(bus, store)


def replay_fixture_path() -> Path | None:
    if not settings.replay_fixture:
        return None
    path = Path(settings.replay_fixture)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    return path if path.is_file() else None


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.connect()
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        log.info("git: %s", git_ops.git_executable())
    except git_ops.GitError as exc:
        # Not fatal at boot — the UI and the replay demo work without git. Cloning and
        # committing will fail loudly later, which is better than refusing to start.
        log.error("%s", exc)
    if replay_fixture_path():
        log.warning("REPLAY MODE — serving %s instead of live agents", settings.replay_fixture)
    yield
    await rooms.shutdown()
    await store.close()


app = FastAPI(title="Synesis", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter()


# --- bodies ------------------------------------------------------------------


class CreateRoomBody(BaseModel):
    feature: str = ""
    repo_url: str = ""
    repo_ref: str = ""


class IntentBody(BaseModel):
    user_id: str
    agent_id: str
    requirements: list[Requirement] = Field(default_factory=list)
    display_name: str = ""
    provider: str | None = None


class ApproveBody(BaseModel):
    approved: bool
    notes: str | None = None


# --- endpoints ---------------------------------------------------------------


@api.get("/health")
async def health() -> dict:
    try:
        git_version = git_ops.git_executable()
    except git_ops.GitError as exc:
        git_version = f"MISSING: {exc}"
    return {
        "ok": True,
        "rooms": len(rooms.all()),
        "mongo": store.enabled,
        "git": git_version,
        "replay": bool(replay_fixture_path()),
    }


@api.post("/rooms")
async def create_room(body: CreateRoomBody) -> dict:
    room = rooms.create(feature=body.feature)
    url = body.repo_url or settings.target_repo_url
    ref = body.repo_ref or settings.target_repo_ref

    # Clone off the event loop — a cold clone takes seconds and would block SSE.
    try:
        if url:
            sha = await asyncio.to_thread(git_ops.clone, url, room.workspace.root, ref)
            room.repo.url, room.repo.commit = url, sha
        else:
            # No target configured: an empty local repo, so the rest of the flow works.
            sha = await asyncio.to_thread(git_ops.init_local, room.workspace.root)
            room.repo.url, room.repo.commit = "(local scratch)", sha
    except git_ops.GitError as exc:
        log.error("clone failed for %s: %s", room.room_id, exc)
        room.repo.url = url
        room.repo.commit = ""

    await store.upsert_room(room.room_id, room.summary())
    return {"room_id": room.room_id}


@api.get("/rooms/{room_id}")
async def get_room(room_id: str) -> dict:
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"no such room: {room_id}")
    return room.summary()


@api.post("/rooms/{room_id}/intents")
async def post_intents(room_id: str, body: IntentBody) -> dict:
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"no such room: {room_id}")
    if room.phase not in (Phase.WAITING,):
        raise HTTPException(409, f"room is {room.phase.value}; intents are closed")

    try:
        room.add_intent(
            user_id=body.user_id,
            agent_id=body.agent_id,
            requirements=body.requirements,
            provider=body.provider,
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    await bus.publish(
        room_id,
        {
            "type": "intent_registered",
            "room_id": room_id,
            "user_id": body.user_id,
            "agent_id": body.agent_id,
            "requirement_count": len(body.requirements),
            "ts": utc_now(),
        },
    )

    # The last intent starts the negotiation. No separate "go" button — the demo has
    # enough buttons in it already.
    if room.ready_to_negotiate and (room.task is None or room.task.done()):
        fixture = replay_fixture_path()
        coro = (
            orchestrator.replay(room, fixture)
            if fixture
            else orchestrator.negotiate(room)
        )
        room.task = asyncio.create_task(coro)

    await store.upsert_room(room_id, room.summary())
    return {"ok": True}


@api.get("/rooms/{room_id}/stream")
async def stream(room_id: str) -> StreamingResponse:
    if rooms.get(room_id) is None:
        raise HTTPException(404, f"no such room: {room_id}")
    return StreamingResponse(
        bus.stream(room_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx on Vultr buffers SSE without this
        },
    )


@api.post("/rooms/{room_id}/plan/approve")
async def approve_plan(room_id: str, body: ApproveBody) -> dict:
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"no such room: {room_id}")
    if room.plan is None:
        raise HTTPException(409, "no plan has been proposed yet")

    if not body.approved:
        room.plan.status = "rejected"
        room.phase = Phase.REJECTED
        if room.workspace:
            room.workspace.plan_approved = False
        await bus.publish(
            room_id,
            {
                "type": "plan_rejected",
                "room_id": room_id,
                "notes": body.notes,
                "ts": utc_now(),
            },
        )
        return {"ok": True}

    # The gate. Until this line runs, every write in the room is refused.
    room.plan.status = "approved"
    if room.workspace:
        room.workspace.plan = room.plan
        room.workspace.plan_approved = True

    await store.save_plan(room_id, room.plan.model_dump())

    fixture = replay_fixture_path()
    coro = (
        orchestrator.replay_after_approval(room, fixture)
        if fixture
        else orchestrator.execute(room)
    )
    room.task = asyncio.create_task(coro)
    return {"ok": True}


@api.get("/rooms/{room_id}/files")
async def get_files(room_id: str) -> dict:
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"no such room: {room_id}")
    if room.plan is None or room.workspace is None:
        return {"files": []}

    files = []
    for task in room.plan.tasks:
        for path in task.files_owned:
            full = room.workspace.root / path
            if not full.is_file():
                continue
            files.append(
                {
                    "path": path,
                    "agent_id": task.owner_agent,
                    "diff": await asyncio.to_thread(
                        git_ops.diff_for, room.workspace.root, path
                    ),
                }
            )
    return {"files": files}


app.include_router(api)
