"""SSE bus. History is replayed on connect so a refresh does not blank the UI.

Every frame carries an SSE `id:` — the event's index in its room's history.
When a connection drops, EventSource reconnects on its own and sends the
last id it saw as Last-Event-ID; the replay then resumes after it. It used
to restart from the top, so every event reached the client's reducer twice
(duplicated chat, re-fired auto-approve). A fresh EventSource sends no id
and gets the whole history, exactly as before.

While nothing is published — one K2 call can take 20s+ — a comment frame
goes out every HEARTBEAT_SECONDS so proxies don't cut an idle stream.
EventSource ignores comment frames.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict

from . import db

HEARTBEAT_SECONDS = float(os.getenv("SSE_HEARTBEAT_SECONDS", "15") or "15")
HEARTBEAT_FRAME = ": keep-alive\n\n"


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_sse(event: dict, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"


def parse_last_event_id(raw: str | None) -> int | None:
    """Last-Event-ID is client input: anything but a non-negative int means
    "no id", i.e. a full replay."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


class EventBus:
    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = defaultdict(list)
        # One wake-up queue per open stream. What it carries doesn't matter:
        # a stream re-reads history from its own cursor on every wake, so a
        # missed or extra wake-up can neither drop nor duplicate an event.
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    async def publish(self, room_id: str, event: dict) -> None:
        event.setdefault("room_id", room_id)
        event.setdefault("ts", utc_now())
        self._history[room_id].append(event)
        for queue in list(self._subs[room_id]):
            queue.put_nowait(None)
        # Durable transcript. Fire-and-forget: a slow or misconfigured
        # Mongo must never add latency to event delivery, let alone block
        # it — db.record_event already swallows its own failures.
        asyncio.create_task(db.record_event(event))

    def seed(self, room_id: str, events: list[dict]) -> None:
        """Install a finished room's log as its history (archive.py). Not
        published: nobody is subscribed yet, and the events were recorded
        when they first happened."""
        self._history[room_id] = list(events)

    def history(self, room_id: str) -> list[dict]:
        """The event log so far, in order. Used by tests that assert on
        what a room published without opening a real SSE connection."""
        return list(self._history[room_id])

    async def stream(self, room_id: str, last_event_id: int | None = None):
        """Every event after `last_event_id` (all of them when None), then
        live events as they're published, with heartbeats in between.

        The cursor is also why an event published mid-replay is sent once:
        the old replay iterated the growing history list AND received the
        same event on its queue."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subs[room_id].append(queue)
        history = self._history[room_id]
        cursor = 0 if last_event_id is None else last_event_id + 1
        if cursor > len(history):
            # An id this room never issued (a client carrying one across a
            # restart): resending beats silently withholding events.
            cursor = 0
        try:
            while True:
                while cursor < len(history):
                    yield format_sse(history[cursor], cursor)
                    cursor += 1
                try:
                    await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield HEARTBEAT_FRAME
        finally:
            self._subs[room_id].remove(queue)
