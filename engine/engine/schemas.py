"""THE FROZEN CONTRACT, as Pydantic models.

This module is the single source of truth for event and workplan shapes on the Python
side. `/fixtures` is the same contract as data — `tests/test_contract.py` asserts the
two agree, so the fixtures the frontend is built against cannot silently drift from
what the backend emits.

Do not change field names here without the team agreeing out loud.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# --- helpers -----------------------------------------------------------------


def utc_now() -> str:
    """ISO-8601 UTC with a trailing Z. Every `ts` in the contract uses this."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- inputs ------------------------------------------------------------------

Priority = Literal["must-have", "nice-to-have"]


class Requirement(BaseModel):
    req_id: str
    text: str
    priority: Priority = "nice-to-have"

    @property
    def is_must_have(self) -> bool:
        return self.priority == "must-have"


class AgentSpec(BaseModel):
    """An agent's identity: who it speaks for, and with which model.

    `agent_id` is the stable public name used in every event (`claude`, `gpt`,
    `gemini`, `k2`, `grok`). `provider` selects the client; `model` is passed through
    untouched so a bad model id fails loudly at call time rather than being guessed at.
    """

    agent_id: str
    provider: str
    model: str
    user_id: str
    display_name: str = ""
    requirements: list[Requirement] = Field(default_factory=list)

    def must_haves(self) -> list[Requirement]:
        return [r for r in self.requirements if r.is_must_have]


# --- workplan ----------------------------------------------------------------


class RepoRef(BaseModel):
    url: str = ""
    commit: str = ""


class Task(BaseModel):
    task_id: str
    title: str
    description: str = ""
    owner_agent: str
    owner_user: str = ""
    files_owned: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class InterfaceContract(BaseModel):
    contract_id: str
    name: str
    kind: Literal["function", "http_endpoint", "schema"] = "function"
    signature: str = ""
    producer_task: str = ""
    consumer_tasks: list[str] = Field(default_factory=list)
    notes: str = ""


class Concession(BaseModel):
    agent: str
    gave_up: str
    accepted: str
    reason: str = ""


class UnresolvedIssue(BaseModel):
    issue: str
    positions: dict[str, str] = Field(default_factory=dict)


class Workplan(BaseModel):
    plan_id: str
    repo: RepoRef = Field(default_factory=RepoRef)
    rounds_used: int = 0
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    tasks: list[Task] = Field(default_factory=list)
    interface_contracts: list[InterfaceContract] = Field(default_factory=list)
    concessions: list[Concession] = Field(default_factory=list)
    unresolved: list[UnresolvedIssue] = Field(default_factory=list)

    def files_owned_by(self, agent_id: str) -> set[str]:
        """Every path this agent may write, across all of its tasks.

        The server's sandbox is the enforcement point; this is the lookup it uses.
        """
        owned: set[str] = set()
        for task in self.tasks:
            if task.owner_agent == agent_id:
                owned.update(task.files_owned)
        return owned

    def tasks_for(self, agent_id: str) -> list[Task]:
        return [t for t in self.tasks if t.owner_agent == agent_id]


# --- events ------------------------------------------------------------------


class BaseEvent(BaseModel):
    room_id: str
    ts: str = Field(default_factory=utc_now)


class AgentMessageEvent(BaseEvent):
    type: Literal["agent_message"] = "agent_message"
    round: int
    agent_id: str
    user_id: str
    content: str


class RoundCompleteEvent(BaseEvent):
    type: Literal["round_complete"] = "round_complete"
    round: int


class PlanProposedEvent(BaseEvent):
    type: Literal["plan_proposed"] = "plan_proposed"
    plan: Workplan


class DeadlockEvent(BaseEvent):
    type: Literal["deadlock"] = "deadlock"
    round: int
    unresolved: list[UnresolvedIssue] = Field(default_factory=list)


class FileWrittenEvent(BaseEvent):
    """Emitted for EVERY write attempt, accepted or not.

    A rejection carries `accepted: false` and a non-null `reason` prefixed with the
    rule that fired: `no_approved_plan`, `path_escape`, or `path_not_owned`.
    """

    type: Literal["file_written"] = "file_written"
    agent_id: str
    path: str
    bytes: int = 0
    accepted: bool
    reason: str | None = None


class CommitEvent(BaseEvent):
    type: Literal["commit"] = "commit"
    agent_id: str
    sha: str
    message: str
    files: list[str] = Field(default_factory=list)


Event = (
    AgentMessageEvent
    | RoundCompleteEvent
    | PlanProposedEvent
    | DeadlockEvent
    | FileWrittenEvent
    | CommitEvent
)


# --- moderator ---------------------------------------------------------------


class ModeratorVerdict(BaseModel):
    """What the moderator emits after each round.

    `premature` is the anti-agreement guard: agents that converge without anybody
    having given anything up have not negotiated, they have just been agreeable. The
    loop forces another round when this is true.
    """

    converged: bool = False
    conflicts_remaining: list[UnresolvedIssue] = Field(default_factory=list)
    premature: bool = False
    note: str = ""
