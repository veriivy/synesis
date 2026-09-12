"""SSE event models and the per-room pub/sub that backs GET /rooms/{id}/stream.

Event shapes are transcribed from CLAUDE.md's "### SSE events" section and
must match web/lib/types.ts's `SSEEvent` union exactly, field for field —
that TS file is what the frontend's exhaustive reducer switch is built
against.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from .schemas import Analysis, Difference, FinalPlan, PoA, Similarity, Ticket, TicketStatus


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() % 1 * 1000):03d}Z"


class EventBase(BaseModel):
    room_id: str
    ts: str = Field(default_factory=now_iso)


class ParticipantJoined(EventBase):
    type: Literal["participant_joined"] = "participant_joined"
    user_id: str
    provider: str
    model: str


class PoaGenerated(EventBase):
    type: Literal["poa_generated"] = "poa_generated"
    agent_id: str
    user_id: str
    poa: PoA


class AnalysisEvent(EventBase):
    type: Literal["analysis"] = "analysis"
    round: int
    similarities: list[Similarity]
    differences: list[Difference]
    converged: bool


class AgentMessage(EventBase):
    type: Literal["agent_message"] = "agent_message"
    round: int
    agent_id: str
    content: str
    addresses_issues: list[str] = Field(default_factory=list)


class UserMessage(EventBase):
    type: Literal["user_message"] = "user_message"
    round: int
    user_id: str
    content: str


class ModeratorMessage(EventBase):
    type: Literal["moderator_message"] = "moderator_message"
    round: int
    content: str


class RoundComplete(EventBase):
    type: Literal["round_complete"] = "round_complete"
    round: int


class PlanProposed(EventBase):
    type: Literal["plan_proposed"] = "plan_proposed"
    plan: FinalPlan


class ApprovalUpdated(EventBase):
    type: Literal["approval_updated"] = "approval_updated"
    user_id: str
    approved: bool


class PlanApproved(EventBase):
    type: Literal["plan_approved"] = "plan_approved"
    plan_id: str


class ContextUpdated(EventBase):
    type: Literal["context_updated"] = "context_updated"
    version: int
    content: str


class TicketsCreated(EventBase):
    type: Literal["tickets_created"] = "tickets_created"
    tickets: list[Ticket]


class TicketStarted(EventBase):
    type: Literal["ticket_started"] = "ticket_started"
    ticket_id: str
    agent_id: str


class FileWritten(EventBase):
    type: Literal["file_written"] = "file_written"
    ticket_id: str
    agent_id: str
    path: str
    accepted: bool
    reason: str | None = None


class TicketCompleted(EventBase):
    type: Literal["ticket_completed"] = "ticket_completed"
    ticket_id: str
    status: TicketStatus


class ErrorEvent(EventBase):
    type: Literal["error"] = "error"
    where: str
    detail: str


SSEEvent = Annotated[
    Union[
        ParticipantJoined,
        PoaGenerated,
        AnalysisEvent,
        AgentMessage,
        UserMessage,
        ModeratorMessage,
        RoundComplete,
        PlanProposed,
        ApprovalUpdated,
        PlanApproved,
        ContextUpdated,
        TicketsCreated,
        TicketStarted,
        FileWritten,
        TicketCompleted,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]

_event_adapter: TypeAdapter[SSEEvent] = TypeAdapter(SSEEvent)


def parse_event(raw: dict) -> SSEEvent:
    return _event_adapter.validate_python(raw)


def to_sse_line(event: BaseModel) -> str:
    """Format one SSE `data:` frame. We don't set an `event:` field — the
    frontend dispatches on the JSON body's own `type`, matching how the
    fixture player already consumes these (see web/lib/fixtures.ts)."""
    return f"data: {event.model_dump_json()}\n\n"


class RoomBus:
    """Per-room event log + fan-out to live subscribers.

    A late subscriber (a fresh page load, an SSE reconnect) replays the full
    log first, then joins live — matching roomReducer.ts's `replay()`, which
    is exactly what a reconnect needs: it's pure, so replaying the log and
    then continuing live produces the same state a subscriber who was
    there the whole time would have.
    """

    def __init__(self) -> None:
        self._log: list[SSEEvent] = []
        self._subscribers: list[asyncio.Queue[SSEEvent]] = []
        self._lock = asyncio.Lock()

    @property
    def log(self) -> list[SSEEvent]:
        return list(self._log)

    async def publish(self, event: SSEEvent) -> None:
        async with self._lock:
            self._log.append(event)
            for queue in self._subscribers:
                queue.put_nowait(event)

    async def subscribe(self) -> tuple[list[SSEEvent], "asyncio.Queue[SSEEvent]"]:
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue()
        async with self._lock:
            backlog = list(self._log)
            self._subscribers.append(queue)
        return backlog, queue

    def unsubscribe(self, queue: "asyncio.Queue[SSEEvent]") -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)


__all__ = [
    "SSEEvent",
    "RoomBus",
    "to_sse_line",
    "parse_event",
    "now_iso",
    "ParticipantJoined",
    "PoaGenerated",
    "AnalysisEvent",
    "AgentMessage",
    "UserMessage",
    "ModeratorMessage",
    "RoundComplete",
    "PlanProposed",
    "ApprovalUpdated",
    "PlanApproved",
    "ContextUpdated",
    "TicketsCreated",
    "TicketStarted",
    "FileWritten",
    "TicketCompleted",
    "ErrorEvent",
]
