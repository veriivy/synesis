import json
from pathlib import Path

import pytest

from app.jsonx import ModelOutputError
from app.k2.ticketing import decompose_plan_to_tickets
from app.k2.validator import EmptyFilesOwnedError
from app.providers.stub import StubChatProvider
from app.schemas import FinalPlan

FIXTURE = Path(__file__).parent / "fixtures" / "final_plan.json"


def load_plan() -> FinalPlan:
    return FinalPlan.model_validate(json.loads(FIXTURE.read_text()))


@pytest.mark.asyncio
async def test_happy_path_disjoint_tickets_pass_straight_through():
    plan = load_plan()
    provider = StubChatProvider(
        script=[
            json.dumps(
                [
                    {
                        "ticket_id": "t1",
                        "title": "Rate limiter middleware",
                        "description": "s1",
                        "assigned_agent": "a1",
                        "files_owned": ["src/middleware/rate_limit.py"],
                        "lane": "parallel",
                    },
                    {
                        "ticket_id": "t2",
                        "title": "Login banner",
                        "description": "s3",
                        "assigned_agent": "a2",
                        "files_owned": ["web/src/components/LoginForm.tsx"],
                        "lane": "parallel",
                    },
                ]
            )
        ]
    )

    tickets, adjustments = await decompose_plan_to_tickets(plan, provider, model="stub-model")

    assert adjustments == []
    assert {t.ticket_id for t in tickets} == {"t1", "t2"}
    assert all(t.plan_id == "plan-1" and t.status == "pending" for t in tickets)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_overlapping_tickets_get_demoted_not_rejected():
    plan = load_plan()
    provider = StubChatProvider(
        script=[
            json.dumps(
                [
                    {
                        "ticket_id": "t1",
                        "title": "Rate limiter",
                        "description": "s1",
                        "assigned_agent": "a1",
                        "files_owned": ["src/auth.py", "src/middleware/rate_limit.py"],
                    },
                    {
                        "ticket_id": "t2",
                        "title": "429 responses",
                        "description": "s2",
                        "assigned_agent": "a2",
                        "files_owned": ["src/auth.py"],
                    },
                ]
            )
        ]
    )

    tickets, adjustments = await decompose_plan_to_tickets(plan, provider, model="stub-model")

    by_id = {t.ticket_id: t for t in tickets}
    assert by_id["t1"].lane == "parallel"
    assert by_id["t2"].lane == "sequential"
    assert by_id["t2"].depends_on == ["t1"]
    assert len(adjustments) == 1


@pytest.mark.asyncio
async def test_empty_files_owned_triggers_one_regeneration_then_succeeds():
    plan = load_plan()
    provider = StubChatProvider(
        script=[
            json.dumps(
                [
                    {
                        "ticket_id": "t1",
                        "title": "Bad ticket",
                        "description": "s1",
                        "assigned_agent": "a1",
                        "files_owned": [],
                    }
                ]
            ),
            json.dumps(
                [
                    {
                        "ticket_id": "t1",
                        "title": "Fixed ticket",
                        "description": "s1",
                        "assigned_agent": "a1",
                        "files_owned": ["src/auth.py"],
                    }
                ]
            ),
        ]
    )

    tickets, _ = await decompose_plan_to_tickets(
        plan, provider, model="stub-model", max_regenerations=1
    )

    assert len(provider.calls) == 2
    assert tickets[0].files_owned == ["src/auth.py"]


@pytest.mark.asyncio
async def test_empty_files_owned_exhausts_regenerations_and_raises():
    plan = load_plan()
    always_empty = json.dumps(
        [
            {
                "ticket_id": "t1",
                "title": "Still bad",
                "description": "s1",
                "assigned_agent": "a1",
                "files_owned": [],
            }
        ]
    )
    provider = StubChatProvider(script=[always_empty, always_empty])

    with pytest.raises(EmptyFilesOwnedError):
        await decompose_plan_to_tickets(plan, provider, model="stub-model", max_regenerations=1)

    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_markdown_fenced_json_is_repaired():
    plan = load_plan()
    fenced = "```json\n" + json.dumps(
        [
            {
                "ticket_id": "t1",
                "title": "Fenced",
                "description": "s1",
                "assigned_agent": "a1",
                "files_owned": ["src/auth.py"],
            }
        ]
    ) + "\n```"
    provider = StubChatProvider(script=[fenced])

    tickets, _ = await decompose_plan_to_tickets(plan, provider, model="stub-model")

    assert tickets[0].ticket_id == "t1"


@pytest.mark.asyncio
async def test_unparseable_output_raises_model_output_error_after_retry():
    plan = load_plan()
    provider = StubChatProvider(script=["not json at all, no braces", "still not json"])

    with pytest.raises(ModelOutputError):
        await decompose_plan_to_tickets(plan, provider, model="stub-model")

    assert len(provider.calls) == 2
