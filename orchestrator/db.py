"""MongoDB Atlas persistence — CLAUDE.md: "Persistence: MongoDB Atlas —
rooms, transcripts, plans, tickets."

Deliberately partial, matching what's actually achievable safely in this
pass: every SSE event is durably recorded (the "transcript"), and a room
snapshot (participants, tasks, plan, tickets, files — never an api_key,
see rooms.room_snapshot) is saved at the milestones main.py already has a
natural hook for. What this does NOT do is let an in-progress negotiation
survive a process restart — the asyncio.Task driving a room's round loop
is gone the moment the process is, regardless of what's in the database.
Resuming that would mean checkpointing and replaying the loop itself, a
much bigger change than "wire up Mongo."

Optional by design, same pattern as every other external service here
(providers.chat's fallback chain): with MONGODB_URI unset, every function
in this module is a no-op — nothing here can turn a working demo into a
broken one just because a database isn't configured. Every call is also
wrapped so a Mongo failure (bad URI, network blip, Atlas hiccup) never
raises into the caller — same reasoning as providers.chat's own
try/except-and-fall-back, applied to "the write can't take down the room."
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("synesis.orchestrator.db")

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@lru_cache
def _client():
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        return None
    from motor.motor_asyncio import AsyncIOMotorClient

    return AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)


def get_db():
    """The configured database, or None if MONGODB_URI isn't set. Every
    other function in this module already checks this — most callers
    don't need to."""
    client = _client()
    if client is None:
        return None
    return client[os.getenv("MONGODB_DB_NAME", "synesis").strip() or "synesis"]


async def record_event(event: dict) -> None:
    """Append one SSE event to the durable transcript. Called from
    events.EventBus.publish for every event, every room — that's the one
    place all of them already flow through."""
    db = get_db()
    if db is None:
        return
    try:
        await db.events.insert_one(dict(event))
    except Exception:  # noqa: BLE001 — a persistence failure must not break the room
        log.warning("record_event failed for room %s", event.get("room_id"), exc_info=True)


async def save_room(snapshot: dict) -> None:
    """Upsert a room snapshot (see rooms.room_snapshot) by room_id. Called
    from main.py at each milestone: room created, plan_proposed,
    tickets_created, execution finished."""
    db = get_db()
    if db is None:
        return
    room_id = snapshot.get("room_id")
    if not room_id:
        return
    try:
        await db.rooms.replace_one({"room_id": room_id}, snapshot, upsert=True)
    except Exception:  # noqa: BLE001
        log.warning("save_room failed for room %s", room_id, exc_info=True)
