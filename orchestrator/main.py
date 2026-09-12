"""FastAPI app — first slice of THE FROZEN CONTRACT.

    POST /rooms                   -> { room_id }
    POST /rooms/{id}/negotiate    -> 202, runs K2 on fixture PoAs
    GET  /rooms/{id}/stream       -> SSE

On negotiate we skip live PoA generation: both plans come from /fixtures, then the
same K2 call as k2mod.py. That is the 1am checkpoint — an `analysis` event on the
stream. The rest of the loop is not here yet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .events import EventBus, utc_now
from .k2 import analyze, load_fixture
from .rooms import Phase, Room, RoomStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("synesis.orchestrator")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

bus = EventBus()
rooms = RoomStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("orchestrator up — POST /rooms, then POST /rooms/{id}/negotiate")
    yield


app = FastAPI(title="Synesis orchestrator", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "rooms": len(rooms)}


@app.post("/rooms")
async def create_room() -> dict:
    room = rooms.create()
    return {"room_id": room.room_id}


@app.get("/rooms/{room_id}/stream")
async def stream(room_id: str) -> StreamingResponse:
    if rooms.get(room_id) is None:
        raise HTTPException(404, f"no such room: {room_id}")
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
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, f"no such room: {room_id}")
    if room.task is not None and not room.task.done():
        raise HTTPException(409, "negotiation already running")
    room.task = asyncio.create_task(_run_k2_slice(room))
    return {"ok": True}


async def _run_k2_slice(room: Room) -> None:
    room.phase = Phase.NEGOTIATING
    try:
        poa1 = load_fixture("PoA1.json")
        poa2 = load_fixture("PoA2.json")
        context = load_fixture("context.json")
        tasks = load_fixture("tasks.json")

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

        analysis = await asyncio.to_thread(
            analyze, context=context, tasks=tasks, poa1=poa1, poa2=poa2, round_index=1
        )
        await bus.publish(
            room.room_id,
            {
                "type": "analysis",
                "round": analysis.get("round", 1),
                "similarities": analysis.get("similarities") or [],
                "differences": analysis.get("differences") or [],
                "converged": bool(analysis.get("converged")),
                "ts": utc_now(),
            },
        )
        room.phase = Phase.ANALYZED
    except Exception as exc:  # noqa: BLE001 — a crashed room must still report
        log.exception("negotiate failed for %s", room.room_id)
        room.phase = Phase.FAILED
        await bus.publish(
            room.room_id,
            {
                "type": "error",
                "where": "k2.analysis",
                "detail": str(exc),
                "ts": utc_now(),
            },
        )
