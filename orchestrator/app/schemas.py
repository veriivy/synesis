"""Pydantic models for the FROZEN CONTRACT schemas this slice touches.

Field names and shapes must match CLAUDE.md's "### Schemas" section exactly —
that file is the source of truth, this just gives it types.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Step(BaseModel):
    step_id: str
    title: str
    description: str
    files_touched: list[str] = Field(default_factory=list)
    rationale: str = ""


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
    status: Literal["proposed", "approved", "rejected"] = "proposed"


class Ticket(BaseModel):
    ticket_id: str
    plan_id: str
    title: str
    description: str
    assigned_agent: str
    files_owned: list[str]
    depends_on: list[str] = Field(default_factory=list)
    lane: Literal["parallel", "sequential"] = "parallel"
    status: Literal["pending", "running", "done", "failed"] = "pending"
