"""What a negotiation records about how it ended (issues.py, main._run_loop,
main._decision_record).

Pinned here, each with the failure it used to produce:
  - an issue conceded mid-negotiation vanished from the FinalPlan (CLAUDE.md
    step 4 wants a resolution per blocking issue)
  - an unreadable K2 round published zero differences: under
    STOP_ON_CONVERGE that ended the argument as "converged", and in the last
    round it erased every open issue from the plan
  - CLAUDE.md's forced probe round didn't exist
  - context_updated on approval carried the plan's one-line summary, and
    the shared context the coders read never changed
"""

from __future__ import annotations

import pytest

import orchestrator.main as main_module
from orchestrator.issues import Issue, IssueLedger, how_settled, moves_of
from orchestrator.main import _task_lists_differ
from orchestrator.tests.scripted_room import (  # noqa: F401 — fixtures
    ScriptedRoom,
    client,
    events,
    negotiate,
    say,
    script,
    setup_room,
)
from orchestrator.tests.test_lifecycle import _approve_both, real_ticketing  # noqa: F401

D1 = {
    "issue_id": "d1",
    "topic": "auth mechanism",
    "positions": {"a1": "session cookies", "a2": "bearer JWT"},
    "severity": "blocking",
    "files": ["src/auth.py"],
}


def blocking(*diffs: dict) -> dict:
    return {"differences": list(diffs)}


# --- moves ---------------------------------------------------------------------------


def test_structured_moves_win_and_only_known_issues_count():
    reply = {"moves": {"d1": "concede", "d9": "HOLD", "d2": "shrug"}, "content": "d1: HOLD"}

    assert moves_of(reply, ["d1", "d2"]) == {"d1": "CONCEDE"}


def test_the_speech_is_the_fallback_one_clause_at_a_time():
    reply = {"content": "d1: CONCEDE — fine, JWT.\nd2: HOLD; d3 COMPROMISES on file ownership."}

    assert moves_of(reply, ["d1", "d2", "d3"]) == {"d1": "CONCEDE", "d2": "HOLD", "d3": "COMPROMISE"}


def test_prose_and_ambiguous_clauses_are_not_read_as_moves():
    reply = {"content": "On d1 no compromise is possible. d2: HOLD or CONCEDE, undecided."}

    assert moves_of(reply, ["d1", "d2"]) == {}


# --- the ledger ------------------------------------------------------------------------


def test_the_ledger_settles_and_reopens_issues():
    ledger = IssueLedger()
    assert ledger.observe(blocking(D1), 1) == []

    settled = ledger.observe(blocking({**D1, "severity": "minor"}), 2)
    assert [i.issue_id for i in settled] == ["d1"] and settled[0].settled_round == 2

    assert ledger.observe(blocking(D1), 3) == []
    assert not ledger.issues["d1"].settled and ledger.issues["d1"].first_round == 1


@pytest.mark.parametrize(
    "moves, expected",
    [
        ({"a1": "HOLD", "a2": "CONCEDE"}, "a2 conceded"),
        ({"a1": "CONCEDE", "a2": "CONCEDE"}, "a1 and a2 conceded"),
        ({"a1": "COMPROMISE", "a2": "HOLD"}, "settled on a1's compromise"),
        ({"a1": "HOLD", "a2": "HOLD"}, "nobody conceded, but K2 no longer finds it blocking"),
        ({}, "K2 no longer finds it blocking"),
    ],
)
def test_how_an_issue_was_settled(moves, expected):
    issue = Issue("d1", "auth", {}, first_round=1, last_blocking_round=1, settled_round=2, moves=moves)

    assert how_settled(issue) == expected


def test_task_lists_differ_ignores_case_and_spacing():
    def payload(a: str, b: str) -> dict:
        return {"tasks_by_user": [{"tasks": [{"text": a}]}, {"tasks": [{"text": b}]}]}

    assert _task_lists_differ(payload("Use JWT", "use  jwt")) is False
    assert _task_lists_differ(payload("Use JWT", "Use cookies")) is True
    assert _task_lists_differ({"tasks_by_user": []}) is False


# --- through the loop -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_concession_mid_negotiation_gets_its_own_resolution(client, script: ScriptedRoom):
    script.speech = {"a2": "d1: CONCEDE — cookies it is."}
    script.differences_by_round = {2: [], 3: []}
    room_id, _, _ = await setup_room(client)

    room = await negotiate(client, room_id)

    (resolution,) = room.plan.resolutions
    assert resolution.issue_id == "d1"
    assert resolution.outcome == "Resolved in round 2: a2 (Blair) conceded to a1 (Avery) — session cookies."
    assert "auth mechanism was blocking in round 1" in resolution.rationale
    assert "1 settled during negotiation" in room.plan.summary
    assert "0 blocking issue(s) still need a decision" in room.plan.summary
    (said,) = [m for m in events(room_id, "moderator_message") if m["content"].startswith("Settled")]
    assert said["round"] == 2 and "d1 (auth mechanism) — a2 (Blair) conceded" in said["content"]


@pytest.mark.asyncio
async def test_k2_is_shown_last_rounds_differences(client, script: ScriptedRoom):
    room_id, _, _ = await setup_room(client)

    await negotiate(client, room_id)

    assert script.analyses[0]["previous_differences"] is None
    assert [d["issue_id"] for d in script.analyses[1]["previous_differences"]] == ["d1"]


