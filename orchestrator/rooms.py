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
import secrets
import uuid
from dataclasses import dataclass, field
from enum import Enum

from .goal import Goal
from .influence import shares
from .interjections import Interjection
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
    # user_id -> a token issued the first time that user_id joins
    # (main.py's add_participant), required on subsequent /participants,
    # /tasks, /messages, /plan/approve calls for that user_id — see
    # main._check_owner. Never included in room_snapshot (only
    # `participants`/`tasks_by_user`/etc. are serialized below), same
    # reasoning as api_key: this is a secret, not room state to persist.
    participant_tokens: dict[str, str] = field(default_factory=dict, repr=False)
    project_context: str = ""
    context_version: int = 0
    tasks_by_user: dict[str, list[Task]] = field(default_factory=dict)

    # Room governance, both configured by whoever created the room (they
    # hold admin_token, returned once by POST /rooms) and both frozen once
    # negotiation starts — see `governance_locked`.
    #   goal: the north star every agent is anchored to (goal.py).
    #   weights: user_id -> relative influence (influence.py); anyone
    #     absent weighs influence.DEFAULT_WEIGHT.
    # admin_token is a secret like participant_tokens: never serialized.
    admin_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    goal: Goal | None = None
    weights: dict[str, float] = field(default_factory=dict)

    # Mid-loop interjections (CLAUDE.md step 3a; interjections.py). POST
    # /messages and a plan rejection's notes append here; the negotiate loop
    # drains it at the top of each round and hands the batch to K2 and both
    # advocates. Anything that arrives after the last round's drain waits
    # for the next negotiation (a re-negotiation after a rejection).
    pending_messages: list[Interjection] = field(default_factory=list)
    current_round: int = 0

    # agent_id -> that advocate's latest PoA. Kept so a re-negotiation (after
    # a rejected plan) resumes from where the argument left off instead of
    # redrafting from scratch and relitigating everything already conceded.
    poas: dict[str, dict] = field(default_factory=dict)

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

    @property
    def governance_locked(self) -> bool:
        """The goal and the weights are setup, not levers to pull mid-
        argument: once negotiation has started (phase leaves WAITING, and
        it never goes back), both are fixed for the room's life."""
        return self.phase != Phase.WAITING

    def drain_interjections(self) -> list[Interjection]:
        """Take everything queued so far. No await inside, so nothing can
        land between the read and the reset."""
        batch, self.pending_messages = self.pending_messages, []
        return batch

    def display_name(self, user_id: str) -> str:
        p = self.participants.get(user_id)
        return p.display_name if p else user_id

    def user_shares(self) -> dict[str, float]:
        """Influence share for everyone the room knows about: joined
        participants plus anyone weighted ahead of joining."""
        return shares(self.weights, [*self.participants, *self.weights])

    def agent_shares(self) -> dict[str, float]:
        """agent_id -> its user's influence share among the negotiating
        users only (a weighted bystander doesn't dilute the two who are
        actually arguing). Needs agent_users, so: set once PoAs exist."""
        by_user = shares(self.weights, self.agent_users.values())
        return {agent_id: by_user[user_id] for agent_id, user_id in self.agent_users.items()}

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
        "goal": room.goal.public() if room.goal else None,
        "weights": dict(room.weights),
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
