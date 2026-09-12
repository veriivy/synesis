"""The execution phase: approved plan -> agents write code into files they own.

Each agent runs its own tool-calling loop against the workspace. Every write attempt,
accepted or rejected, becomes a `file_written` event. Rejections are not errors to be
swallowed — they are the observable proof that ownership is enforced structurally
rather than negotiated after the fact, and they go straight to the UI.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from . import prompts
from .config import PROVIDERS
from .providers import LLMClient, Message, ProviderError, ToolResult
from .schemas import AgentSpec, Event, FileWrittenEvent, Workplan
from .tools import AGENT_TOOLS, ToolHost

log = logging.getLogger(__name__)

MAX_TOOL_TURNS = 24


def _tasks_block(plan: Workplan, agent_id: str) -> str:
    tasks = plan.tasks_for(agent_id)
    if not tasks:
        return "  (none — you have nothing to implement)"
    lines = []
    for t in tasks:
        deps = f" (after {', '.join(t.depends_on)})" if t.depends_on else ""
        lines.append(f"  {t.task_id}: {t.title}{deps}\n    {t.description}")
    return "\n".join(lines)


def _files_block(plan: Workplan, agent_id: str) -> str:
    owned = sorted(plan.files_owned_by(agent_id))
    return "\n".join(f"  {p}" for p in owned) or "  (none)"


def _contracts_block(plan: Workplan, agent_id: str) -> str:
    """Contracts this agent produces or consumes. Others are noise."""
    my_tasks = {t.task_id for t in plan.tasks_for(agent_id)}
    relevant = [
        c
        for c in plan.interface_contracts
        if c.producer_task in my_tasks or my_tasks & set(c.consumer_tasks)
    ]
    if not relevant:
        return "  (none)"
    lines = []
    for c in relevant:
        role = "you produce" if c.producer_task in my_tasks else "you consume"
        lines.append(f"  {c.contract_id} [{c.kind}, {role}] {c.signature}")
        if c.notes:
            lines.append(f"    {c.notes}")
    return "\n".join(lines)


class Executor:
    """Runs one agent's implementation loop against the sandboxed workspace."""

    def __init__(
        self,
        *,
        room_id: str,
        plan: Workplan,
        host: ToolHost,
        clients: dict[str, LLMClient],
    ) -> None:
        self.room_id = room_id
        self.plan = plan
        self.host = host
        self.clients = clients

    async def run_agent(self, spec: AgentSpec) -> AsyncIterator[Event]:
        agent_id = spec.agent_id
        if not self.plan.tasks_for(agent_id):
            log.info("agent %s has no tasks; skipping execution", agent_id)
            return

        label = PROVIDERS[spec.provider].label if spec.provider in PROVIDERS else agent_id
        system = prompts.EXECUTOR_SYSTEM.format(
            label=label,
            tasks_block=_tasks_block(self.plan, agent_id),
            files_block=_files_block(self.plan, agent_id),
            contracts_block=_contracts_block(self.plan, agent_id),
        )

        client = self.clients[agent_id]
        messages: list[Message] = [
            Message(
                role="user",
                content=(
                    "Implement your tasks now. Read what you need, then write each file "
                    "you own with its complete contents. Reply DONE when finished."
                ),
            )
        ]

        for turn in range(MAX_TOOL_TURNS):
            try:
                completion = await client.complete(
                    system=system, messages=messages, tools=AGENT_TOOLS, max_tokens=8192
                )
            except ProviderError as exc:
                log.error("execution turn failed for %s: %s", agent_id, exc)
                return

            if not completion.wants_tools:
                log.info(
                    "agent %s finished execution after %d turns: %s",
                    agent_id,
                    turn + 1,
                    completion.text[:120],
                )
                return

            messages.append(
                Message(
                    role="assistant",
                    content=completion.text,
                    tool_calls=completion.tool_calls,
                )
            )

            results: list[ToolResult] = []
            for call in completion.tool_calls:
                result, event = self._dispatch(agent_id, call.call_id, call.name, call.arguments)
                results.append(result)
                if event is not None:
                    yield event

            messages.append(Message(role="user", tool_results=results))

        log.warning("agent %s hit the %d-turn cap", agent_id, MAX_TOOL_TURNS)

    def _dispatch(
        self, agent_id: str, call_id: str, name: str, args: dict
    ) -> tuple[ToolResult, Event | None]:
        """Execute one tool call. Returns the result and any event to emit.

        Never raises. An agent that calls a tool wrongly gets an error string back and
        another chance; it does not take the room down.
        """
        path = str(args.get("path", "")).strip()

        if name == "read_file":
            if not path:
                return ToolResult(call_id, "error: 'path' is required", is_error=True), None
            try:
                return ToolResult(call_id, self.host.read_file(agent_id, path)), None
            except Exception as exc:  # host decides what is readable; never trust it not to raise
                return ToolResult(call_id, f"error: {exc}", is_error=True), None

        if name == "write_file":
            content = args.get("content")
            if not path or not isinstance(content, str):
                return (
                    ToolResult(
                        call_id,
                        "error: 'path' and 'content' (string) are both required",
                        is_error=True,
                    ),
                    None,
                )

            outcome = self.host.write_file(agent_id, path, content)
            event = FileWrittenEvent(
                room_id=self.room_id,
                agent_id=agent_id,
                path=path,
                bytes=outcome.bytes_written,
                accepted=outcome.accepted,
                reason=outcome.reason,
            )
            if outcome.accepted:
                message = f"wrote {path} ({outcome.bytes_written} bytes)"
            else:
                # Tell the agent plainly that it was refused and not to retry. Left
                # vague, models loop on the same rejected path until the turn cap.
                message = (
                    f"REJECTED: {outcome.reason}\n"
                    "This was refused by the workspace, not by another agent, and "
                    "retrying will be refused identically. Continue with the files you "
                    "do own."
                )
            return ToolResult(call_id, message, is_error=not outcome.accepted), event

        return ToolResult(call_id, f"error: unknown tool {name!r}", is_error=True), None
