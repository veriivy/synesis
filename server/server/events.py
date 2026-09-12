"""Per-room event bus and SSE framing.

Every event is kept in a per-room history as well as fanned out live, so a browser that
connects late — or reconnects mid-demo — replays everything it missed instead of
showing an empty screen. On stage, a refresh that wipes the transcript is fatal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger(__name__)

QUEUE_MAXSIZE = 512
HEARTBEAT_SECONDS = 15.0


def sse_frame(event: dict[str, Any], event_id: int | None = None) -> str:
    """One SSE frame. `event:` carries the type so the client can addEventListener."""
    parts = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event.get('type', 'message')}")
    parts.append(f"data: {json.dumps(event, separators=(',', ':'))}")
    return "\n".join(parts) + "\n\n"


class EventBus:
    """Fan-out with replay. One instance per process; rooms are keyed inside it."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._history: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    def history(self, room_id: str) -> list[dict]:
        return list(self._history.get(room_id, []))

    async def publish(self, room_id: str, event: dict) -> None:
        async with self._lock:
            self._history.setdefault(room_id, []).append(event)
            subscribers = list(self._subscribers.get(room_id, ()))

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A browser tab that stopped reading must not stall the negotiation.
                log.warning("dropping event for a slow subscriber in room %s", room_id)

    async def subscribe(self, room_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.setdefault(room_id, set()).add(queue)
        return queue

    async def unsubscribe(self, room_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(room_id)
            if subscribers:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(room_id, None)

    async def stream(self, room_id: str, *, replay: bool = True) -> AsyncIterator[str]:
        """SSE frames for one client: history first, then live, with heartbeats."""
        queue = await self.subscribe(room_id)
        seq = 0
        try:
            if replay:
                for event in self.history(room_id):
                    seq += 1
                    yield sse_frame(event, seq)

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # A comment frame. Keeps proxies from closing an idle connection
                    # while the agents are thinking, which can take a while.
                    yield ": keepalive\n\n"
                    continue
                seq += 1
                yield sse_frame(event, seq)
        except asyncio.CancelledError:
            raise
        finally:
            await self.unsubscribe(room_id, queue)
