"""The moderator: judges each round, then synthesises the workplan.

Deliberately a separate agent with no stake in the outcome. It is also the component
most likely to hand back malformed JSON at 3am, so every read here degrades to a safe
default instead of raising — a bad moderator response costs one round, never the run.
"""

from __future__ import annotations

import logging
import uuid

from . import prompts
from .jsonx import extract_object
from .providers import LLMClient, Message, ProviderError
from .schemas import (
    AgentSpec,
    ModeratorVerdict,
    RepoRef,
    UnresolvedIssue,
    Workplan,
)

log = logging.getLogger(__name__)


def _requirements_digest(specs: list[AgentSpec]) -> str:
    lines = []
    for spec in specs:
        who = spec.display_name or spec.user_id
        lines.append(f"{spec.agent_id} (for {who}):")
        lines.extend(
            f"  {r.req_id} [{r.priority}] {r.text}" for r in spec.requirements
        )
    return "\n".join(lines)


class Moderator:
    def __init__(self, client: LLMClient, max_rounds: int = 3) -> None:
        self.client = client
        self.max_rounds = max_rounds

    async def judge(
        self, *, round_index: int, transcript: str, specs: list[AgentSpec]
    ) -> ModeratorVerdict:
        """Judge one round. Never raises — a failure means 'not converged'."""
        user = prompts.MODERATOR_USER.format(
            round=round_index,
            max_rounds=self.max_rounds,
            requirements=_requirements_digest(specs),
            transcript=transcript,
        )
        try:
            completion = await self.client.complete(
                system=prompts.MODERATOR_SYSTEM,
                messages=[Message(role="user", content=user)],
                max_tokens=2048,
            )
        except ProviderError as exc:
            log.error("moderator call failed on round %d: %s", round_index, exc)
            return ModeratorVerdict(
                converged=False, note=f"moderator unavailable ({exc}); continuing"
            )

        data = extract_object(completion.text)
        if data is None:
            log.warning("moderator returned unparseable JSON on round %d", round_index)
            return ModeratorVerdict(
                converged=False, note="moderator response unparseable; continuing"
            )

        conflicts: list[UnresolvedIssue] = []
        for raw in data.get("conflicts_remaining") or []:
            if not isinstance(raw, dict):
                continue
            positions = raw.get("positions")
            conflicts.append(
                UnresolvedIssue(
                    issue=str(raw.get("issue", "")).strip() or "(unspecified)",
                    positions={
                        str(k): str(v)
                        for k, v in (positions or {}).items()
                        if isinstance(positions, dict)
                    },
                )
            )

        converged = bool(data.get("converged"))
        premature = bool(data.get("premature"))
        # Premature convergence is not convergence, whatever the moderator ticked.
        if premature:
            converged = False

        return ModeratorVerdict(
            converged=converged,
            premature=premature,
            conflicts_remaining=conflicts,
            note=str(data.get("note", "")).strip(),
        )

    async def synthesise_plan(
        self,
        *,
        transcript: str,
        specs: list[AgentSpec],
        rounds_used: int,
        repo_map: str,
        repo: RepoRef,
    ) -> Workplan | None:
        """Turn the transcript into a validated workplan, or None if it cannot be."""
        agent_ids = [s.agent_id for s in specs]
        system = prompts.PLAN_SYSTEM.format(
            schema=prompts.plan_schema_hint(),
            agent_ids=", ".join(agent_ids),
            repo_map=repo_map or "(repository map unavailable)",
        )
        user = prompts.PLAN_USER.format(transcript=transcript, rounds_used=rounds_used)

        try:
            completion = await self.client.complete(
                system=system,
                messages=[Message(role="user", content=user)],
                max_tokens=8192,
            )
        except ProviderError as exc:
            log.error("plan synthesis failed: %s", exc)
            return None

        data = extract_object(completion.text)
        if data is None:
            log.error("plan synthesis returned unparseable JSON")
            return None

        data.setdefault("plan_id", f"plan_{uuid.uuid4().hex[:8]}")
        data["rounds_used"] = rounds_used
        data["status"] = "proposed"
        data["repo"] = repo.model_dump()

        try:
            plan = Workplan.model_validate(data)
        except Exception as exc:  # pydantic ValidationError and anything odder
            log.error("plan failed validation: %s", exc)
            return None

        problems = validate_ownership(plan, agent_ids)
        if problems:
            # A plan whose file ownership overlaps defeats the entire premise, so it is
            # rejected rather than patched. The caller retries or deadlocks.
            log.error("plan rejected: %s", "; ".join(problems))
            return None

        return plan


def validate_ownership(plan: Workplan, agent_ids: list[str]) -> list[str]:
    """Structural checks the model is not trusted to have got right.

    Returns a list of human-readable problems; empty means the plan is sound.
    """
    problems: list[str] = []

    seen: dict[str, str] = {}
    for task in plan.tasks:
        if task.owner_agent not in agent_ids:
            problems.append(
                f"task {task.task_id} assigned to unknown agent {task.owner_agent!r}"
            )
        for path in task.files_owned:
            if path in seen and seen[path] != task.task_id:
                problems.append(
                    f"{path} is claimed by both {seen[path]} and {task.task_id}"
                )
            seen[path] = task.task_id

    task_ids = {t.task_id for t in plan.tasks}
    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                problems.append(f"task {task.task_id} depends on unknown task {dep!r}")

    if _has_cycle(plan):
        problems.append("task dependency graph contains a cycle")

    for contract in plan.interface_contracts:
        if not contract.signature.strip() or "TBD" in contract.signature.upper():
            problems.append(
                f"contract {contract.contract_id} ({contract.name}) has no real signature"
            )

    return problems


def _has_cycle(plan: Workplan) -> bool:
    graph = {t.task_id: list(t.depends_on) for t in plan.tasks}
    state: dict[str, int] = {}  # 0 unvisited, 1 in progress, 2 done

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        for dep in graph.get(node, []):
            if dep in graph and visit(dep):
                return True
        state[node] = 2
        return False

    return any(visit(node) for node in graph)
