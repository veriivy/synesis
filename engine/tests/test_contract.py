"""The fixtures and the Pydantic models must agree.

`/web` is built against `/fixtures`; `/server` emits what `/engine` models. If these
drift, the frontend renders one shape and the backend sends another, and we find out
during the demo. This test is the tripwire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.schemas import (
    AgentMessageEvent,
    CommitEvent,
    DeadlockEvent,
    FileWrittenEvent,
    PlanProposedEvent,
    RoundCompleteEvent,
    Workplan,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

EVENT_MODELS = {
    "agent_message": AgentMessageEvent,
    "round_complete": RoundCompleteEvent,
    "plan_proposed": PlanProposedEvent,
    "deadlock": DeadlockEvent,
    "file_written": FileWrittenEvent,
    "commit": CommitEvent,
}


def _drop_comments(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def test_workplan_fixture_validates():
    data = json.loads((FIXTURES / "workplan.json").read_text(encoding="utf-8"))
    plan = Workplan.model_validate(data)
    assert plan.tasks and plan.interface_contracts
    assert plan.concessions, "a workplan with no concessions means nobody negotiated"


def test_workplan_fixture_has_disjoint_file_ownership():
    plan = Workplan.model_validate(
        json.loads((FIXTURES / "workplan.json").read_text(encoding="utf-8"))
    )
    claimed: dict[str, str] = {}
    for task in plan.tasks:
        for path in task.files_owned:
            assert path not in claimed, f"{path} claimed twice ({claimed.get(path)}, {task.task_id})"
            claimed[path] = task.task_id


@pytest.mark.parametrize("name", sorted(EVENT_MODELS))
def test_every_event_type_has_a_fixture(name: str):
    path = FIXTURES / "events" / f"{name}.json"
    assert path.exists(), f"missing fixture for event type {name!r}"

    raw = _drop_comments(json.loads(path.read_text(encoding="utf-8")))
    model = EVENT_MODELS[name]

    # file_written.json holds several named samples rather than one event.
    samples = [raw] if "type" in raw else list(raw.values())
    assert samples, f"{path} contains no samples"
    for sample in samples:
        model.model_validate(sample)


def test_stream_fixture_validates_line_by_line():
    lines = [
        line
        for line in (FIXTURES / "stream.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) > 10, "the replay stream should cover the whole demo"

    seen = set()
    for i, line in enumerate(lines, 1):
        payload = json.loads(line)
        model = EVENT_MODELS.get(payload.get("type"))
        assert model is not None, f"line {i}: unknown event type {payload.get('type')!r}"
        model.model_validate(payload)
        seen.add(payload["type"])

    # The backup demo has to show the whole story, rejection included.
    assert {"agent_message", "plan_proposed", "file_written", "commit"} <= seen
    rejections = [
        json.loads(line)
        for line in lines
        if json.loads(line)["type"] == "file_written" and not json.loads(line)["accepted"]
    ]
    assert rejections, "the replay stream must contain a rejected write — it is the demo"


def test_intents_fixture_has_a_real_conflict():
    data = json.loads((FIXTURES / "intents.json").read_text(encoding="utf-8"))
    participants = data["participants"]
    assert len(participants) >= 2

    for p in participants:
        must = [r for r in p["requirements"] if r["priority"] == "must-have"]
        assert must, f"{p['agent_id']} has no must-have — it has nothing to defend"
