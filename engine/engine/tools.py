"""The two tools agents get, and the host interface that backs them.

The engine deliberately does NOT implement `write_file`. It declares the interface and
calls it; `/server` implements it against a sandboxed per-room workspace. Agents are
untrusted processes, so the component that runs them is not the component that decides
what they may touch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .providers import ToolSpec

READ_FILE = ToolSpec(
    name="read_file",
    description=(
        "Read a file from the shared repository. Any path in the repository may be "
        "read — ownership restricts writes, not reads. Returns the file's contents, or "
        "an error string if it does not exist."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repository-relative path, e.g. src/auth/session.py",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)

WRITE_FILE = ToolSpec(
    name="write_file",
    description=(
        "Write the complete contents of a file you own. You may only write paths listed "
        "in your own tasks' files_owned; any other path is rejected by the workspace and "
        "the rejection is shown to the humans. Pass whole file contents, never a diff."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repository-relative path you own.",
            },
            "content": {
                "type": "string",
                "description": "The complete new contents of the file.",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)

AGENT_TOOLS: list[ToolSpec] = [READ_FILE, WRITE_FILE]


@dataclass
class WriteOutcome:
    """The result of one write attempt.

    `reason` is prefixed with the rule that fired — `no_approved_plan`, `path_escape`,
    or `path_not_owned` — so the UI can style rejections by cause and the demo can
    point at the specific rule.
    """

    accepted: bool
    reason: str | None = None
    bytes_written: int = 0


class ToolHost(Protocol):
    """Implemented by the server. The engine holds only this interface.

    Neither method may raise for ordinary failure: a rejected write is a value, not an
    exception, because the agent has to see it and react.
    """

    def read_file(self, agent_id: str, path: str) -> str: ...

    def write_file(self, agent_id: str, path: str, content: str) -> WriteOutcome: ...
