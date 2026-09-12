"""write_file enforcement and execute_room scheduling, in isolation from
the negotiation loop — a Room and its tickets are built by hand here so
these tests exercise exactly CLAUDE.md's "Agent tools during execution"
clause: "write_file rejects when: the resolved path escapes the workspace
root; the path is not in the calling ticket's files_owned; or there is no
approved plan." This is where that guarantee is proven, not in a staged
demo event.
"""

from __future__ import annotations

import pytest

from app.k2.execution import execute_room, write_file
from app.rooms import Room
from app.schemas import FinalPlan, Ticket


def _approved_plan(plan_id: str = "plan-1") -> FinalPlan:
    return FinalPlan(
        plan_id=plan_id,
        rounds_used=1,
        summary="s",
        steps=[],
        approvals={"u1": True, "u2": True},
        status="approved",
    )


def _ticket(ticket_id: str, files: list[str], **kw) -> Ticket:
    return Ticket(
        ticket_id=ticket_id,
        plan_id="plan-1",
        title=ticket_id,
        description="",
        assigned_agent=kw.pop("assigned_agent", "a1"),
        files_owned=files,
        **kw,
    )


def test_write_file_rejects_without_an_approved_plan():
    room = Room(room_id="r1")  # room.plan is None
    ticket = _ticket("t1", ["src/a.py"])

    result = write_file(room, ticket, "src/a.py", "content")

    assert result.accepted is False
    assert "approved plan" in result.reason


def test_write_file_rejects_path_outside_files_owned():
    room = Room(room_id="r1")
    room.plan = _approved_plan()
    ticket = _ticket("t1", ["src/a.py"])

    result = write_file(room, ticket, "src/b.py", "content")

    assert result.accepted is False
    assert "does not own" in result.reason
    assert "src/b.py" not in room.files or room.files["src/b.py"].last_written_by is None


@pytest.mark.parametrize(
    "escaping_path",
    [
        "../outside.py",
        "src/../../outside.py",
        "/etc/passwd",
        "C:\\Windows\\System32\\evil.py",
        "https://example.com/x.py",
    ],
)
def test_write_file_rejects_paths_that_escape_the_workspace(escaping_path: str):
    room = Room(room_id="r1")
    room.plan = _approved_plan()
    ticket = _ticket("t1", [escaping_path])

    result = write_file(room, ticket, escaping_path, "content")

    assert result.accepted is False
    assert "escapes workspace" in result.reason


def test_write_file_accepts_a_legitimate_write_and_updates_the_file():
    room = Room(room_id="r1")
    room.plan = _approved_plan()
    ticket = _ticket("t1", ["src/a.py"], assigned_agent="a1")

    result = write_file(room, ticket, "src/a.py", "new content")

    assert result.accepted is True
    assert result.reason is None
    assert room.files["src/a.py"].content == "new content"
    assert room.files["src/a.py"].last_written_by == "a1"


@pytest.mark.asyncio
async def test_execute_room_runs_parallel_tickets_and_completes_sequential_after():
    room = Room(room_id="r1")
    room.plan = _approved_plan()
    room.tickets = [
        _ticket("t1", ["src/a.py"], assigned_agent="a1", lane="parallel"),
        _ticket("t2", ["src/b.py"], assigned_agent="a2", lane="parallel"),
        _ticket("t3", ["src/c.py"], assigned_agent="a1", lane="sequential", depends_on=["t1", "t2"]),
    ]

    await execute_room(room)

    assert {t.status for t in room.tickets} == {"done"}
    assert room.status == "done"
    for path in ("src/a.py", "src/b.py", "src/c.py"):
        assert path in room.files
        assert room.files[path].last_written_by is not None

    events_by_ticket = {}
    for e in room.bus.log:
        if e.type == "ticket_started":
            events_by_ticket.setdefault(e.ticket_id, []).append(("started", e))
        if e.type == "ticket_completed":
            events_by_ticket.setdefault(e.ticket_id, []).append(("completed", e))
    # t3 must not start until both its dependencies have completed.
    t1_completed_index = [e.type for e in room.bus.log].index("ticket_completed")
    t3_started_index = next(
        i
        for i, e in enumerate(room.bus.log)
        if e.type == "ticket_started" and e.ticket_id == "t3"
    )
    assert t3_started_index > t1_completed_index


@pytest.mark.asyncio
async def test_execute_room_fails_tickets_on_a_dangling_dependency():
    room = Room(room_id="r1")
    room.plan = _approved_plan()
    room.tickets = [
        _ticket("t1", ["src/a.py"], lane="sequential", depends_on=["does-not-exist"]),
    ]

    await execute_room(room)

    assert room.tickets[0].status == "failed"
