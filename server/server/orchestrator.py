"""Drives a room through its phases and publishes every event onto the bus.

  intents -> negotiate -> (deadlock | plan_proposed) -> human approval -> execute -> commit

The approval gate is a real gate: `Workspace.plan_approved` stays False until a human
says yes, and the sandbox refuses every write while it is False. Nothing here can skip
it, because nothing here does the writing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from engine.execution import Executor
from engine.negotiation import Negotiation
from engine.providers import ProviderError, build_client
from engine.schemas import CommitEvent, utc_now

from . import git_ops
from .db import Store
from .events import EventBus
from .rooms import Phase, Room

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, bus: EventBus, store: Store) -> None:
        self.bus = bus
        self.store = store

    async def _emit(self, room_id: str, event: dict) -> None:
        await self.bus.publish(room_id, event)
        await self.store.append_event(room_id, event)

    async def _error(self, room_id: str, message: str) -> None:
        """Errors travel the same channel as everything else, so the UI can show them
        instead of just going quiet."""
        log.error("room %s: %s", room_id, message)
        await self._emit(
            room_id, {"type": "error", "room_id": room_id, "message": message, "ts": utc_now()}
        )

    # --- negotiation ---------------------------------------------------------

    async def negotiate(self, room: Room) -> None:
        room.phase = Phase.NEGOTIATING
        try:
            negotiation = Negotiation(
                room_id=room.room_id,
                specs=room.specs,
                feature=room.feature,
                repo_map=room.workspace.repo_map() if room.workspace else "",
                repo=room.repo,
            )
        except (ProviderError, ValueError) as exc:
            room.phase = Phase.WAITING
            await self._error(room.room_id, f"could not start negotiation: {exc}")
            return

        try:
            async for event in negotiation.run():
                payload = event.model_dump()
                await self._emit(room.room_id, payload)

                if event.type == "plan_proposed":
                    room.plan = event.plan
                    if room.workspace:
                        # Attached but NOT approved. The sandbox still refuses writes.
                        room.workspace.plan = event.plan
                        room.workspace.plan_approved = False
                    room.phase = Phase.AWAITING_APPROVAL
                    await self.store.save_plan(room.room_id, event.plan.model_dump())

                elif event.type == "deadlock":
                    room.phase = Phase.DEADLOCKED

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a crashed room must still report
            room.phase = Phase.WAITING
            await self._error(room.room_id, f"negotiation failed: {exc}")
            return

        await self.store.upsert_room(room.room_id, room.summary())

    # --- execution -----------------------------------------------------------

    async def execute(self, room: Room) -> None:
        """Run every agent's implementation loop, then commit per agent per task."""
        if room.plan is None or room.workspace is None:
            await self._error(room.room_id, "cannot execute without an approved plan")
            return

        room.phase = Phase.EXECUTING

        clients = {}
        for spec in room.specs:
            try:
                clients[spec.agent_id] = build_client(spec.provider)
            except (ProviderError, ValueError) as exc:
                await self._error(room.room_id, f"agent {spec.agent_id} unavailable: {exc}")

        executor = Executor(
            room_id=room.room_id, plan=room.plan, host=room.workspace, clients=clients
        )

        for spec in room.specs:
            if spec.agent_id not in clients:
                continue
            try:
                async for event in executor.run_agent(spec):
                    await self._emit(room.room_id, event.model_dump())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await self._error(room.room_id, f"{spec.agent_id} execution failed: {exc}")
                continue

            await self._commit_tasks(room, spec.agent_id)

        room.phase = Phase.DONE
        await self.store.upsert_room(room.room_id, room.summary())

    async def _commit_tasks(self, room: Room, agent_id: str) -> None:
        """One commit per agent per task, authored as that agent."""
        assert room.plan is not None and room.workspace is not None

        for task in room.plan.tasks_for(agent_id):
            message = f"{task.task_id}: {task.title}"
            try:
                result = await asyncio.to_thread(
                    git_ops.commit_as_agent,
                    room.workspace.root,
                    agent_id,
                    message,
                    task.files_owned,
                )
            except git_ops.GitError as exc:
                await self._error(room.room_id, f"commit failed for {task.task_id}: {exc}")
                continue

            if result is None:
                log.info("nothing to commit for %s / %s", agent_id, task.task_id)
                continue

            await self._emit(
                room.room_id,
                CommitEvent(
                    room_id=room.room_id,
                    agent_id=agent_id,
                    sha=result.sha,
                    message=result.message,
                    files=result.files,
                ).model_dump(),
            )

    # --- replay --------------------------------------------------------------

    async def replay(self, room: Room, fixture: Path, delay: float = 1.2) -> None:
        """Replay a recorded stream. The backup demo: no keys, no latency, no surprises.

        Timings are faked rather than real so the pacing suits a 3-minute pitch.
        """
        room.phase = Phase.NEGOTIATING
        try:
            lines = [
                line for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
        except OSError as exc:
            await self._error(room.room_id, f"replay fixture unreadable: {exc}")
            return

        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event["room_id"] = room.room_id
            event["ts"] = utc_now()
            await self._emit(room.room_id, event)

            if event.get("type") == "plan_proposed":
                from engine.schemas import Workplan

                room.plan = Workplan.model_validate(event["plan"])
                room.phase = Phase.AWAITING_APPROVAL
                if room.workspace:
                    room.workspace.plan = room.plan
                    room.workspace.plan_approved = False
                # Stop at the approval gate — a human still has to press the button,
                # because that beat is part of the demo.
                return

            await asyncio.sleep(delay if event.get("type") == "agent_message" else delay / 3)

    async def replay_after_approval(self, room: Room, fixture: Path, delay: float = 0.8) -> None:
        """The post-approval half of a recorded stream: writes, the rejection, commits."""
        room.phase = Phase.EXECUTING
        lines = [
            line for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        seen_plan = False
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not seen_plan:
                seen_plan = event.get("type") == "plan_proposed"
                continue

            event["room_id"] = room.room_id
            event["ts"] = utc_now()
            await self._emit(room.room_id, event)
            await asyncio.sleep(delay)

        room.phase = Phase.DONE
