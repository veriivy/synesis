"""Room governance: the fixed north-star goal (goal.py) and per-person
influence weights (influence.py).

What's pinned here, roughly in the order a room meets it:
  - the goal's anchor strength fades draft -> rounds -> execute, never to 0,
    and the prompt block shrinks with it
  - weights become shares, an advocate stance, and a deadlock tiebreak that
    a clear lead wins and a close call (or a goal-backed underdog) doesn't
  - only the room's creator (admin_token) can set either, and both freeze
    the moment negotiation starts
  - through the real app: every stage gets the goal at its own strength,
    K2 never sees the weights, a weighted default still needs both approvals,
    and the coder is told the outcome

LLM calls are faked at the names main.py imported them under, same as
test_end_to_end.py — no keys, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

import orchestrator.execution as execution_module
import orchestrator.k2 as k2_module
import orchestrator.main as main_module
from orchestrator.coding import TicketCode, implement_ticket_sync
from orchestrator.goal import ANCHOR_FLOOR, Goal, anchor_strength, goal_block
from orchestrator.influence import shares, stance_block, weigh_deadlock
from orchestrator.rooms import Room, room_snapshot
from orchestrator.schemas import FinalPlan, Resolution, Step, Ticket

GOAL = {
    "statement": "Ship login that works for both the browser app and the mobile app.",
    "success_criteria": ["Mobile clients can authenticate without cookies", "No external identity service"],
}


# --- goal.py ---------------------------------------------------------------


def test_anchor_strength_fades_from_draft_to_execute_but_never_reaches_zero():
    curve = [
        anchor_strength("draft"),
        *(anchor_strength("negotiate", r, 3) for r in (1, 2, 3)),
        anchor_strength("execute"),
    ]

    assert curve == [1.0, 0.88, 0.7, 0.52, ANCHOR_FLOOR]
    assert all(a >= b for a, b in zip(curve, curve[1:]))
    assert ANCHOR_FLOOR > 0


def test_anchor_strength_clamps_rounds_and_handles_a_single_round_cap():
    assert anchor_strength("negotiate", 1, 1) == anchor_strength("negotiate", 1, 3)
    assert anchor_strength("negotiate", 99, 3) == anchor_strength("negotiate", 3, 3)


def test_goal_block_carries_less_of_the_goal_as_it_fades():
    goal = Goal(**GOAL)
    criterion = GOAL["success_criteria"][0]

    primary = goal_block(goal, 0.88)
    reference = goal_block(goal, 0.6)
    background = goal_block(goal, 0.2)

    for block in (primary, reference, background):
        assert goal.statement in block
        assert goal.fingerprint in block
    assert "primary" in primary and criterion in primary
    assert "tiebreaker" in reference and criterion in reference
    assert "sanity check" in background and criterion not in background
    assert len(primary) > len(background)
    assert goal_block(None, 1.0) == ""


def test_fingerprint_is_stable_and_tracks_the_exact_text():
    a = Goal(**GOAL)
    b = Goal(statement="  " + GOAL["statement"] + "  ", success_criteria=GOAL["success_criteria"] + [" "])
    c = Goal(statement=GOAL["statement"], success_criteria=GOAL["success_criteria"][:1])

    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint


def test_a_blank_goal_is_rejected():
    with pytest.raises(ValidationError):
        Goal(statement="   ")


# --- influence.py ----------------------------------------------------------


def test_shares_are_relative_and_default_missing_users_to_one():
    assert shares({"u1": 2.0}, ["u1", "u2"]) == pytest.approx({"u1": 2 / 3, "u2": 1 / 3})
    assert shares({"u1": 10.0, "u2": 5.0}, ["u1", "u2"]) == shares({"u1": 2.0}, ["u1", "u2"])
    assert shares({}, ["u1", "u2"]) == {"u1": 0.5, "u2": 0.5}
    assert shares({}, []) == {}


def test_stance_leans_with_the_share_but_never_licenses_dropping_a_must():
    larger = stance_block("Avery", 2 / 3, 2)
    smaller = stance_block("Blair", 1 / 3, 2)
    even = stance_block("Casey", 0.52, 2)

    assert "67%" in larger and "larger say" in larger and "not a veto" in larger
    assert "33%" in smaller and "smaller say" in smaller and "never means dropping a must" in smaller
    assert "Influence is even" in even


def _diff(goal_favors=None) -> dict:
    return {
        "issue_id": "d1",
        "topic": "auth mechanism",
        "positions": {"a1": "session cookies", "a2": "bearer JWT"},
        "severity": "blocking",
        "goal_favors": goal_favors,
    }


@pytest.mark.parametrize(
    ("weights", "goal_favors", "winner"),
    [
        ((1, 1), None, None),  # even, no goal: the humans' call, exactly as before
        ((1, 1), "a2", "a2"),  # even: the goal breaks the tie
        ((2, 1), None, "a1"),  # a clear lead carries a deadlock
        ((3, 2), "a2", None),  # a modest lead vs the goal: level, back to the humans
        ((2, 1), "a2", None),  # 2:1 still can't steamroll a goal-backed position
        ((3, 1), "a2", "a1"),  # a dominant lead can — it's a default, approval still gates it
        ((1, 1), "a9", None),  # a goal_favors that names no position is ignored
    ],
)
def test_weigh_deadlock(weights, goal_favors, winner):
    agent_shares = shares({"a1": weights[0], "a2": weights[1]}, ["a1", "a2"])

    verdict = weigh_deadlock(_diff(goal_favors), agent_shares)

    assert verdict.winner == winner


# --- K2 sees the goal, not the weights ---------------------------------------


def test_k2_gets_the_goal_and_sanitizes_goal_favors(monkeypatch):
    prompts: list[str] = []

    def fake_chat(*, system, user, provider="ifm", max_tokens=2048, api_key=None):
        prompts.append(user)
        return json.dumps(
            {
                "round": 1,
                "similarities": [],
                "differences": [
                    {**_diff("a1"), "issue_id": "d1"},
                    {**_diff("nobody"), "issue_id": "d2"},
                ],
                "converged": False,
            }
        )

    monkeypatch.setattr(k2_module, "chat", fake_chat)
    block = goal_block(Goal(**GOAL), 0.88)

    analysis = k2_module.analyze(context={}, tasks={}, poa1={}, poa2={}, goal_block=block)

    assert block in prompts[0] and "goal_favors" in prompts[0]
    assert [d["goal_favors"] for d in analysis["differences"]] == ["a1", None]


def test_k2_prompt_is_unchanged_for_a_room_without_a_goal(monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr(
        k2_module,
        "chat",
        lambda **kw: prompts.append(kw["user"]) or '{"differences":[],"converged":true}',
    )

    k2_module.analyze(context={}, tasks={}, poa1={}, poa2={})

    assert "NORTH STAR" not in prompts[0] and "goal_favors" not in prompts[0]


# --- the API: admin-only, frozen once negotiation starts ----------------------


def _poa(agent_id: str, user_id: str) -> dict:
    return {
        "poa_id": f"poa_{agent_id}",
        "agent_id": agent_id,
        "user_id": user_id,
        "summary": f"{agent_id}'s auth plan",
        "steps": [
            {
                "step_id": "s1",
                "title": f"{agent_id} auth",
                "description": "auth",
                "files_touched": ["src/auth.py"],
                "rationale": "must-have",
            }
        ],
        "assumptions": [],
    }


class Recorder:
    """Fakes for draft_poa / analyze / revise_poa that record what each
    stage was steered with, and deadlock on one blocking issue forever."""

    def __init__(self, goal_favors: str | None = None) -> None:
        self.goal_favors = goal_favors
        self.drafts: list[dict] = []
        self.analyses: list[dict] = []
        self.revisions: list[dict] = []

    def draft_poa(
        self, *, agent_id, user_id, tasks, provider=None, api_key=None, goal_block="", stance_block="",
        project_context="",
    ):
        self.drafts.append({"agent_id": agent_id, "goal_block": goal_block, "stance_block": stance_block})
        return _poa(agent_id, user_id)

    def analyze(self, *, context, tasks, poa1, poa2, round_index, transcript, goal_block="", interjections=""):
        self.analyses.append({"round": round_index, "goal_block": goal_block})
        return {
            "round": round_index,
            "similarities": [],
            "differences": [_diff(self.goal_favors)],
            "converged": False,
        }

    def revise_poa(
        self, *, poa, tasks, analysis, round_index, provider=None, api_key=None, goal_block="", stance_block="",
        interjections="", project_context="",
    ):
        self.revisions.append(
            {"agent_id": poa["agent_id"], "round": round_index, "goal_block": goal_block, "stance_block": stance_block}
        )
        return {
            "agent_id": poa["agent_id"],
            "user_id": poa["user_id"],
            "content": "d1: HOLD",
            "addresses_issues": ["d1"],
            "poa": poa,
        }


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(main_module, "draft_poa", rec.draft_poa)
    monkeypatch.setattr(main_module, "analyze", rec.analyze)
    monkeypatch.setattr(main_module, "revise_poa", rec.revise_poa)
    monkeypatch.setattr(main_module, "MAX_ROUNDS", 3)
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", False)
    return rec


@pytest.fixture
async def client():
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup_room(
    client: AsyncClient, *, goal: dict | None = None, weights: dict | None = None
) -> tuple[str, str, dict[str, str]]:
    created = (await client.post("/rooms")).json()
    room_id, admin = created["room_id"], created["admin_token"]
    if goal is not None:
        r = await client.post(f"/rooms/{room_id}/goal", json={**goal, "admin_token": admin})
        assert r.status_code == 200, r.text
    if weights is not None:
        r = await client.post(f"/rooms/{room_id}/weights", json={"weights": weights, "admin_token": admin})
        assert r.status_code == 200, r.text
    tokens: dict[str, str] = {}
    for user_id, name in (("u1", "Avery"), ("u2", "Blair")):
        r = await client.post(
            f"/rooms/{room_id}/participants",
            json={"user_id": user_id, "display_name": name, "provider": "claude", "model": "m"},
        )
        tokens[user_id] = r.json()["participant_token"]
        await client.post(
            f"/rooms/{room_id}/tasks",
            json={
                "user_id": user_id,
                "tasks": [{"text": f"{name}'s auth", "priority": "must"}],
                "participant_token": tokens[user_id],
            },
        )
    return room_id, admin, tokens


async def _negotiate(client: AsyncClient, room_id: str) -> Room:
    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 202, r.text
    room = main_module.rooms.get(room_id)
    await asyncio.wait_for(room.task, timeout=5)
    return room


@pytest.mark.asyncio
async def test_only_the_room_creator_can_set_the_goal(client: AsyncClient):
    created = (await client.post("/rooms")).json()
    room_id, admin = created["room_id"], created["admin_token"]
    join = await client.post(
        f"/rooms/{room_id}/participants",
        json={"user_id": "u1", "display_name": "Avery", "provider": "claude", "model": "m"},
    )
    participant_token = join.json()["participant_token"]

    for token in (None, "wrong", participant_token):
        r = await client.post(f"/rooms/{room_id}/goal", json={**GOAL, "admin_token": token})
        assert r.status_code == 403, token

    r = await client.post(f"/rooms/{room_id}/goal", json={**GOAL, "admin_token": admin})
    assert r.status_code == 200, r.text
    fingerprint = Goal(**GOAL).fingerprint
    assert r.json()["goal"]["fingerprint"] == fingerprint
    events = [e for e in main_module.bus.history(room_id) if e["type"] == "goal_set"]
    assert events[-1]["goal"]["fingerprint"] == fingerprint


@pytest.mark.asyncio
async def test_weights_are_admin_only_and_bounded(client: AsyncClient):
    created = (await client.post("/rooms")).json()
    room_id, admin = created["room_id"], created["admin_token"]

    r = await client.post(f"/rooms/{room_id}/weights", json={"weights": {"u1": 2}})
    assert r.status_code == 403
    for bad in (0, -1, 10.5):
        r = await client.post(f"/rooms/{room_id}/weights", json={"weights": {"u1": bad}, "admin_token": admin})
        assert r.status_code == 422, bad

    # Weighting a user before they join is fine; a join then reports it.
    r = await client.post(f"/rooms/{room_id}/weights", json={"weights": {"u1": 3}, "admin_token": admin})
    assert r.status_code == 200
    assert r.json()["shares"] == {"u1": 1.0}
    await client.post(
        f"/rooms/{room_id}/participants",
        json={"user_id": "u2", "display_name": "Blair", "provider": "gpt", "model": "m"},
    )
    r = await client.post(f"/rooms/{room_id}/weights", json={"weights": {"u2": 1}, "admin_token": admin})
    assert r.json()["shares"] == pytest.approx({"u1": 0.75, "u2": 0.25})

    history = main_module.bus.history(room_id)
    assert [e for e in history if e["type"] == "weights_updated"][-1]["weights"] == {"u1": 3, "u2": 1}
    joined = [e for e in history if e["type"] == "participant_joined"]
    assert joined[-1]["weight"] == 1.0


@pytest.mark.asyncio
async def test_goal_and_weights_freeze_once_negotiation_starts(client: AsyncClient, recorder: Recorder):
    room_id, admin, _ = await _setup_room(client, goal=GOAL, weights={"u1": 2})
    # Still in setup: the goal can be corrected.
    revised = {**GOAL, "statement": GOAL["statement"] + " Offline-first."}
    r = await client.post(f"/rooms/{room_id}/goal", json={**revised, "admin_token": admin})
    assert r.status_code == 200

    room = await _negotiate(client, room_id)

    r = await client.post(f"/rooms/{room_id}/goal", json={**GOAL, "admin_token": admin})
    assert r.status_code == 409
    r = await client.post(f"/rooms/{room_id}/weights", json={"weights": {"u2": 10}, "admin_token": admin})
    assert r.status_code == 409
    assert room.goal.statement == revised["statement"]
    assert room.weights == {"u1": 2}


# --- the goal fades through the real loop; weights shape stance ------------------


@pytest.mark.asyncio
async def test_every_stage_gets_the_goal_at_its_own_strength(client: AsyncClient, recorder: Recorder):
    room_id, _, _ = await _setup_room(client, goal=GOAL, weights={"u1": 2, "u2": 1})
    await _negotiate(client, room_id)

    assert all("Anchor 1.00: primary" in d["goal_block"] for d in recorder.drafts)
    assert [a["round"] for a in recorder.analyses] == [1, 2, 3]
    assert "Anchor 0.88: primary" in recorder.analyses[0]["goal_block"]
    assert "Anchor 0.70: reference" in recorder.analyses[1]["goal_block"]
    assert "Anchor 0.52: reference" in recorder.analyses[2]["goal_block"]
    for revision in recorder.revisions:
        expected = recorder.analyses[revision["round"] - 1]["goal_block"]
        assert revision["goal_block"] == expected

    analysis_events = [e for e in main_module.bus.history(room_id) if e["type"] == "analysis"]
    assert [e["goal_anchor"] for e in analysis_events] == [0.88, 0.7, 0.52]


@pytest.mark.asyncio
async def test_weights_shape_the_advocates_but_never_reach_k2(client: AsyncClient, recorder: Recorder):
    room_id, _, _ = await _setup_room(client, goal=GOAL, weights={"u1": 2, "u2": 1})
    await _negotiate(client, room_id)

    stance = {d["agent_id"]: d["stance_block"] for d in recorder.drafts}
    assert "Avery holds 67%" in stance["a1"] and "larger say" in stance["a1"]
    assert "Blair holds 33%" in stance["a2"] and "smaller say" in stance["a2"]
    assert {r["agent_id"] for r in recorder.revisions} == {"a1", "a2"}
    assert all(r["stance_block"] == stance[r["agent_id"]] for r in recorder.revisions)
    assert all("INFLUENCE" not in a["goal_block"] and "67%" not in a["goal_block"] for a in recorder.analyses)


@pytest.mark.asyncio
async def test_a_room_without_goal_or_weights_prompts_as_before(client: AsyncClient, recorder: Recorder):
    room_id, _, _ = await _setup_room(client)
    room = await _negotiate(client, room_id)

    assert all(d["goal_block"] == "" and d["stance_block"] == "" for d in recorder.drafts)
    assert all(r["goal_block"] == "" and r["stance_block"] == "" for r in recorder.revisions)
    assert [e["goal_anchor"] for e in main_module.bus.history(room_id) if e["type"] == "analysis"] == [None] * 3
    # ...and an even deadlock is still the humans' call.
    assert room.plan.resolutions[0].outcome.startswith("Unresolved after 3 round(s)")


# --- deadlocks: weighted default, still gated by approval -----------------------


@pytest.mark.asyncio
async def test_a_weighted_default_still_needs_both_approvals(client: AsyncClient, recorder: Recorder):
    room_id, _, tokens = await _setup_room(client, weights={"u1": 2, "u2": 1})
    room = await _negotiate(client, room_id)

    (resolution,) = room.plan.resolutions
    assert resolution.outcome.startswith("Defaults to a1's position")
    assert "session cookies" in resolution.outcome
    assert "Blair can still reject" in resolution.rationale
    assert "1 defaulted by the influence tiebreak" in room.plan.summary

    # The heavier user approving alone does not push the plan through.
    await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": "u1", "approved": True, "participant_token": tokens["u1"]},
    )
    assert room.plan.status == "proposed"
    await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": "u2", "approved": False, "participant_token": tokens["u2"]},
    )
    assert room.plan.status == "proposed"
    assert not room.tickets


@pytest.mark.asyncio
async def test_the_goal_breaks_an_even_deadlock(client: AsyncClient, recorder: Recorder):
    recorder.goal_favors = "a2"
    room_id, _, _ = await _setup_room(client, goal=GOAL)
    room = await _negotiate(client, room_id)

    (resolution,) = room.plan.resolutions
    assert resolution.outcome.startswith("Defaults to a2's position")
    assert "a2 50% say + goal" in resolution.rationale


@pytest.mark.asyncio
async def test_the_goal_holds_off_a_two_to_one_weight(client: AsyncClient, recorder: Recorder):
    recorder.goal_favors = "a2"
    room_id, _, _ = await _setup_room(client, goal=GOAL, weights={"u1": 2, "u2": 1})
    room = await _negotiate(client, room_id)

    (resolution,) = room.plan.resolutions
    assert resolution.outcome.startswith("Unresolved")
    assert "too close to call" in resolution.rationale


# --- execution: the goal as a background check, the outcome as instructions -------


def _approved_room() -> tuple[Room, Ticket]:
    room = Room(room_id="r_exec")
    room.goal = Goal(**GOAL)
    ticket = Ticket(
        ticket_id="t1", plan_id="p", title="auth", description="build auth",
        assigned_agent="a1", files_owned=["src/auth.py"],
    )
    room.plan = FinalPlan(
        plan_id="p",
        rounds_used=3,
        summary="merged",
        steps=[Step(step_id="s1", title="auth", description="d", files_touched=["src/auth.py"])],
        resolutions=[Resolution(issue_id="d1", outcome="Defaults to a1's position: session cookies", rationale="r")],
        status="approved",
    )
    room.tickets = [ticket]
    return room, ticket


@pytest.mark.asyncio
async def test_the_coder_gets_the_goal_at_background_strength(monkeypatch):
    room, ticket = _approved_room()
    captured: dict = {}

    async def fake_implement_ticket(**kwargs) -> TicketCode:
        captured.update(kwargs)
        return TicketCode(files={})

    monkeypatch.setenv("EXECUTION_MODE", "agent")
    monkeypatch.setattr(execution_module, "implement_ticket", fake_implement_ticket)

    await execution_module.agent_implementer()(room, ticket)

    assert "Anchor 0.20: background" in captured["goal_block"]
    assert GOAL["success_criteria"][0] not in captured["goal_block"]


def test_the_coder_prompt_carries_the_resolutions_and_the_goal():
    room, ticket = _approved_room()
    captured: dict = {}

    def chat_fn(**kwargs) -> str:
        captured.update(kwargs)
        return "=== FILE: src/auth.py ===\nx = 1\n=== END FILE ===\n"

    implement_ticket_sync(
        ticket=ticket,
        all_tickets=[ticket],
        plan=room.plan,
        context=None,
        read_file=lambda p: None,
        paths=[],
        chat_fn=chat_fn,
        goal_block=goal_block(room.goal, 0.2),
    )

    assert "RESOLVED DISAGREEMENTS" in captured["user"]
    assert "d1: Defaults to a1's position: session cookies" in captured["user"]
    assert room.goal.statement in captured["user"]


# --- persistence --------------------------------------------------------------


def test_snapshot_keeps_goal_and_weights_but_never_the_admin_token():
    room = Room(room_id="r_snap")
    room.goal = Goal(**GOAL)
    room.weights = {"u1": 2.0}

    snapshot = room_snapshot(room)

    assert snapshot["goal"]["fingerprint"] == room.goal.fingerprint
    assert snapshot["weights"] == {"u1": 2.0}
    assert room.admin_token not in json.dumps(snapshot)
