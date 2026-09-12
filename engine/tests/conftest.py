from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from engine.providers import Completion, Message, ToolSpec
from engine.schemas import AgentSpec, Requirement


@dataclass
class ScriptedClient:
    """A fake LLMClient that replays canned completions.

    Lets the loop be tested for its control flow — round counting, premature-convergence
    handling, deadlock, ownership validation — with no network and no API keys.
    """

    provider_key: str = "fake"
    model: str = "fake-1"
    script: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    tool_calls_script: list[list] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> Completion:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        index = len(self.calls) - 1
        text = self.script[min(index, len(self.script) - 1)] if self.script else ""
        calls = (
            self.tool_calls_script[index]
            if index < len(self.tool_calls_script)
            else []
        )
        return Completion(text=text, tool_calls=list(calls), model=self.model)

    async def list_models(self) -> list[str]:
        return [self.model]


@pytest.fixture
def specs() -> list[AgentSpec]:
    return [
        AgentSpec(
            agent_id="claude",
            provider="anthropic",
            model="fake-1",
            user_id="u1",
            display_name="Audrey",
            requirements=[
                Requirement(req_id="r1", text="No external services.", priority="must-have"),
                Requirement(req_id="r2", text="Revocation is immediate.", priority="must-have"),
            ],
        ),
        AgentSpec(
            agent_id="gpt",
            provider="openai",
            model="fake-1",
            user_id="u2",
            display_name="Sam",
            requirements=[
                Requirement(req_id="r4", text="Auth must be stateless.", priority="must-have"),
                Requirement(req_id="r5", text="No cookies.", priority="must-have"),
            ],
        ),
    ]


def verdict_json(**kwargs) -> str:
    payload = {
        "converged": False,
        "premature": False,
        "conflicts_remaining": [],
        "note": "",
    }
    payload.update(kwargs)
    return json.dumps(payload)


def plan_json(**overrides) -> str:
    """A valid two-agent plan with disjoint ownership."""
    payload = {
        "plan_id": "plan_test",
        "tasks": [
            {
                "task_id": "t1",
                "title": "Token store",
                "description": "d",
                "owner_agent": "claude",
                "owner_user": "u1",
                "files_owned": ["src/auth/tokens.py"],
                "depends_on": [],
            },
            {
                "task_id": "t2",
                "title": "Middleware",
                "description": "d",
                "owner_agent": "gpt",
                "owner_user": "u2",
                "files_owned": ["src/auth/middleware.py"],
                "depends_on": ["t1"],
            },
        ],
        "interface_contracts": [
            {
                "contract_id": "c1",
                "name": "authenticate_user",
                "kind": "function",
                "signature": "def authenticate_user(token: str) -> User | None",
                "producer_task": "t1",
                "consumer_tasks": ["t2"],
                "notes": "",
            }
        ],
        "concessions": [
            {
                "agent": "gpt",
                "gave_up": "stateless JWT",
                "accepted": "opaque tokens",
                "reason": "r2 is a must-have for u1",
            }
        ],
        "unresolved": [],
    }
    payload.update(overrides)
    return json.dumps(payload)
