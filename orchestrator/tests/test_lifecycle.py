"""Gaps found in an audit of the room lifecycle, each pinned by the exact
failure it used to produce:

  - re-negotiating an approved plan swapped room.plan out from under its
    tickets (write_file then refuses every write mid-execution)
  - voting while a re-negotiation runs approved a plan about to be replaced
  - any never-joined user_id could add itself as a voter and veto the plan
  - a second "yes" re-ran ticketing and replaced room.tickets
  - POST /context was open to anyone, any time — and it goes into prompts
  - opening drafts ran in sequence ON the event loop, freezing the server
  - advocates were told to name real file paths in a repo never shown them
  - a weighted deadlock default kept the loser's step, so the loser's coder
    wrote the contested file last
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from httpx import AsyncClient

import orchestrator.main as main_module
from orchestrator.tests.scripted_room import (  # noqa: F401 — fixtures
    ScriptedRoom,
    client,
    events,
    negotiate,
    script,
    setup_room,
)
from orchestrator.tests.test_end_to_end import fake_agent_implementer, fake_decompose_tickets


@pytest.fixture
def real_ticketing(monkeypatch):
    """Ticketing without a model, execution without a model — the
    validator and write_file still run for real."""
    monkeypatch.setattr(main_module, "decompose_tickets", fake_decompose_tickets)
    monkeypatch.setattr(main_module, "agent_implementer", fake_agent_implementer)


async def _approve(client: AsyncClient, room_id: str, user_id: str, token: str | None, approved=True, **extra):
    return await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": user_id, "approved": approved, "participant_token": token, **extra},
    )


async def _approve_both(client, room_id, tokens):
    for user_id in ("u1", "u2"):
        r = await _approve(client, room_id, user_id, tokens[user_id])
        assert r.status_code == 200, r.text


# --- approval / negotiation lifecycle -------------------------------------------------


@pytest.mark.asyncio
async def test_an_approved_plan_cannot_be_renegotiated(client, script, real_ticketing):
    room_id, _, tokens = await setup_room(client)
    room = await negotiate(client, room_id)
    await _approve_both(client, room_id, tokens)
    approved_plan = room.plan

    r = await client.post(f"/rooms/{room_id}/negotiate")

    assert r.status_code == 409
    assert room.plan is approved_plan and room.plan.status == "approved"


@pytest.mark.asyncio
async def test_a_second_yes_does_not_rerun_ticketing(client, script, real_ticketing):
    room_id, _, tokens = await setup_room(client)
    room = await negotiate(client, room_id)
    await _approve_both(client, room_id, tokens)
    tickets = room.tickets

    again = await _approve(client, room_id, "u1", tokens["u1"])
    late_no = await _approve(client, room_id, "u2", tokens["u2"], approved=False)

    assert again.status_code == 409 and late_no.status_code == 409
    assert room.tickets is tickets
    assert len(events(room_id, "tickets_created")) == 1
    assert room.plan.approvals == {"u1": True, "u2": True}


@pytest.mark.asyncio
async def test_approval_after_failed_ticketing_retries_it(client, script, monkeypatch):
    room_id, _, tokens = await setup_room(client)
    room = await negotiate(client, room_id)

    def broken(plan, *, step_owner=None):
        raise RuntimeError("K2 down")

    monkeypatch.setattr(main_module, "decompose_tickets", broken)
    await _approve_both(client, room_id, tokens)
    assert room.plan.status == "approved" and not room.tickets

    monkeypatch.setattr(main_module, "decompose_tickets", fake_decompose_tickets)
    r = await _approve(client, room_id, "u1", tokens["u1"])

    assert r.status_code == 200 and room.tickets
    assert len(events(room_id, "plan_approved")) == 1, "a retry is not a second approval"


@pytest.mark.asyncio
async def test_votes_are_refused_while_a_renegotiation_runs(client, script):
    room_id, _, tokens = await setup_room(client)
    await negotiate(client, room_id)
    await _approve(client, room_id, "u2", tokens["u2"], approved=False, notes="no")

    release = threading.Event()
    script.on_analyze = lambda round_index: release.wait(timeout=5)
    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 202
    try:
        vote = await _approve(client, room_id, "u1", tokens["u1"])
        assert vote.status_code == 409
        assert "negotiation is running" in vote.json()["detail"]
    finally:
        release.set()
        await asyncio.wait_for(main_module.rooms.get(room_id).task, timeout=5)


@pytest.mark.asyncio
async def test_a_stranger_cannot_add_themselves_as_a_voter(client, script):
    room_id, _, _ = await setup_room(client)
    room = await negotiate(client, room_id)

    r = await _approve(client, room_id, "u9", None, approved=False)

    assert r.status_code == 403
    assert room.plan.approvals == {"u1": False, "u2": False}
    assert events(room_id, "approval_updated") == []


# --- /context ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_is_admin_only_and_frozen_once_negotiation_starts(client, script):
    created = (await client.post("/rooms")).json()
    room_id, admin = created["room_id"], created["admin_token"]

    r = await client.post(f"/rooms/{room_id}/context", json={"project_context": "injected"})
    assert r.status_code == 403
    r = await client.post(f"/rooms/{room_id}/context", json={"project_context": "x", "admin_token": "nope"})
    assert r.status_code == 403
    r = await client.post(f"/rooms/{room_id}/context", json={"project_context": "real", "admin_token": admin})
    assert r.status_code == 200 and r.json() == {"version": 1}

    await negotiate(client, room_id)  # fixture path is enough to lock it
    r = await client.post(f"/rooms/{room_id}/context", json={"project_context": "late", "admin_token": admin})
    assert r.status_code == 409
    assert main_module.rooms.get(room_id).project_context == "real"


# --- drafting ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_drafts_run_concurrently_and_off_the_event_loop(client, script):
    room_id, _, _ = await setup_room(client)
    loop_thread = threading.get_ident()
    both_drafting = threading.Barrier(2, timeout=3)
    threads: list[int] = []

    def drafting(agent_id: str) -> None:
        threads.append(threading.get_ident())
        both_drafting.wait()  # breaks (and fails the draft) unless both run at once

    script.on_draft = drafting
    await negotiate(client, room_id)

    assert len(script.drafts) == 2 and not events(room_id, "error")
    assert loop_thread not in threads


@pytest.mark.asyncio
async def test_advocates_are_shown_the_project_and_its_files(client, script):
    room_id, _, _ = await setup_room(client, context="ledger-api: add auth so /me knows the caller.")
    await negotiate(client, room_id)

    for call in [*script.drafts, *script.revisions]:
        assert "ledger-api: add auth" in call["project_context"]
        assert "- src/app.py" in call["project_context"]


# --- weighted defaults change what gets built --------------------------------------------


@pytest.mark.asyncio
async def test_a_weighted_default_takes_the_contested_file_away_from_the_loser(
    client, script, real_ticketing
):
    script.files = {"a1": ["src/auth.py"], "a2": ["src/auth.py", "src/tokens.py"]}
    script.diff["files"] = ["src/auth.py", "src/tokens.py"]  # tokens.py: only a2 writes it
    room_id, _, tokens = await setup_room(client, weights={"u1": 2, "u2": 1})
    room = await negotiate(client, room_id)

    owners = {p: room.step_owner[s.step_id] for s in room.plan.steps for p in s.files_touched}
    assert owners == {"src/auth.py": "a1", "src/tokens.py": "a2"}, (
        "a2 loses only the file a1 also writes; a file nobody else would write stays"
    )
    assert "a2 no longer writes src/auth.py" in room.plan.resolutions[0].rationale

    await _approve_both(client, room_id, tokens)
    auth_owners = [t.assigned_agent for t in room.tickets if "src/auth.py" in t.files_owned]
    assert auth_owners == ["a1"]


@pytest.mark.asyncio
async def test_an_undecided_deadlock_leaves_both_sides_steps_in(client, script):
    room_id, _, _ = await setup_room(client)
    room = await negotiate(client, room_id)

    assert sorted(room.step_owner.values()) == ["a1", "a2"]
    assert room.plan.resolutions[0].outcome.startswith("Unresolved")
