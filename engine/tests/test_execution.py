"""The execution phase. Rejections must surface as events, not exceptions."""

from __future__ import annotations

import pytest

from engine.execution import Executor
from engine.providers import ToolCall
from engine.schemas import Workplan
from engine.tools import WriteOutcome

from conftest import ScriptedClient, plan_json

pytestmark = pytest.mark.asyncio


class FakeHost:
    """Stands in for the server's sandboxed workspace.

    Mirrors the real rule set: reads are open, writes are restricted to the paths the
    approved plan gives the calling agent.
    """

    def __init__(self, plan: Workplan) -> None:
        self.plan = plan
        self.written: dict[str, str] = {}

    def read_file(self, agent_id: str, path: str) -> str:
        return self.written.get(path, f"# existing contents of {path}\n")

    def write_file(self, agent_id: str, path: str, content: str) -> WriteOutcome:
        if path not in self.plan.files_owned_by(agent_id):
            return WriteOutcome(
                accepted=False,
                reason=f"path_not_owned: {path} is not owned by {agent_id}",
            )
        self.written[path] = content
        return WriteOutcome(accepted=True, bytes_written=len(content.encode("utf-8")))


@pytest.fixture
def plan() -> Workplan:
    import json

    return Workplan.model_validate(json.loads(plan_json()))


async def test_accepted_write_emits_an_accepted_event(specs, plan):
    client = ScriptedClient(
        script=["writing my file", "DONE"],
        tool_calls_script=[
            [ToolCall("call_1", "write_file", {"path": "src/auth/tokens.py", "content": "x = 1\n"})],
            [],
        ],
    )
    executor = Executor(room_id="r", plan=plan, host=FakeHost(plan), clients={"claude": client})

    events = [e async for e in executor.run_agent(specs[0])]

    assert len(events) == 1
    assert events[0].type == "file_written"
    assert events[0].accepted is True
    assert events[0].reason is None
    assert events[0].bytes == 6
    assert events[0].agent_id == "claude"


async def test_write_to_another_agents_file_is_rejected_and_reported(specs, plan):
    """The demo's money shot: gpt reaches for claude's file and is refused."""
    client = ScriptedClient(
        script=["I'll just fix this too", "DONE"],
        tool_calls_script=[
            [ToolCall("call_1", "write_file", {"path": "src/auth/tokens.py", "content": "sneaky"})],
            [],
        ],
    )
    host = FakeHost(plan)
    executor = Executor(room_id="r", plan=plan, host=host, clients={"gpt": client})

    events = [e async for e in executor.run_agent(specs[1])]

    assert len(events) == 1
    event = events[0]
    assert event.accepted is False
    assert event.reason and event.reason.startswith("path_not_owned")
    assert event.bytes == 0
    assert "src/auth/tokens.py" not in host.written, "nothing may reach disk on a rejection"


async def test_the_agent_is_told_not_to_retry_a_rejected_path(specs, plan):
    client = ScriptedClient(
        script=["attempt", "DONE"],
        tool_calls_script=[
            [ToolCall("c1", "write_file", {"path": "src/auth/tokens.py", "content": "x"})],
            [],
        ],
    )
    executor = Executor(room_id="r", plan=plan, host=FakeHost(plan), clients={"gpt": client})
    [e async for e in executor.run_agent(specs[1])]

    # Turn 2's prompt carries the tool result the agent will react to.
    tool_results = client.calls[1]["messages"][-1].tool_results
    assert tool_results[0].is_error is True
    assert "REJECTED" in tool_results[0].content
    assert "retrying will be refused" in tool_results[0].content


async def test_malformed_tool_arguments_do_not_crash_the_agent(specs, plan):
    client = ScriptedClient(
        script=["oops", "DONE"],
        tool_calls_script=[
            [ToolCall("c1", "write_file", {"path": "src/auth/tokens.py"})],  # no content
            [],
        ],
    )
    executor = Executor(room_id="r", plan=plan, host=FakeHost(plan), clients={"claude": client})

    events = [e async for e in executor.run_agent(specs[0])]

    assert events == [], "a malformed call is not a write attempt, so no event"
    assert client.calls[1]["messages"][-1].tool_results[0].is_error is True


async def test_agent_with_no_tasks_is_skipped(specs, plan):
    other = specs[0].model_copy(update={"agent_id": "gemini"})
    executor = Executor(room_id="r", plan=plan, host=FakeHost(plan), clients={})

    assert [e async for e in executor.run_agent(other)] == []


async def test_only_the_agents_own_files_appear_in_its_system_prompt(specs, plan):
    client = ScriptedClient(script=["DONE"])
    executor = Executor(room_id="r", plan=plan, host=FakeHost(plan), clients={"claude": client})
    [e async for e in executor.run_agent(specs[0])]

    system = client.calls[0]["system"]
    assert "src/auth/tokens.py" in system
    assert "src/auth/middleware.py" not in system, "do not show an agent files it cannot write"
    assert "def authenticate_user(token: str) -> User | None" in system
