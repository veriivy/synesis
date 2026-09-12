"""room_snapshot() — the Mongo-bound serialization of a Room. Pure
function, no database needed: the property under test is the shape it
produces and, especially, what it leaves out.
"""

from __future__ import annotations

import json

from orchestrator.rooms import Phase, Room, room_snapshot
from orchestrator.schemas import FinalPlan, Participant, Task, Ticket


def test_snapshot_never_includes_an_api_key():
    room = Room(room_id="r1")
    room.participants["u1"] = Participant(
        user_id="u1",
        display_name="Avery",
        provider="claude",
        model="claude-sonnet-4-5",
        api_key="sk-super-secret-do-not-leak",
    )

    snapshot = room_snapshot(room)

    assert "sk-super-secret-do-not-leak" not in json.dumps(snapshot)
    assert "api_key" not in snapshot["participants"]["u1"]


def test_snapshot_is_json_serializable_and_covers_every_stage():
    room = Room(room_id="r1")
    room.phase = Phase.AWAITING_APPROVAL
    room.participants["u1"] = Participant(
        user_id="u1", display_name="Avery", provider="claude", model="m"
    )
    room.tasks_by_user["u1"] = [Task(text="do the thing", priority="must")]
    room.plan = FinalPlan(
        plan_id="plan-1",
        rounds_used=2,
        summary="s",
        steps=[],
        approvals={"u1": True},
        status="approved",
    )
    room.tickets = [
        Ticket(
            ticket_id="t1",
            plan_id="plan-1",
            title="t",
            description="d",
            assigned_agent="a1",
            files_owned=["src/a.py"],
        )
    ]

    snapshot = room_snapshot(room)
    json.dumps(snapshot)  # must not raise

    assert snapshot["room_id"] == "r1"
    assert snapshot["phase"] == "awaiting_approval"
    assert snapshot["tasks_by_user"]["u1"][0]["text"] == "do the thing"
    assert snapshot["plan"]["status"] == "approved"
    assert snapshot["tickets"][0]["ticket_id"] == "t1"
    assert "README.md" in snapshot["files"]  # the seeded workspace
