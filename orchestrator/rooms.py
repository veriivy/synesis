"""In-memory rooms — the live, authoritative state a running negotiation
or execution actually operates on. db.py mirrors a snapshot of this into
Mongo at a few milestones (see room_snapshot below) for durability, but
the asyncio.Task driving a room is still gone on restart; see db.py's
module docstring for exactly what that does and doesn't buy you.

Extends the original Room (room_id/phase/task) with what the tasking
endpoints, ticket decomposition, and execution need: participants, task
intake, the negotiated FinalPlan, tickets, and the workspace files.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum

from .schemas import FinalPlan, Participant, Task, Ticket
from .workspace import seed_files


class Phase(str, Enum):
    WAITING = "waiting"
    NEGOTIATING = "negotiating"
    ANALYZED = "analyzed"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class FileState:
    content: str
    last_written_by: str | None = None


@dataclass
class Room:
    room_id: str
    phase: Phase = Phase.WAITING
    task: asyncio.Task | None = None  # the negotiate() background task
    execute_task: asyncio.Task | None = None  # the execute() background task

    # Task intake: POST /participants, /context, /tasks.
    participants: dict[str, Participant] = field(default_factory=dict)
    project_context: str = ""
    context_version: int = 0
    tasks_by_user: dict[str, list[Task]] = field(default_factory=dict)

    # Mid-loop interjections (CLAUDE.md step 3a). POST /messages appends
    # here and publishes the user_message event immediately; the
    # negotiate loop drains (clears) this at the top of each round.
    pending_messages: list[str] = field(default_factory=list)
    current_round: int = 0

    # Negotiation output.
    plan: FinalPlan | None = None
    # step_id -> agent_id, set when the FinalPlan is built. Not part of the
    # wire FinalPlan (Step has no owner field) — ticketing.py uses it only
    # to give K2's prompt a hint a real model could otherwise infer from
    # the plan's own rationale text.
    step_owner: dict[str, str] = field(default_factory=dict)
    # agent_id -> user_id, set once opening PoAs exist (main.py — works
    # for both the live-tasks path and the fixture-fallback path, since
    # both always produce a poa1/poa2 with agent_id/user_id). BYO key
    # threading (main.py's _agent_provider_and_key) uses this to find
    # which participant, if any, owns a given agent.
    agent_users: dict[str, str] = field(default_factory=dict)

    # Ticket decomposition + execution.
    tickets: list[Ticket] = field(default_factory=list)
    files: dict[str, FileState] = field(
        default_factory=lambda: {p: FileState(content=c) for p, c in seed_files().items()}
    )

    def tasks_payload(self) -> dict:
        """The exact shape k2.analyze / agents.revise_poa already expect
        (and fixtures/tasks.json already uses): room_id + tasks_by_user."""
        return {
            "room_id": self.room_id,
            "tasks_by_user": [
                {
                    "user_id": user_id,
                    "display_name": (
                        self.participants[user_id].display_name
                        if user_id in self.participants
                        else user_id
                    ),
                    "tasks": [t.model_dump() for t in tasks],
                }
                for user_id, tasks in self.tasks_by_user.items()
            ],
        }


def room_snapshot(room: Room) -> dict:
    """A Mongo-ready snapshot of `room`'s durable state — no asyncio.Task,
    no lock, and (via Participant.api_key's Field(exclude=True)) never an
    api_key. Called from main.py at each milestone; see db.save_room."""
    return {
        "room_id": room.room_id,
        "phase": room.phase.value,
        "participants": {
            user_id: p.model_dump() for user_id, p in room.participants.items()
        },
        "project_context": room.project_context,
        "context_version": room.context_version,
        "tasks_by_user": {
            user_id: [t.model_dump() for t in tasks]
            for user_id, tasks in room.tasks_by_user.items()
        },
        "current_round": room.current_round,
        "plan": room.plan.model_dump() if room.plan else None,
        "tickets": [t.model_dump() for t in room.tickets],
        "files": {
            path: {"content": f.content, "last_written_by": f.last_written_by}
            for path, f in room.files.items()
        },
    }


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
