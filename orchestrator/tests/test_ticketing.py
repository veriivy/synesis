import json
from pathlib import Path

import pytest

from orchestrator.schemas import FinalPlan
from orchestrator.ticketing import decompose_tickets
from orchestrator.validator import EmptyFilesOwnedError

FIXTURE = Path(__file__).parent / "fixtures" / "final_plan.json"


def load_plan() -> FinalPlan:
    return FinalPlan.model_validate(json.loads(FIXTURE.read_text()))


class FakeChat:
    """Scripted stand-in for providers.chat — same keyword signature
    (system, user, provider, max_tokens), so it's a drop-in for
    decompose_tickets(chat_fn=...) without ever making a real call."""

    def __init__(self, script: list[str]):
        self._script = list(script)
        self.calls: list[dict] = []

    def __call__(self, *, system, user, provider, max_tokens):
        self.calls.append({"system": system, "user": user, "provider": provider})
        if self._script:
            return self._script.pop(0)
        return json.dumps({"tickets": []})


def test_happy_path_disjoint_tickets_pass_straight_through():
    plan = load_plan()
    fake = FakeChat(
        [
            json.dumps(
                {
                    "tickets": [
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
                }
            )
        ]
    )

    result = decompose_tickets(plan, chat_fn=fake)

    assert result.adjustments == []
    assert {t.ticket_id for t in result.tickets} == {"t1", "t2"}
    assert all(t.plan_id == plan.plan_id and t.status == "pending" for t in result.tickets)
    assert len(fake.calls) == 1


def test_overlapping_tickets_get_demoted_not_rejected():
    plan = load_plan()
    fake = FakeChat(
        [
            json.dumps(
                {
                    "tickets": [
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
                }
            )
        ]
    )

    result = decompose_tickets(plan, chat_fn=fake)

    by_id = {t.ticket_id: t for t in result.tickets}
    assert by_id["t1"].lane == "parallel"
    assert by_id["t2"].lane == "sequential"
    assert by_id["t2"].depends_on == ["t1"]
    assert len(result.adjustments) == 1


def test_empty_files_owned_triggers_one_regeneration_then_succeeds():
    plan = load_plan()
    fake = FakeChat(
        [
            json.dumps(
                {
                    "tickets": [
                        {
                            "ticket_id": "t1",
                            "title": "Bad ticket",
                            "description": "s1",
                            "assigned_agent": "a1",
                            "files_owned": [],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "tickets": [
                        {
                            "ticket_id": "t1",
                            "title": "Fixed ticket",
                            "description": "s1",
                            "assigned_agent": "a1",
                            "files_owned": ["src/auth.py"],
                        }
                    ]
                }
            ),
        ]
    )

    result = decompose_tickets(plan, chat_fn=fake, max_regenerations=1)

    assert len(fake.calls) == 2
    assert result.tickets[0].files_owned == ["src/auth.py"]


def test_empty_files_owned_exhausts_regenerations_and_raises():
    plan = load_plan()
    always_empty = json.dumps(
        {
            "tickets": [
                {
                    "ticket_id": "t1",
                    "title": "Still bad",
                    "description": "s1",
                    "assigned_agent": "a1",
                    "files_owned": [],
                }
            ]
        }
    )
    fake = FakeChat([always_empty, always_empty])

    with pytest.raises(EmptyFilesOwnedError):
        decompose_tickets(plan, chat_fn=fake, max_regenerations=1)

    assert len(fake.calls) == 2


def test_markdown_fenced_json_is_repaired():
    plan = load_plan()
    fenced = (
        "```json\n"
        + json.dumps(
            {
                "tickets": [
                    {
                        "ticket_id": "t1",
                        "title": "Fenced",
                        "description": "s1",
                        "assigned_agent": "a1",
                        "files_owned": ["src/auth.py"],
                    }
                ]
            }
        )
        + "\n```"
    )
    fake = FakeChat([fenced])

    result = decompose_tickets(plan, chat_fn=fake)

    assert result.tickets[0].ticket_id == "t1"


def test_unparseable_output_is_retried_once_then_raises():
    plan = load_plan()
    fake = FakeChat(["not json at all, no braces", "still not json"])

    with pytest.raises(ValueError):
        decompose_tickets(plan, chat_fn=fake)

    assert len(fake.calls) == 2
