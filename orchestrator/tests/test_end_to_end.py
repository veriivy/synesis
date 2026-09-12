"""One full room lifecycle through the real FastAPI app: create -> join ->
tasks -> negotiate -> approve x2 -> tickets_created -> execute -> done.
Uses only the template providers (no network, no API keys) — this is the
"does the whole wire actually work" test, as opposed to test_validator.py /
test_ticketing.py which test units in isolation.

Asserts against Room.bus.log (the in-memory event log) rather than
consuming GET /rooms/{id}/stream over HTTP: httpx's ASGITransport collects
an entire streamed response before returning it, so it can't be used
in-process to read a live SSE stream that — correctly — never ends (see
Room.background_task's docstring in app/rooms.py). The actual wire format
of that endpoint is exercised manually against a real uvicorn socket, not
in this automated suite.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.rooms import Room, RoomStore

# Both users describe the same feature so the template PoA generator's
# naive slugify collides on one file — a real conflict for K2 to diff and
# negotiate over, the same trick fixtures/PoA1.json and PoA2.json use.
TASK_TEXT_U1 = "Handle authentication using session cookies for the browser."
TASK_TEXT_U2 = "Handle authentication using session cookies for the browser too."


async def _run_to_tickets(client: AsyncClient, store: RoomStore, *, priority_u1: str, priority_u2: str) -> Room:
    """Create a room, join two users, submit colliding tasks, negotiate,
    and get both approvals — through tickets_created. Returns the Room so
    the caller can inspect whatever the scenario needs to check."""
    room_id = (await client.post("/rooms")).json()["room_id"]
    room = store.get(room_id)

    for user_id in ("u1", "u2"):
        r = await client.post(
            f"/rooms/{room_id}/participants",
            json={"user_id": user_id, "display_name": user_id, "provider": "claude", "model": "m"},
        )
        assert r.status_code == 201, r.text

    for user_id, text, priority in (("u1", TASK_TEXT_U1, priority_u1), ("u2", TASK_TEXT_U2, priority_u2)):
        r = await client.post(
            f"/rooms/{room_id}/tasks",
            json={"user_id": user_id, "tasks": [{"text": text, "priority": priority}]},
        )
        assert r.status_code == 200, r.text

    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 202, r.text
    await asyncio.wait_for(room.background_task, timeout=10)

    assert room.plan is not None
    assert room.plan.resolutions, "expected a resolution for the deliberate collision"

    r1 = await client.post(f"/rooms/{room_id}/plan/approve", json={"user_id": "u1", "approved": True})
    assert r1.status_code == 200
    r2 = await client.post(f"/rooms/{room_id}/plan/approve", json={"user_id": "u2", "approved": True})
    assert r2.status_code == 200
    assert room.plan.status == "approved"
    assert room.tickets, "expected at least one ticket"

    return room


def _assert_parallel_tickets_disjoint(room: Room) -> None:
    seen: dict[str, str] = {}
    for t in room.tickets:
        if t.lane != "parallel":
            continue
        for path in t.files_owned:
            assert path not in seen, f"parallel collision on {path}: {seen[path]} and {t.ticket_id}"
            seen[path] = t.ticket_id


@pytest.mark.asyncio
async def test_full_room_lifecycle_want_concedes_to_must():
    store = RoomStore()
    app = create_app(store=store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        room = await _run_to_tickets(client, store, priority_u1="must", priority_u2="want")

        types = [e.type for e in room.bus.log]
        assert "poa_generated" in types
        assert "analysis" in types
        assert types.index("plan_proposed") < types.index("tickets_created")
        assert types[-1] == "tickets_created"

        # a clean concession: exactly one step survives for the shared
        # path, so it never even reaches two tickets claiming one file.
        _assert_parallel_tickets_disjoint(room)
        owned_paths = [p for t in room.tickets for p in t.files_owned]
        assert len(owned_paths) == len(set(owned_paths))

        room_id = room.room_id
        r = await client.post(f"/rooms/{room_id}/execute")
        assert r.status_code == 202, r.text
        await asyncio.wait_for(room.background_task, timeout=10)

        assert {t.ticket_id for t in room.tickets if t.status == "done"} == {
            t.ticket_id for t in room.tickets
        }
        assert room.status == "done"

        files = (await client.get(f"/rooms/{room_id}/files")).json()["tree"]
        assert files, "workspace should be non-empty"
        for ticket in room.tickets:
            for path in ticket.files_owned:
                f = (await client.get(f"/rooms/{room_id}/files/{path}")).json()
                assert f["last_written_by"] == ticket.assigned_agent


@pytest.mark.asyncio
async def test_must_vs_must_deadlocks_then_ticketing_serializes_it():
    """Neither agent may concede a must-have (CLAUDE.md's anti-mush rule),
    so the issue survives all 3 rounds as an honest "unresolved" resolution
    and the same file ends up claimed by two steps -> two tickets. The
    validator's job is exactly this case: it must not let both run
    parallel, so it demotes the later one to sequential with a depends_on
    edge, and execution must still complete (the dependent ticket's write
    lands after, not instead of, colliding with the first)."""
    store = RoomStore()
    app = create_app(store=store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        room = await _run_to_tickets(client, store, priority_u1="must", priority_u2="must")

        assert any("Unresolved after" in r.outcome for r in room.plan.resolutions)
        assert len(room.tickets) == 2
        _assert_parallel_tickets_disjoint(room)

        by_lane = sorted(room.tickets, key=lambda t: t.lane)
        parallel_ticket, sequential_ticket = by_lane[0], by_lane[1]
        assert parallel_ticket.lane == "parallel"
        assert sequential_ticket.lane == "sequential"
        assert sequential_ticket.depends_on == [parallel_ticket.ticket_id]
        assert sequential_ticket.files_owned == parallel_ticket.files_owned

        room_id = room.room_id
        r = await client.post(f"/rooms/{room_id}/execute")
        assert r.status_code == 202, r.text
        await asyncio.wait_for(room.background_task, timeout=10)

        assert {t.status for t in room.tickets} == {"done"}
        shared_path = parallel_ticket.files_owned[0]
        f = (await client.get(f"/rooms/{room_id}/files/{shared_path}")).json()
        # the sequential ticket ran second (it depended on the parallel
        # one), so its write is what the file ends up holding.
        assert f["last_written_by"] == sequential_ticket.assigned_agent


@pytest.mark.asyncio
async def test_negotiate_rejects_wrong_participant_count():
    store = RoomStore()
    app = create_app(store=store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        room_id = (await client.post("/rooms")).json()["room_id"]
        await client.post(
            f"/rooms/{room_id}/participants",
            json={"user_id": "u1", "display_name": "u1", "provider": "claude", "model": "m"},
        )
        r = await client.post(f"/rooms/{room_id}/negotiate")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_execute_rejects_before_approval():
    store = RoomStore()
    app = create_app(store=store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        room_id = (await client.post("/rooms")).json()["room_id"]
        r = await client.post(f"/rooms/{room_id}/execute")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_unknown_room_returns_404():
    app = create_app(store=RoomStore())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/rooms/does-not-exist/files")
        assert r.status_code == 404
