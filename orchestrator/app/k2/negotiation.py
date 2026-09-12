"""The K2 round loop — CLAUDE.md's "### Sequence" steps 2-4.

Generates both PoAs in parallel, runs up to MAX_ROUNDS of
analyze -> both agents reply -> reconcile, then writes a FinalPlan with a
resolutions entry per blocking issue (resolved or not — an issue both
agents held through the round cap still gets an entry, just an honest
"unresolved" one). Ticket decomposition (steps 5/6) is a separate call
from main.py triggered by the second approval, not run automatically here.

Bookkeeping limitation, worth knowing before trusting this against a real
model: resolved_paths tracking (so a settled issue doesn't just get
re-flagged with a new id next round) is done by parsing "Both plans write
<path>" back out of Difference.topic — a convention TemplateAnalysisProvider
always follows, but the frozen contract's Difference schema has no
structured path field, so a real K2 phrasing its topic differently would
lose that de-duplication. It would still converge or hit the round cap
correctly; it just might re-litigate the same conflict under a new
issue_id instead of recognizing it as already settled.
"""

from __future__ import annotations

import asyncio

from ..config import get_agent_poa_provider, get_agent_reply_provider, get_k2_analysis_provider
from ..events import (
    AgentMessage,
    AnalysisEvent,
    ModeratorMessage,
    PlanProposed,
    RoundComplete,
)
from ..events import PoaGenerated as PoaGeneratedEvent
from ..rooms import Room
from ..schemas import Difference, FinalPlan, PoA, Resolution, Step
from .analysis import analyze
from .poa import generate_poa
from .reply import AgentReply, respond_to_analysis

MAX_ROUNDS = 3
AGENT_A, AGENT_B = "a1", "a2"


def _path_from_topic(topic: str) -> str | None:
    prefix = "Both plans write "
    return topic[len(prefix) :] if topic.startswith(prefix) else None


def _reconcile(
    diff: Difference, reply_a: AgentReply, reply_b: AgentReply, agent_a_id: str, agent_b_id: str
) -> tuple[Resolution, str | None] | None:
    """Returns (resolution, conceding_agent_id | None) or None if both held."""
    stance_a = reply_a.stances.get(diff.issue_id)
    stance_b = reply_b.stances.get(diff.issue_id)

    if stance_a == "concede":
        return (
            Resolution(
                issue_id=diff.issue_id,
                outcome=f"{agent_b_id} keeps the disputed path; {agent_a_id} does not touch it.",
                rationale=reply_a.content,
            ),
            agent_a_id,
        )
    if stance_b == "concede":
        return (
            Resolution(
                issue_id=diff.issue_id,
                outcome=f"{agent_a_id} keeps the disputed path; {agent_b_id} does not touch it.",
                rationale=reply_b.content,
            ),
            agent_b_id,
        )
    if stance_a == "compromise":
        return (
            Resolution(issue_id=diff.issue_id, outcome=reply_a.content, rationale=f"compromise proposed by {agent_a_id}"),
            None,
        )
    if stance_b == "compromise":
        return (
            Resolution(issue_id=diff.issue_id, outcome=reply_b.content, rationale=f"compromise proposed by {agent_b_id}"),
            None,
        )
    return None


def _merge_steps(poa: PoA, conceded_paths: set[str]) -> list[Step]:
    """Drop any step whose files_touched were entirely conceded away."""
    return [s for s in poa.steps if not (set(s.files_touched) & conceded_paths)]


def _build_final_plan(
    room_id: str,
    poa1: PoA,
    poa2: PoA,
    conceded_by_agent: dict[str, set[str]],
    resolutions: list[Resolution],
    rounds_used: int,
) -> tuple[FinalPlan, dict[str, str]]:
    kept1 = _merge_steps(poa1, conceded_by_agent.get(AGENT_A, set()))
    kept2 = _merge_steps(poa2, conceded_by_agent.get(AGENT_B, set()))

    steps: list[Step] = []
    step_owner: dict[str, str] = {}
    for i, (owner, step) in enumerate(
        [(AGENT_A, s) for s in kept1] + [(AGENT_B, s) for s in kept2], start=1
    ):
        new_id = f"s{i}"
        step_owner[new_id] = owner
        steps.append(step.model_copy(update={"step_id": new_id}))

    plan = FinalPlan(
        plan_id=f"plan_{room_id}",
        rounds_used=rounds_used,
        summary=(
            f"Merged plan from {poa1.agent_id} ({poa1.user_id}) and "
            f"{poa2.agent_id} ({poa2.user_id}) after {rounds_used} round(s); "
            f"{len(resolutions)} issue(s) resolved or recorded as unresolved."
        ),
        steps=steps,
        resolutions=resolutions,
        approvals={poa1.user_id: False, poa2.user_id: False},
        status="proposed",
    )
    return plan, step_owner