@pytest.mark.asyncio
async def test_an_unreadable_final_round_keeps_the_open_issue_in_the_plan(client, script: ScriptedRoom):
    script.unreadable_rounds = {3}
    room_id, _, _ = await setup_room(client)

    room = await negotiate(client, room_id)

    assert [r.issue_id for r in room.plan.resolutions] == ["d1"]
    assert room.plan.resolutions[0].outcome.startswith("Unresolved")
    round3 = events(room_id, "analysis")[-1]
    assert round3["round"] == 3 and [d["issue_id"] for d in round3["differences"]] == ["d1"]
    assert any(
        "carrying round 2's differences forward" in m["content"]
        for m in events(room_id, "moderator_message")
    )


@pytest.mark.asyncio
async def test_an_unreadable_round_is_never_taken_for_convergence(client, script: ScriptedRoom, monkeypatch):
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", True)
    script.unreadable_rounds = {1}
    room_id, _, _ = await setup_room(client)

    room = await negotiate(client, room_id)

    assert len(script.analyses) == 3, "round 1 said nothing — that is not agreement"
    assert room.plan.resolutions[0].issue_id == "d1"


@pytest.mark.asyncio
async def test_a_quiet_first_round_gets_one_probe_round(client, script: ScriptedRoom, monkeypatch):
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", True)
    script.differences_by_round = {1: []}  # K2 skims round 1; the probe finds d1
    room_id, _, tokens = await setup_room(client)
    await say(client, room_id, "u1", tokens["u1"], "cookies are non-negotiable")

    room = await negotiate(client, room_id)

    assert [a["probe"] for a in script.analyses] == [False, True, False]
    assert {r["round_index"] for r in script.revisions} == {2}, "no advocate turn in the probe's round"
    assert "[YOUR USER · Avery] said" in script.revisions_for("a1")[0]["interjections"], (
        "what K2 read in round 1 reaches the advocates in round 2"
    )
    assert script.analyses[1]["interjections"] == "", "K2 isn't handed it twice"
    assert [e["round"] for e in events(room_id, "round_complete")] == [1, 2]
    assert any("one probe round" in m["content"] for m in events(room_id, "moderator_message"))
    assert room.plan.resolutions[0].issue_id == "d1"


@pytest.mark.asyncio
async def test_a_probe_that_finds_nothing_ends_the_negotiation(client, script: ScriptedRoom, monkeypatch):
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", True)
    script.differences_by_round = {1: [], 2: []}
    room_id, _, _ = await setup_room(client)

    room = await negotiate(client, room_id)

    assert len(script.analyses) == 2 and script.revisions == []
    assert room.plan.rounds_used == 2 and room.plan.resolutions == []


@pytest.mark.asyncio
async def test_matching_task_lists_converge_without_a_probe(client, script: ScriptedRoom, monkeypatch):
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", True)
    script.differences_by_round = {1: []}
    same = {"u1": "Bearer JWT auth", "u2": "bearer jwt auth"}
    room_id, _, _ = await setup_room(client, task_texts=same)

    room = await negotiate(client, room_id)

    assert len(script.analyses) == 1 and room.plan.rounds_used == 1


@pytest.mark.asyncio
async def test_without_early_stopping_the_probe_still_directs_k2(client, script: ScriptedRoom):
    script.differences_by_round = {1: []}
    room_id, _, _ = await setup_room(client)

    await negotiate(client, room_id)

    assert [a["probe"] for a in script.analyses] == [False, True, False]
    assert {r["round_index"] for r in script.revisions} == {1, 2}, "advocates keep their turns"


# --- approval updates the shared context -------------------------------------------------


@pytest.mark.asyncio
async def test_approval_records_the_decision_in_the_shared_context(client, script, real_ticketing):
    script.files = {"a1": ["src/auth.py"], "a2": ["src/auth.py", "src/tokens.py"]}
    script.diff["files"] = ["src/auth.py"]
    room_id, _, tokens = await setup_room(client, weights={"u1": 2, "u2": 1}, context="ledger-api")
    room = await negotiate(client, room_id)

    await _approve_both(client, room_id, tokens)

    seeded, update = events(room_id, "context_updated")
    assert seeded["content"] == "ledger-api"
    assert update["content"].startswith("Decided: d1 (auth mechanism): Defaults to a1's position")
    assert "a1 (Avery) owns src/auth.py." in update["content"]
    assert "a2 (Blair) owns src/tokens.py." in update["content"]
    assert update["version"] == 2, "v1 was the seeded context"
    assert room.project_context.startswith("ledger-api\n\nDecided: ")


@pytest.mark.asyncio
async def test_a_settled_issue_is_recorded_with_the_position_that_stands(client, script, real_ticketing):
    script.speech = {"a2": "d1: CONCEDE — cookies it is."}
    script.differences_by_round = {2: [], 3: []}
    room_id, _, tokens = await setup_room(client)
    await negotiate(client, room_id)

    await _approve_both(client, room_id, tokens)

    content = events(room_id, "context_updated")[0]["content"]
    assert content.startswith(
        "Decided: d1 (auth mechanism): Resolved in round 2: a2 (Blair) conceded to a1 (Avery) — session cookies."
    )


@pytest.mark.asyncio
async def test_an_undecided_issue_is_recorded_as_such(client, script, real_ticketing):
    room_id, _, tokens = await setup_room(client)
    room = await negotiate(client, room_id)

    await _approve_both(client, room_id, tokens)

    content = events(room_id, "context_updated")[0]["content"]
    assert "d1 (auth mechanism): no single answer" in content
    assert "Both sides write src/auth.py, one ticket after the other." in content
    assert room.project_context == content, "no seeded context, so the record is all of it"
