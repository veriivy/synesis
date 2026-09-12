"""In-memory room state.

CLAUDE.md specs Mongo Atlas for persistence; that wiring is explicitly out
of scope for this slice (it wasn't one of the improvements asked for, and
adding a datastore is not a "small working increment"). This is the
same-process stand-in: correct for a single orchestrator instance, gone on
restart. Swapping in Mongo later means replacing RoomStore's dict with
collection reads/writes — nothing above this module should need to change,
since callers only ever go through get_room/create_room.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .events import RoomBus
from .schemas import Analysis, FinalPlan, Participant, PoA, Task, Ticket
from .workspace import seed_files


class RoomNotFoundError(KeyError):
    pass


@dataclass
class FileState:
    content: str
    last_written_by: str | None = None


@dataclass
class Room:
    room_id: str
    bus: RoomBus = field(default_factory=RoomBus)
    participants: dict[str, Participant] = field(default_factory=dict)
    project_context: str = ""
    context_version: int = 0
    tasks_by_user: dict[str, list[Task]] = field(default_factory=dict)
    poas: dict[str, PoA] = field(default_factory=dict)  # agent_id -> PoA
    current_round: int = 0
    latest_analysis: Analysis | None = None
    plan: FinalPlan | None = None
    # step_id -> agent_id, set by k2/negotiation.py when it builds the
    # FinalPlan. Not part of the wire FinalPlan (the frozen Step schema has
    # no owner field) — k2/ticketing.py uses it only to give the template
    # ticketing provider something to assign tickets to.
    step_owner: dict[str, str] = field(default_factory=dict)
    tickets: list[Ticket] = field(default_factory=list)
    files: dict[str, FileState] = field(default_factory=lambda: {
        path: FileState(content=content) for path, content in seed_files().items()
    })
    status: str = "setup"
    negotiate_started: bool = False
    execute_started: bool = False
    # The asyncio.Task running negotiate() or execute() in the background.
    # Production code (main.py) fires these and returns 202 without
    # awaiting — a real client follows progress over SSE. Tests await this
    # directly instead of consuming the live stream: httpx's ASGITransport
    # collects a streamed response in full before returning it, so it can't
    # be used in-process to read a stream that (correctly) never ends.
    background_task: asyncio.Task | None = field(default=None, repr=False, compare=False)
    # Mid-loop interjections (CLAUDE.md step 3a: "drain the user-message
    # queue"). Cut-order item #1 in CLAUDE.md — kept minimal: POST
    # /rooms/{id}/messages appends the raw content here and publishes the
    # user_message event immediately; the negotiation loop drains (clears)
    # this at the top of each round so nothing leaks into a later one.
    pending_messages: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def agent_id_for_user(self, user_id: str) -> str | None:
        p = self.participants.get(user_id)
        return p.agent_id if p else None

    def user_id_for_agent(self, agent_id: str) -> str | None:
        for p in self.participants.values():
            if p.agent_id == agent_id:
                return p.user_id
        return None


class RoomStore:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create(self, room_id: str) -> Room:
        room = Room(room_id=room_id)
        self._rooms[room_id] = room
        return room

    def get(self, room_id: str) -> Room:
        try:
            return self._rooms[room_id]
        except KeyError:
            raise RoomNotFoundError(room_id) from None


# One process-wide store. FastAPI dependency-injects this via app.state in
# main.py; tests construct their own RoomStore() instead of sharing it.
_default_store = RoomStore()


def default_store() -> RoomStore:
    return _default_store
