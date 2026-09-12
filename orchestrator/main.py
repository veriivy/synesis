"""FastAPI app — frozen endpoints that exist so far.

    POST /rooms                   -> { room_id }
    POST /rooms/{id}/negotiate    -> 202, K2 loop on fixture PoAs
    GET  /rooms/{id}/stream       -> SSE

Opening PoAs come from /fixtures. K2 publishes an `analysis` (humans and agents
both see it). Each advocate then rewrites its own PoA from that comparison, in
parallel. K2 diffs the new plans. Default: 3 rounds (`MAX_ROUNDS` in .env).
a1 is Gemini, a2 is ChatGPT, moderator is K2 — missing/failed keys fall back to IFM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agents import revise_poa
from .events import EventBus, utc_now
from .k2 import analyze, blocking_ids, has_converged, load_fixture
from .providers import provider_status
from .rooms import Phase, Room, RoomStore

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
    room.task = asyncio.create_task(_run_loop(room))
    return {"ok": True}


def _publish_analysis(room_id: str, analysis: dict) -> dict:
    return {
        "type": "analysis",
        "round": analysis.get("round", 1),
        "similarities": analysis.get("similarities") or [],
        "differences": analysis.get("differences") or [],
        "converged": has_converged(analysis),
        "ts": utc_now(),
    }


async def _run_loop(room: Room) -> None:
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

        transcript_parts: list[str] = []
        analysis: dict = {}
        for round_index in range(1, MAX_ROUNDS + 1):
            analysis = await asyncio.to_thread(
                analyze,
                context=context,
                tasks=tasks,
                poa1=poa1,
                poa2=poa2,
                round_index=round_index,
                transcript="\n\n".join(transcript_parts),
            )
            await bus.publish(room.room_id, _publish_analysis(room.room_id, analysis))

            last_round = round_index >= MAX_ROUNDS
            if STOP_ON_CONVERGE and (has_converged(analysis) or not blocking_ids(analysis)):
                log.info("room %s converged at round %d", room.room_id, round_index)
                break
            if last_round:
                log.info("room %s finished %d rounds", room.room_id, MAX_ROUNDS)
                break

            replies = await asyncio.gather(
                asyncio.to_thread(
                    revise_poa, poa=poa1, tasks=tasks, analysis=analysis, round_index=round_index
                ),
                asyncio.to_thread(
                    revise_poa, poa=poa2, tasks=tasks, analysis=analysis, round_index=round_index
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

        room.phase = Phase.ANALYZED
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
