"""In-memory rooms. Mongo can mirror later; this is enough to run the K2 slice.

Extends the original Room (room_id/phase/task) with what the tasking
endpoints, ticket decomposition, and execution need: participants, task
intake, the negotiated FinalPlan, tickets, and the workspace files. Nothing
here is persisted — gone on restart, same as before.
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
