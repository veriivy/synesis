"""Pydantic models for the FROZEN CONTRACT schemas.

Field names and shapes must match CLAUDE.md's "### Schemas" section exactly,
and web/lib/types.ts's snake_case wire types — that pair is the source of
truth, this just gives the Python side the same shapes with validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["claude", "gemini", "gpt"]
Priority = Literal["must", "want"]
Severity = Literal["blocking", "minor"]
Lane = Literal["parallel", "sequential"]
TicketStatus = Literal["pending", "running", "done", "failed"]
PlanStatus = Literal["proposed", "approved", "rejected"]


class Step(BaseModel):
    step_id: str
    title: str
    description: str
    files_touched: list[str] = Field(default_factory=list)
    rationale: str = ""


class PoA(BaseModel):
    poa_id: str
    agent_id: str
    user_id: str
    summary: str
    steps: list[Step]
    assumptions: list[str] = Field(default_factory=list)


class Similarity(BaseModel):
    topic: str
    detail: str


class Difference(BaseModel):
    issue_id: str
    topic: str
    positions: dict[str, str]
    severity: Severity


class Analysis(BaseModel):
    round: int
    similarities: list[Similarity] = Field(default_factory=list)
    differences: list[Difference] = Field(default_factory=list)
    converged: bool


class Resolution(BaseModel):
    issue_id: str
    outcome: str
    rationale: str


class FinalPlan(BaseModel):
    plan_id: str
    rounds_used: int
    summary: str
    steps: list[Step]
    resolutions: list[Resolution] = Field(default_factory=list)
    approvals: dict[str, bool] = Field(default_factory=dict)
    status: PlanStatus = "proposed"


class Ticket(BaseModel):
    ticket_id: str
    plan_id: str
    title: str
    description: str
    assigned_agent: str
    files_owned: list[str]
    depends_on: list[str] = Field(default_factory=list)
    lane: Lane = "parallel"
    status: TicketStatus = "pending"


class SharedContext(BaseModel):
    room_id: str
    version: int
    content: str
    updated_at: str


class Task(BaseModel):
    text: str
    priority: Priority


class Participant(BaseModel):
    user_id: str
    display_name: str
    provider: Provider
    model: str
    agent_id: str | None = None
    # Held only in the in-memory room for the room's lifetime; never in an
    # event, a log line, or persisted storage. See CLAUDE.md "Keys" clause.
    api_key: str | None = Field(default=None, exclude=True, repr=False)
