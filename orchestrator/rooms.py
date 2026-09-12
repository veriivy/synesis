"""In-memory rooms. Mongo can mirror later; this is enough to run the K2 slice."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Phase(str, Enum):
    WAITING = "waiting"
    NEGOTIATING = "negotiating"
    ANALYZED = "analyzed"
    FAILED = "failed"


@dataclass
class Room:
    room_id: str
    phase: Phase = Phase.WAITING
    task: asyncio.Task | None = None


class RoomStore:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create(self) -> Room:
        room = Room(room_id=f"room_{uuid.uuid4().hex[:8]}")
        self._rooms[room.room_id] = room
        return room

    def get(self, room_id: str) -> Room | None:
        return self._rooms.get(room_id)

    def __len__(self) -> int:
        return len(self._rooms)