async def run_negotiation(room: Room) -> None:
    """Mutates `room` in place and publishes every event onto room.bus.
    Callers (main.py) are responsible for calling this at most once per
    room — see Room.negotiate_started.
    """
    user_ids = list(room.participants.keys())
    if len(user_ids) != 2:
        raise ValueError(f"negotiation needs exactly 2 participants, got {len(user_ids)}")
    u1_id, u2_id = user_ids

    poa_a_provider, poa_a_model = get_agent_poa_provider(AGENT_A)
    poa_b_provider, poa_b_model = get_agent_poa_provider(AGENT_B)

    poa1, poa2 = await asyncio.gather(
        generate_poa(
            agent_id=AGENT_A,
            user_id=u1_id,
            tasks=room.tasks_by_user.get(u1_id, []),
            provider=poa_a_provider,
            model=poa_a_model,
        ),
        generate_poa(
            agent_id=AGENT_B,
            user_id=u2_id,
            tasks=room.tasks_by_user.get(u2_id, []),
            provider=poa_b_provider,
            model=poa_b_model,
        ),
    )
    room.poas = {AGENT_A: poa1, AGENT_B: poa2}
    room.participants[u1_id].agent_id = AGENT_A
    room.participants[u2_id].agent_id = AGENT_B
    room.status = "negotiating"

    await room.bus.publish(
        PoaGeneratedEvent(room_id=room.room_id, agent_id=AGENT_A, user_id=u1_id, poa=poa1)
    )
    await room.bus.publish(
        PoaGeneratedEvent(room_id=room.room_id, agent_id=AGENT_B, user_id=u2_id, poa=poa2)
    )

    analysis_provider, analysis_model = get_k2_analysis_provider()
    reply_a_provider, reply_a_model = get_agent_reply_provider(AGENT_A)
    reply_b_provider, reply_b_model = get_agent_reply_provider(AGENT_B)

    resolved_paths: set[str] = set()
    conceded_by_agent: dict[str, set[str]] = {AGENT_A: set(), AGENT_B: set()}
    resolutions: list[Resolution] = []
    latest_analysis = None
    round_ = 0
    forced_probe_used = False

    while round_ < MAX_ROUNDS:
        round_ += 1
        # Step 3a: drain mid-loop interjections. POST /rooms/{id}/messages
        # already published the user_message event synchronously when it
        # arrived; this only clears the queue so it doesn't leak forward.
        room.pending_messages.clear()

        analysis = await analyze(
            poa_a=poa1,
            poa_b=poa2,
            round_=round_,
            resolved_paths=resolved_paths,
            provider=analysis_provider,
            model=analysis_model,
        )
        latest_analysis = analysis
        room.current_round = round_
        room.latest_analysis = analysis
        await room.bus.publish(
            AnalysisEvent(
                room_id=room.room_id,
                round=round_,
                similarities=analysis.similarities,
                differences=analysis.differences,
                converged=analysis.converged,
            )
        )

        blocking = [d for d in analysis.differences if d.severity == "blocking"]

        if not blocking:
            if round_ == 1 and not forced_probe_used:
                forced_probe_used = True
                await room.bus.publish(
                    ModeratorMessage(
                        room_id=room.room_id,
                        round=round_,
                        content=(
                            "No blocking differences on round 1 — running one forced "
                            "probe round before declaring convergence."
                        ),
                    )
                )
                await room.bus.publish(RoundComplete(room_id=room.room_id, round=round_))
                continue
            await room.bus.publish(RoundComplete(room_id=room.room_id, round=round_))
            break

        reply_a, reply_b = await asyncio.gather(
            respond_to_analysis(
                agent_id=AGENT_A,
                other_agent_id=AGENT_B,
                my_poa=poa1,
                differences=blocking,
                provider=reply_a_provider,
                model=reply_a_model,
            ),
            respond_to_analysis(
                agent_id=AGENT_B,
                other_agent_id=AGENT_A,
                my_poa=poa2,
                differences=blocking,
                provider=reply_b_provider,
                model=reply_b_model,
            ),
        )
        for reply, agent_id in ((reply_a, AGENT_A), (reply_b, AGENT_B)):
            await room.bus.publish(
                AgentMessage(
                    room_id=room.room_id,
                    round=round_,
                    agent_id=agent_id,
                    content=reply.content,
                    addresses_issues=reply.addresses_issues,
                )
            )

        for diff in blocking:
            outcome = _reconcile(diff, reply_a, reply_b, AGENT_A, AGENT_B)
            if outcome is None:
                continue
            resolution, conceding_agent = outcome
            resolutions.append(resolution)
            path = _path_from_topic(diff.topic)
            if path:
                resolved_paths.add(path)
                if conceding_agent:
                    conceded_by_agent[conceding_agent].add(path)

        await room.bus.publish(RoundComplete(room_id=room.room_id, round=round_))

    resolved_issue_ids = {r.issue_id for r in resolutions}
    if latest_analysis:
        for diff in latest_analysis.differences:
            if diff.severity == "blocking" and diff.issue_id not in resolved_issue_ids:
                resolutions.append(
                    Resolution(
                        issue_id=diff.issue_id,
                        outcome=(
                            f"Unresolved after {round_} round(s) — both sides held. "
                            "Needs an explicit user decision."
                        ),
                        rationale=f"Final positions: {diff.positions}",
                    )
                )

    plan, step_owner = _build_final_plan(
        room.room_id, poa1, poa2, conceded_by_agent, resolutions, rounds_used=round_
    )
    room.plan = plan
    room.step_owner = step_owner
    room.status = "awaiting_approval"
    await room.bus.publish(PlanProposed(room_id=room.room_id, plan=plan))
