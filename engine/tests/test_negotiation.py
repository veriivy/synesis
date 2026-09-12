"""Control-flow tests for the negotiation loop. No network, no keys."""

from __future__ import annotations

import pytest

from engine.config import Settings
from engine.moderator import Moderator
from engine.negotiation import Negotiation
from engine.schemas import RepoRef

from conftest import ScriptedClient, plan_json, verdict_json

pytestmark = pytest.mark.asyncio


def build(specs, *, moderator_script, agent_script=("position",), max_rounds=3):
    """Wire a negotiation with scripted agents and a scripted moderator."""
    clients = {
        s.agent_id: ScriptedClient(provider_key=s.provider, script=list(agent_script))
        for s in specs
    }
    mod_client = ScriptedClient(provider_key="anthropic", script=list(moderator_script))
    return (
        Negotiation(
            room_id="room_test",
            specs=specs,
            feature="Add authentication.",
            repo_map="src/\n  app.py",
            repo=RepoRef(url="https://example.test/repo", commit="abc123"),
            settings=Settings(
                enabled_agents=["anthropic", "openai"], max_rounds=max_rounds
            ),
            clients=clients,
            moderator=Moderator(mod_client, max_rounds=max_rounds),
        ),
        clients,
        mod_client,
    )


async def collect(negotiation):
    return [event async for event in negotiation.run()]


async def test_round_zero_runs_and_emits_one_message_per_agent(specs):
    negotiation, _, _ = build(
        specs, moderator_script=[verdict_json(converged=True), plan_json()]
    )
    events = await collect(negotiation)

    opening = [e for e in events if e.type == "agent_message" and e.round == 0]
    assert {e.agent_id for e in opening} == {"claude", "gpt"}
    assert [e.type for e in events[:3]] == [
        "agent_message",
        "agent_message",
        "round_complete",
    ]


async def test_converged_on_round_zero_proposes_a_plan(specs):
    negotiation, _, _ = build(
        specs, moderator_script=[verdict_json(converged=True), plan_json()]
    )
    events = await collect(negotiation)

    assert events[-1].type == "plan_proposed"
    assert negotiation.result.succeeded
    plan = events[-1].plan
    assert plan.status == "proposed"
    assert plan.repo.commit == "abc123", "the plan must carry the repo it was built against"
    assert plan.files_owned_by("claude") == {"src/auth/tokens.py"}
    assert plan.files_owned_by("gpt") == {"src/auth/middleware.py"}


async def test_premature_convergence_forces_another_round(specs):
    # Round 0: the moderator says they sound agreed but nobody conceded.
    # Round 1: genuinely converged. Then the plan.
    negotiation, _, _ = build(
        specs,
        moderator_script=[
            verdict_json(converged=True, premature=True, note="nobody conceded"),
            verdict_json(converged=True),
            plan_json(),
        ],
    )
    events = await collect(negotiation)

    rounds = sorted({e.round for e in events if e.type == "round_complete"})
    assert rounds == [0, 1], "premature convergence must not end the negotiation"
    assert events[-1].type == "plan_proposed"


async def test_premature_nudge_reaches_the_agents(specs):
    negotiation, clients, _ = build(
        specs,
        moderator_script=[
            verdict_json(converged=True, premature=True),
            verdict_json(converged=True),
            plan_json(),
        ],
    )
    await collect(negotiation)

    round_one_prompt = clients["claude"].calls[1]["messages"][0].content
    assert "PREMATURE CONVERGENCE" in round_one_prompt


async def test_unresolved_conflict_at_the_round_cap_deadlocks(specs):
    conflict = {
        "issue": "r1 vs r4",
        "positions": {"claude": "r1 is must-have", "gpt": "r4 is must-have"},
    }
    negotiation, _, _ = build(
        specs,
        moderator_script=[verdict_json(conflicts_remaining=[conflict])],
        max_rounds=2,
    )
    events = await collect(negotiation)

    deadlock = events[-1]
    assert deadlock.type == "deadlock"
    assert deadlock.round == 2, "deadlock is reported at the round cap"
    assert deadlock.unresolved[0].positions == {
        "claude": "r1 is must-have",
        "gpt": "r4 is must-have",
    }
    assert not negotiation.result.succeeded


async def test_overlapping_file_ownership_is_rejected_not_shipped(specs):
    """Two tasks claiming one file defeats the whole premise, so it deadlocks."""
    overlapping = plan_json(
        tasks=[
            {
                "task_id": "t1",
                "title": "a",
                "description": "",
                "owner_agent": "claude",
                "owner_user": "u1",
                "files_owned": ["src/auth/tokens.py"],
                "depends_on": [],
            },
            {
                "task_id": "t2",
                "title": "b",
                "description": "",
                "owner_agent": "gpt",
                "owner_user": "u2",
                "files_owned": ["src/auth/tokens.py"],
                "depends_on": [],
            },
        ]
    )
    negotiation, _, _ = build(
        specs, moderator_script=[verdict_json(converged=True), overlapping]
    )
    events = await collect(negotiation)

    assert events[-1].type == "deadlock"
    assert not negotiation.result.succeeded


async def test_malformed_moderator_json_does_not_crash_the_run(specs):
    negotiation, _, _ = build(
        specs,
        moderator_script=["I'm sorry, I can't do that.", verdict_json(converged=True), plan_json()],
        max_rounds=2,
    )
    events = await collect(negotiation)

    # Unparseable verdict reads as "not converged", so the loop simply continues.
    assert any(e.type == "round_complete" and e.round == 1 for e in events)
    assert events[-1].type == "plan_proposed"


async def test_agents_do_not_see_each_other_within_a_round(specs):
    """Round N prompts must be built from round N-1's transcript only.

    If an agent could read its counterpart's message from the same round, whoever spoke
    last would just agree — the exact failure this product exists to prevent.
    """
    negotiation, clients, _ = build(
        specs,
        moderator_script=[verdict_json(), verdict_json(converged=True), plan_json()],
        agent_script=["MY-OWN-POSITION-MARKER"],
        max_rounds=2,
    )
    await collect(negotiation)

    round_zero_prompt = clients["gpt"].calls[0]["messages"][0].content
    assert "MY-OWN-POSITION-MARKER" not in round_zero_prompt

    round_one_prompt = clients["gpt"].calls[1]["messages"][0].content
    assert "MY-OWN-POSITION-MARKER" in round_one_prompt, "round 1 must see round 0"


async def test_must_haves_are_named_in_the_system_prompt(specs):
    negotiation, clients, _ = build(
        specs, moderator_script=[verdict_json(converged=True), plan_json()]
    )
    await collect(negotiation)

    system = clients["claude"].calls[0]["system"]
    assert "r1" in system and "r2" in system
    assert "may NOT concede" in system
    assert "u1" in system


async def test_a_single_agent_is_not_a_negotiation(specs):
    negotiation, _, _ = build(
        specs[:1], moderator_script=[verdict_json(converged=True)]
    )
    with pytest.raises(ValueError, match="at least two agents"):
        await collect(negotiation)
