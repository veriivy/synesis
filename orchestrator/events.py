"""SSE bus. History is replayed on connect so a refresh does not blank the UI."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_sse(event: dict) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    async def publish(self, room_id: str, event: dict) -> None:
        event.setdefault("room_id", room_id)
        event.setdefault("ts", utc_now())
        self._history[room_id].append(event)
        for queue in list(self._subs[room_id]):
            await queue.put(event)

    def history(self, room_id: str) -> list[dict]:
        """The event log so far, in order. Used by tests that assert on
        what a room published without opening a real SSE connection."""
        return list(self._history[room_id])

    async def stream(self, room_id: str):
        queue: asyncio.Queue = asyncio.Queue()
        self._subs[room_id].append(queue)
        try:
            for event in self._history[room_id]:
                yield format_sse(event)
            while True:
                event = await queue.get()
                yield format_sse(event)
        finally:
            self._subs[room_id].remove(queue)
