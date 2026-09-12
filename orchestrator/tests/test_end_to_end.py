"""One full room lifecycle through the real FastAPI app: create -> join ->
tasks -> negotiate -> approve x2 -> tickets_created -> execute -> done.
Also covers the original fixture-based demo path (no tasks submitted) to
make sure adding the tasking endpoints didn't regress it.

The LLM-calling functions (agents.draft_poa/revise_poa, k2.analyze) are
monkeypatched at the names orchestrator.main imported them under — real
network calls aren't part of this test, but the actual ticket
decomposition, the disjoint-ownership validator, and execution/write_file
enforcement all run for real, unfaked. That's deliberate: the model calls
are already used in production by the code that landed on main; what this
suite verifies is the NEW wiring around them (the tasking endpoints,
FinalPlan construction, approval -> ticketing -> execution).

Asserts against bus.history(room_id) rather than consuming GET /stream
over HTTP: httpx's ASGITransport collects an entire streamed response
before returning it, so it can't read a live SSE stream that correctly
never ends.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from httpx import ASGITransport, AsyncClient

import orchestrator.main as main_module
from orchestrator.schemas import Ticket
from orchestrator.ticketing import TicketingResult
from orchestrator.validator import validate_and_fix_tickets

TASK_TEXT_U1 = "Handle authentication using session cookies for the browser."
TASK_TEXT_U2 = "Handle authentication using session cookies for the browser too."


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:4]
    return "-".join(words) or "task"


def fake_draft_poa(
    *, agent_id: str, user_id: str, tasks: dict, provider: str | None = None, api_key: str | None = None
) -> dict:
    entry = next(
        (e for e in tasks.get("tasks_by_user", []) if e.get("user_id") == user_id), None
    )
    task_list = entry["tasks"] if entry else []
    steps = []
    for i, t in enumerate(task_list, start=1):
        steps.append(
            {
                "step_id": f"s{i}",
                "title": t["text"][:60],
                "description": t["text"],
                "files_touched": [f"src/{_slug(t['text'])}.py"],
                "rationale": f"{t['priority']}-have from {user_id}'s task list.",
            }
        )
    return {
        "poa_id": f"poa_{agent_id}",
        "agent_id": agent_id,
        "user_id": user_id,
        "summary": f"Address {user_id}'s {len(steps)} task(s)",
        "steps": steps,
        "assumptions": [],
    }


def fake_analyze(*, context, tasks, poa1: dict, poa2: dict, round_index: int, transcript: str) -> dict:
    by_path_a = {p: s for s in poa1["steps"] for p in s["files_touched"]}
    by_path_b = {p: s for s in poa2["steps"] for p in s["files_touched"]}
    shared = sorted(set(by_path_a) & set(by_path_b))
    differences = []
    for i, path in enumerate(shared, start=1):
        sa, sb = by_path_a[path], by_path_b[path]
        blocking = "must-have" in sa["rationale"] or "must-have" in sb["rationale"]
        differences.append(
            {
                "issue_id": f"d{i}",
                "topic": f"Both plans write {path}",
                "positions": {poa1["agent_id"]: sa["title"], poa2["agent_id"]: sb["title"]},
                "severity": "blocking" if blocking else "minor",
            }
        )
    return {
        "round": round_index,
        "similarities": [] if shared else [{"topic": "no overlap", "detail": "disjoint files"}],
        "differences": differences,
        "converged": not any(d["severity"] == "blocking" for d in differences),
    }


def fake_revise_poa(
    *,
    poa: dict,
    tasks,
    analysis: dict,
    round_index: int,
    provider: str | None = None,
    api_key: str | None = None,
) -> dict:
    priority_by_path: dict[str, str] = {}
    for s in poa["steps"]:
        pr = "must" if "must-have" in s["rationale"] else "want"
        for p in s["files_touched"]:
            priority_by_path[p] = pr

    blocking = [d for d in analysis.get("differences", []) if d["severity"] == "blocking"]
    addressed, lines = [], []
    new_steps = list(poa["steps"])
    for d in blocking:
        path = d["topic"].removeprefix("Both plans write ")
        mine = priority_by_path.get(path, "want")
        addressed.append(d["issue_id"])
        if mine == "want":
            new_steps = [s for s in new_steps if path not in s["files_touched"]]
            lines.append(f"{d['issue_id']}: conceding {path}.")
        else:
            lines.append(f"{d['issue_id']}: holding {path} — must-have, no concession without my user.")

    return {
        "agent_id": poa["agent_id"],
        "user_id": poa["user_id"],
        "content": " ".join(lines) or "nothing to address this round",
        "addresses_issues": addressed,
        "poa": {**poa, "steps": new_steps},
    }


def fake_decompose_tickets(plan, *, step_owner=None):
    """Skips the model entirely: one ticket per plan step, files_owned =
    that step's files_touched. Runs the REAL validator on the result —
    this is what actually proves the disjoint-ownership guarantee held."""
    tickets = [
        Ticket(
            ticket_id=f"t{i}",
            plan_id=plan.plan_id,
            title=step.title,
            description=step.title,
            assigned_agent=(step_owner or {}).get(step.step_id, "a1"),
            files_owned=step.files_touched or [f"src/unspecified-{i}.py"],
            lane="parallel",
        )
        for i, step in enumerate(plan.steps, start=1)
    ]
    fixed, adjustments = validate_and_fix_tickets(tickets)
    return TicketingResult(tickets=fixed, adjustments=adjustments)


@pytest.fixture(autouse=True)
def fake_llm_calls(monkeypatch):
    monkeypatch.setattr(main_module, "draft_poa", fake_draft_poa)
    monkeypatch.setattr(main_module, "analyze", fake_analyze)
    monkeypatch.setattr(main_module, "revise_poa", fake_revise_poa)
    monkeypatch.setattr(main_module, "decompose_tickets", fake_decompose_tickets)


@pytest.fixture
async def client():
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _create_room_with_tasks(
    client: AsyncClient, *, priority_u1: str, priority_u2: str
) -> tuple[str, dict[str, str]]:
    """Returns (room_id, {user_id: participant_token}) — the token from
    each join, required on every subsequent call made as that user_id."""
    room_id = (await client.post("/rooms")).json()["room_id"]

    tokens: dict[str, str] = {}
    for user_id in ("u1", "u2"):
        r = await client.post(
            f"/rooms/{room_id}/participants",
            json={"user_id": user_id, "display_name": user_id, "provider": "claude", "model": "m"},
        )
        assert r.status_code == 201, r.text
        tokens[user_id] = r.json()["participant_token"]

    for user_id, text, priority in (
        ("u1", TASK_TEXT_U1, priority_u1),
        ("u2", TASK_TEXT_U2, priority_u2),
    ):
        r = await client.post(
            f"/rooms/{room_id}/tasks",
            json={
                "user_id": user_id,
                "tasks": [{"text": text, "priority": priority}],
                "participant_token": tokens[user_id],
            },
        )
        assert r.status_code == 200, r.text

    return room_id, tokens


def _assert_parallel_tickets_disjoint(tickets: list[dict]) -> None:
    seen: dict[str, str] = {}
    for t in tickets:
        if t["lane"] != "parallel":
            continue
        for path in t["files_owned"]:
            assert path not in seen, f"parallel collision on {path}: {seen[path]} and {t['ticket_id']}"
            seen[path] = t["ticket_id"]


@pytest.mark.asyncio
async def test_full_room_lifecycle_want_concedes_to_must(client: AsyncClient):
    room_id, tokens = await _create_room_with_tasks(client, priority_u1="must", priority_u2="want")

    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 202, r.text

    room = main_module.rooms.get(room_id)
    await asyncio.wait_for(room.task, timeout=5)

    types = [e["type"] for e in main_module.bus.history(room_id)]
    assert "poa_generated" in types
    assert "analysis" in types
    assert types[-1] == "plan_proposed"

    assert room.plan is not None
    assert room.plan.status == "proposed"
    assert set(room.plan.approvals.keys()) == {"u1", "u2"}

    r1 = await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": "u1", "approved": True, "participant_token": tokens["u1"]},
    )
    assert r1.status_code == 200
    assert room.plan.status == "proposed", "still needs u2's approval"

    r2 = await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": "u2", "approved": True, "participant_token": tokens["u2"]},
    )
    assert r2.status_code == 200
    assert room.plan.status == "approved"
    assert room.tickets, "expected at least one ticket"

    tickets = [t.model_dump() for t in room.tickets]
    _assert_parallel_tickets_disjoint(tickets)
    owned = [p for t in tickets for p in t["files_owned"]]
    assert len(owned) == len(set(owned)), "the concession should leave no file double-claimed"

    r = await client.post(f"/rooms/{room_id}/execute")
    assert r.status_code == 202, r.text
    await asyncio.wait_for(room.execute_task, timeout=5)

    assert {t.status for t in room.tickets} == {"done"}
    for ticket in room.tickets:
        for path in ticket.files_owned:
            f = (await client.get(f"/rooms/{room_id}/files/{path}")).json()
            assert f["last_written_by"] == ticket.assigned_agent


@pytest.mark.asyncio
async def test_must_vs_must_deadlocks_then_ticketing_serializes_it(client: AsyncClient):
    room_id, tokens = await _create_room_with_tasks(client, priority_u1="must", priority_u2="must")

    await client.post(f"/rooms/{room_id}/negotiate")
    room = main_module.rooms.get(room_id)
    await asyncio.wait_for(room.task, timeout=5)

    assert room.plan.resolutions, "expected a resolution recorded for the unresolved collision"

    await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": "u1", "approved": True, "participant_token": tokens["u1"]},
    )
    await client.post(
        f"/rooms/{room_id}/plan/approve",
        json={"user_id": "u2", "approved": True, "participant_token": tokens["u2"]},
    )
    assert len(room.tickets) == 2

    tickets = [t.model_dump() for t in room.tickets]
    _assert_parallel_tickets_disjoint(tickets)
    by_lane = sorted(tickets, key=lambda t: t["lane"])
    assert by_lane[0]["lane"] == "parallel"
    assert by_lane[1]["lane"] == "sequential"
    assert by_lane[1]["depends_on"] == [by_lane[0]["ticket_id"]]
    assert by_lane[1]["files_owned"] == by_lane[0]["files_owned"]

    await client.post(f"/rooms/{room_id}/execute")
    await asyncio.wait_for(room.execute_task, timeout=5)

    assert {t.status for t in room.tickets} == {"done"}
    shared_path = by_lane[0]["files_owned"][0]
    f = (await client.get(f"/rooms/{room_id}/files/{shared_path}")).json()
    assert f["last_written_by"] == by_lane[1]["assigned_agent"]


@pytest.mark.asyncio
async def test_negotiate_without_tasks_falls_back_to_fixtures(client: AsyncClient):
    """The original demo path (no /tasks calls at all) must keep working."""
    room_id = (await client.post("/rooms")).json()["room_id"]

    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 202, r.text

    room = main_module.rooms.get(room_id)
    await asyncio.wait_for(room.task, timeout=5)

    assert room.plan is not None
    assert room.plan.status == "proposed"
    types = [e["type"] for e in main_module.bus.history(room_id)]
    assert types[-1] == "plan_proposed"


@pytest.mark.asyncio
async def test_execute_rejects_before_approval(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]
    r = await client.post(f"/rooms/{room_id}/execute")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_negotiate_with_tasks_but_not_two_participants_rejects(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]
    join = await client.post(
        f"/rooms/{room_id}/participants",
        json={"user_id": "u1", "display_name": "u1", "provider": "claude", "model": "m"},
    )
    token = join.json()["participant_token"]
    r = await client.post(
        f"/rooms/{room_id}/tasks",
        json={
            "user_id": "u1",
            "tasks": [{"text": "x", "priority": "must"}],
            "participant_token": token,
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_unknown_room_returns_404(client: AsyncClient):
    r = await client.get("/rooms/does-not-exist/files")
    assert r.status_code == 404
