"""The provider interface. One shape in, one shape out, regardless of vendor.

Everything above this layer — the negotiation loop, the moderator, the execution
phase — is written against these types and has no idea which vendor answered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    """A tool offered to an agent. JSON Schema, vendor-neutral."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """One turn.

    role: "user" | "assistant"
    Assistant turns that requested tools carry them in `tool_calls`; the following user
    turn carries the matching `tool_results`. Each provider client translates this into
    its own wire format.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class ProviderError(RuntimeError):
    """A provider call failed after retries. Carries the provider key for the UI."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


class LLMClient(Protocol):
    """What every provider client must implement. Two methods, nothing vendor-shaped."""

    provider_key: str
    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> Completion:
        """One turn. Must time out, must retry, must raise ProviderError on failure."""
        ...

    async def list_models(self) -> list[str]:
        """Model ids this key can actually reach. Used to confirm ids at runtime."""
        ...
