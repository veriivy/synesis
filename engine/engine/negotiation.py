"""The negotiation loop.

Round 0 opening positions, then up to MAX_ROUNDS of argument, with the moderator
judging after each. Converge -> workplan. Round cap with conflicts left -> deadlock,
escalated to the humans.

The loop is an async generator of contract events. It does not know about HTTP, SSE,
or Mongo — the server subscribes and forwards. That keeps the engine testable without
a server and the server demoable without live agents.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from . import prompts
from .config import PROVIDERS, Settings
from .providers import LLMClient, Message, ProviderError, build_client
from .schemas import (
    AgentMessageEvent,
    AgentSpec,
    DeadlockEvent,
    Event,
    ModeratorVerdict,
    PlanProposedEvent,
    RepoRef,
    RoundCompleteEvent,
    UnresolvedIssue,
    Workplan,
)
from .moderator import Moderator

log = logging.getLogger(__name__)


@dataclass
class Turn:
    round: int
    agent_id: str
    user_id: str
    content: str


@dataclass
class NegotiationResult:
    plan: Workplan | None = None
    deadlock: list[UnresolvedIssue] = field(default_factory=list)
    rounds_used: int = 0
    transcript: list[Turn] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.plan is not None


def render_transcript(turns: list[Turn]) -> str:
    if not turns:
        return "(nothing said yet)"
    return "\n\n".join(
        f"--- round {t.round} · {t.agent_id} (advocating for {t.user_id}) ---\n{t.content}"
        for t in turns
    )


def _render_conflicts(conflicts: list[UnresolvedIssue]) -> str:
    lines = []
    for i, c in enumerate(conflicts, 1):
        lines.append(f"{i}. {c.issue}")
        lines.extend(f"   - {agent}: {pos}" for agent, pos in c.positions.items())
    return "\n".join(lines)


class Negotiation:
    """One room's negotiation.

    Agents are constructed from AgentSpecs; a spec whose provider has no API key is
    dropped with a warning rather than killing the room. Two agents is the floor —
    below that there is nothing to negotiate.
    """

    def __init__(
        self,
        *,
        room_id: str,
        specs: list[AgentSpec],
        feature: str,
        repo_map: str = "",
        repo: RepoRef | None = None,
        settings: Settings | None = None,
        clients: dict[str, LLMClient] | None = None,
        moderator: Moderator | None = None,
    ) -> None:
        self.room_id = room_id
        self.feature = feature
        self.repo_map = repo_map
        self.repo = repo or RepoRef()
        self.settings = settings or Settings.from_env()

        # `clients` is injected by tests with fakes; in production it is built here.
        self.clients: dict[str, LLMClient] = clients or {}
        self.specs: list[AgentSpec] = []
        for spec in specs:
            if spec.agent_id in self.clients:
                self.specs.append(spec)
                continue
            try:
                self.clients[spec.agent_id] = build_client(spec.provider, self.settings)
            except (ProviderError, ValueError) as exc:
                log.error("dropping agent %s: %s", spec.agent_id, exc)
                continue
            self.specs.append(spec)

        if moderator is not None:
            self.moderator = moderator
        else:
            mod_client = build_client(self.settings.moderator_provider, self.settings)
            self.moderator = Moderator(mod_client, max_rounds=self.settings.max_rounds)

        self.turns: list[Turn] = []
        self.result = NegotiationResult()

    # --- one agent's turn ----------------------------------------------------

    async def _speak(self, spec: AgentSpec, round_index: int, user_prompt: str) -> Turn:
        label = PROVIDERS[spec.provider].label if spec.provider in PROVIDERS else spec.agent_id
        system = prompts.build_advocate_system(spec, self.repo_map, label)
        client = self.clients[spec.agent_id]
        try:
            completion = await client.complete(
                system=system,
                messages=[Message(role="user", content=user_prompt)],
                max_tokens=1500,
            )
            content = completion.text.strip()
        except ProviderError as exc:
            # A dead provider must not take the round down. The agent visibly passes,
            # which the moderator will read as an unresolved position.
            log.error("agent %s failed in round %d: %s", spec.agent_id, round_index, exc)
            content = f"[{spec.agent_id} could not respond this round: {exc}]"

        return Turn(
            round=round_index,
            agent_id=spec.agent_id,
            user_id=spec.user_id,
            content=content or "[empty response]",
        )

    async def _run_round(self, round_index: int, user_prompts: dict[str, str]) -> list[Turn]:
        """All agents speak concurrently on the same prior transcript.

        Concurrent rather than sequential so no agent gets to see and react to another
        agent's message within the same round — otherwise whoever goes last simply
        agrees with whoever went first, which is the exact failure mode we are guarding
        against.
        """
        turns = await asyncio.gather(
            *(self._speak(s, round_index, user_prompts[s.agent_id]) for s in self.specs)
        )
        return list(turns)

    # --- the loop ------------------------------------------------------------

    async def run(self) -> AsyncIterator[Event]:
        if len(self.specs) < 2:
            raise ValueError(
                f"a negotiation needs at least two agents, got {len(self.specs)}. "
                "Check that the API keys for ENABLED_AGENTS are set."
            )

        # --- round 0: opening positions --------------------------------------
        opening = prompts.ROUND_ZERO_USER.format(feature=self.feature)
        turns = await self._run_round(0, {s.agent_id: opening for s in self.specs})
        self.turns.extend(turns)
        for turn in turns:
            yield AgentMessageEvent(
                room_id=self.room_id,
                round=0,
                agent_id=turn.agent_id,
                user_id=turn.user_id,
                content=turn.content,
            )
        yield RoundCompleteEvent(room_id=self.room_id, round=0)

        verdict = await self.moderator.judge(
            round_index=0, transcript=render_transcript(self.turns), specs=self.specs
        )
        log.info("round 0 verdict: converged=%s premature=%s", verdict.converged, verdict.premature)

        # --- rounds 1..N -----------------------------------------------------
        rounds_used = 0
        for round_index in range(1, self.settings.max_rounds + 1):
            if verdict.converged:
                break
            rounds_used = round_index

            conflicts_block = ""
            if verdict.conflicts_remaining:
                conflicts_block = prompts.CONFLICTS_BLOCK.format(
                    conflicts=_render_conflicts(verdict.conflicts_remaining)
                )
            if verdict.premature:
                conflicts_block = f"{prompts.PREMATURE_NUDGE}\n\n{conflicts_block}"

            transcript = render_transcript(self.turns)
            base = prompts.ROUND_N_USER.format(
                round=round_index,
                max_rounds=self.settings.max_rounds,
                transcript=transcript,
                conflicts_block=conflicts_block,
            )

            turns = await self._run_round(
                round_index, {s.agent_id: base for s in self.specs}
            )
            self.turns.extend(turns)
            for turn in turns:
                yield AgentMessageEvent(
                    room_id=self.room_id,
                    round=round_index,
                    agent_id=turn.agent_id,
                    user_id=turn.user_id,
                    content=turn.content,
                )
            yield RoundCompleteEvent(room_id=self.room_id, round=round_index)

            verdict = await self.moderator.judge(
                round_index=round_index,
                transcript=render_transcript(self.turns),
                specs=self.specs,
            )
            log.info(
                "round %d verdict: converged=%s premature=%s conflicts=%d",
                round_index,
                verdict.converged,
                verdict.premature,
                len(verdict.conflicts_remaining),
            )

        self.result.rounds_used = rounds_used
        self.result.transcript = list(self.turns)

        # --- outcome ---------------------------------------------------------
        async for event in self._finish(verdict, rounds_used):
            yield event

    async def _finish(
        self, verdict: ModeratorVerdict, rounds_used: int
    ) -> AsyncIterator[Event]:
        """Convergence -> plan. Round cap with conflicts -> deadlock."""
        if not verdict.converged and verdict.conflicts_remaining:
            self.result.deadlock = verdict.conflicts_remaining
            yield DeadlockEvent(
                room_id=self.room_id,
                round=rounds_used,
                unresolved=verdict.conflicts_remaining,
            )
            return

        plan = await self.moderator.synthesise_plan(
            transcript=render_transcript(self.turns),
            specs=self.specs,
            rounds_used=rounds_used,
            repo_map=self.repo_map,
            repo=self.repo,
        )

        if plan is None:
            # The agents agreed but the plan would not validate — overlapping file
            # ownership, a missing signature, a cycle. That is a real unresolved
            # disagreement dressed up as agreement, so it escalates like one.
            issue = UnresolvedIssue(
                issue=(
                    "The agents reached agreement in conversation, but no valid workplan "
                    "could be derived from it — file ownership, interface signatures, or "
                    "task dependencies did not come out consistent. Something was agreed "
                    "in words but not in substance."
                ),
                positions={t.agent_id: t.content[:400] for t in self.turns[-len(self.specs):]},
            )
            self.result.deadlock = [issue]
            yield DeadlockEvent(
                room_id=self.room_id, round=rounds_used, unresolved=[issue]
            )
            return

        self.result.plan = plan
        yield PlanProposedEvent(room_id=self.room_id, plan=plan)
