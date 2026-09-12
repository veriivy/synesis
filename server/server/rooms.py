"""Room state and lifecycle.

A room owns: its participants, its workspace clone, its plan, and the task that runs
the negotiation. Everything is in-process; Mongo is a write-behind mirror, not the
source of truth.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from engine.config import model_for
from engine.schemas import AgentSpec, RepoRef, Requirement, Workplan

from .workspace import Workspace

log = logging.getLogger(__name__)


class Phase(str, Enum):
    WAITING = "waiting"          # collecting intents
    NEGOTIATING = "negotiating"
    AWAITING_APPROVAL = "awaiting_approval"
    DEADLOCKED = "deadlocked"
    EXECUTING = "executing"
    DONE = "done"
    REJECTED = "rejected"


@dataclass
class Room:
    room_id: str
    feature: str = ""
    phase: Phase = Phase.WAITING
    specs: list[AgentSpec] = field(default_factory=list)
    workspace: Workspace | None = None
    repo: RepoRef = field(default_factory=RepoRef)
    plan: Workplan | None = None
    task: asyncio.Task | None = None

    def add_intent(
        self,
        *,
        user_id: str,
        agent_id: str,
        requirements: list[Requirement],
        provider: str | None = None,
        display_name: str = "",
    ) -> AgentSpec:
        """Register (or replace) one participant's requirements.

        Replacing rather than appending means a user can resubmit after a typo without
        ending up with two agents arguing on their behalf.
        """
        from engine.config import AGENT_ID_TO_PROVIDER

        resolved_provider = provider or AGENT_ID_TO_PROVIDER.get(agent_id)
        if resolved_provider is None:
            raise ValueError(
                f"unknown agent_id {agent_id!r} and no provider given; "
                f"known agents: {sorted(AGENT_ID_TO_PROVIDER)}"
            )

        spec = AgentSpec(
            agent_id=agent_id,
            provider=resolved_provider,
            model=model_for(resolved_provider),
            user_id=user_id,
            display_name=display_name,
            requirements=requirements,
        )
        self.specs = [s for s in self.specs if s.user_id != user_id] + [spec]
        return spec

    def spec_for(self, agent_id: str) -> AgentSpec | None:
        return next((s for s in self.specs if s.agent_id == agent_id), None)

    @property
    def ready_to_negotiate(self) -> bool:
        """Two distinct users with requirements. One agent is not a negotiation."""
        return len(self.specs) >= 2 and all(s.requirements for s in self.specs)

    def summary(self) -> dict:
        return {
            "room_id": self.room_id,
            "phase": self.phase.value,
            "feature": self.feature,
            "repo": self.repo.model_dump(),
            "participants": [
                {
                    "user_id": s.user_id,
                    "agent_id": s.agent_id,
                    "provider": s.provider,
                    "model": s.model,
                    "display_name": s.display_name,
                    "requirements": [r.model_dump() for r in s.requirements],
                }
                for s in self.specs
            ],
            "plan": self.plan.model_dump() if self.plan else None,
        }


class RoomStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root)
        self._rooms: dict[str, Room] = {}

    def create(self, feature: str = "") -> Room:
        room_id = f"room_{uuid.uuid4().hex[:8]}"
        room = Room(room_id=room_id, feature=feature)
        room.workspace = Workspace(
            room_id=room_id, root=self.workspace_root / room_id
        )
        self._rooms[room_id] = room
        log.info("created %s", room_id)
        return room

    def get(self, room_id: str) -> Room | None:
        return self._rooms.get(room_id)

    def all(self) -> list[Room]:
        return list(self._rooms.values())

    async def shutdown(self) -> None:
        for room in self._rooms.values():
            if room.task and not room.task.done():
                room.task.cancel()
                try:
                    await room.task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
