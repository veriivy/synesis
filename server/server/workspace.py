"""The write sandbox. This is the technical claim in the pitch, so it lives in one file.

Agents are untrusted processes holding a capability — a list of paths — and nothing
else. Every write in the entire system goes through `Workspace.write_file`, which
refuses unless all of these hold:

  1. the room has a human-APPROVED plan                     -> no_approved_plan
  2. the resolved absolute path stays inside the room root   -> path_escape
  3. the path is in the calling agent's files_owned          -> path_not_owned
  4. the path is not inside .git                             -> protected_path

Rule 4 is not in the original spec but belongs with the others: an agent that can write
`.git/hooks/post-commit` has escaped the sandbox through a door the other three rules
leave open.

Every attempt — accepted or rejected — returns a WriteOutcome and produces a
`file_written` event. Rejections are not swallowed; they are the point.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from engine.schemas import Workplan
from engine.tools import WriteOutcome

log = logging.getLogger(__name__)

MAX_WRITE_BYTES = 512 * 1024


@dataclass
class Workspace:
    """A per-room checkout of the demo target repo, plus the ownership rules over it.

    `plan` is None until a human approves one. That is deliberate: with no approved
    plan there is no capability list, so there is nothing an agent is allowed to write.
    """

    room_id: str
    root: Path
    plan: Workplan | None = None
    plan_approved: bool = False

    def __post_init__(self) -> None:
        # Resolve once, at construction. If the root itself is reached through a
        # symlink, every later containment check must compare against the real path or
        # the check is trivially defeated.
        self.root = Path(self.root).resolve()

    # --- path handling -------------------------------------------------------

    def _resolve(self, path: str) -> tuple[Path | None, str | None]:
        """Resolve a repo-relative path inside the root, or explain the refusal.

        Returns (resolved_path, None) on success, (None, reason) on refusal.
        """
        if not path or not path.strip():
            return None, "path_escape: empty path"

        # A NUL byte truncates the path at the OS layer and can make a rejected path
        # resolve to a different file than the one we checked.
        if "\x00" in path:
            return None, "path_escape: path contains a NUL byte"

        candidate = Path(path.replace("\\", "/"))

        # An absolute path, or a Windows drive/UNC prefix, ignores the root entirely.
        if candidate.is_absolute() or candidate.drive or candidate.anchor:
            return None, f"path_escape: {path} is absolute"

        try:
            resolved = (self.root / candidate).resolve()
        except (OSError, RuntimeError) as exc:  # loops, name too long
            return None, f"path_escape: {path} could not be resolved ({exc})"

        if resolved != self.root and self.root not in resolved.parents:
            return None, "path_escape: resolved path leaves the room workspace root."

        return resolved, None

    def relative(self, resolved: Path) -> str:
        """Repo-relative POSIX form — the spelling the plan and the events use."""
        return resolved.relative_to(self.root).as_posix()

    # --- the enforcement point ----------------------------------------------

    def write_file(self, agent_id: str, path: str, content: str) -> WriteOutcome:
        """The only way anything is ever written into a room. Never raises."""

        # Rule 1 — no approved plan means no capabilities at all.
        if self.plan is None or not self.plan_approved:
            return WriteOutcome(
                accepted=False,
                reason=(
                    f"no_approved_plan: room {self.room_id} has no human-approved plan. "
                    "Writes are refused until a human approves."
                ),
            )

        # Rule 2 — containment.
        resolved, reason = self._resolve(path)
        if resolved is None:
            log.warning("REJECTED write by %s: %s", agent_id, reason)
            return WriteOutcome(accepted=False, reason=reason)

        rel = self.relative(resolved)

        # Rule 4 — git's own metadata is never an agent-writable file.
        if rel == ".git" or rel.startswith(".git/"):
            reason = f"protected_path: {rel} is git metadata and is never writable."
            log.warning("REJECTED write by %s: %s", agent_id, reason)
            return WriteOutcome(accepted=False, reason=reason)

        # Rule 3 — the capability check.
        owned = self.plan.files_owned_by(agent_id)
        if rel not in owned:
            owner_task = self._owning_task(rel)
            if owner_task is not None:
                detail = f"{rel} belongs to task {owner_task[0]} (owner {owner_task[1]})."
            else:
                detail = f"{rel} is not owned by any task in the approved plan."
            reason = (
                f"path_not_owned: {detail} Agent {agent_id} owns: "
                f"{', '.join(sorted(owned)) or '(nothing)'}."
            )
            log.warning("REJECTED write by %s: %s", agent_id, reason)
            return WriteOutcome(accepted=False, reason=reason)

        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return WriteOutcome(
                accepted=False,
                reason=(
                    f"size_limit: {rel} is {len(encoded)} bytes, over the "
                    f"{MAX_WRITE_BYTES}-byte cap."
                ),
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(encoded)
        except OSError as exc:
            return WriteOutcome(accepted=False, reason=f"io_error: {exc}")

        log.info("accepted write by %s: %s (%d bytes)", agent_id, rel, len(encoded))
        return WriteOutcome(accepted=True, bytes_written=len(encoded))

    def _owning_task(self, rel: str) -> tuple[str, str] | None:
        if self.plan is None:
            return None
        for task in self.plan.tasks:
            if rel in task.files_owned:
                return task.task_id, task.owner_agent
        return None

    # --- reads ---------------------------------------------------------------

    def read_file(self, agent_id: str, path: str) -> str:
        """Reads are open across the repository — ownership restricts writes only.

        Containment still applies: an agent may not read its way out of the workspace.
        """
        resolved, reason = self._resolve(path)
        if resolved is None:
            return f"error: {reason}"
        if not resolved.is_file():
            return f"error: {self.relative(resolved)} does not exist"
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"error: could not read {path}: {exc}"

    # --- repo map ------------------------------------------------------------

    IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".pytest_cache"}

    def repo_map(self, max_entries: int = 400) -> str:
        """A flat listing with sizes, for the agents' system prompts.

        Flat rather than a tree: agents cite paths, and a listing they can copy exactly
        produces fewer invented paths than an indented tree does.
        """
        lines: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in self.IGNORED_DIRS)
            for name in sorted(filenames):
                full = Path(dirpath) / name
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                lines.append(f"  {self.relative(full.resolve())} ({size}b)")
                if len(lines) >= max_entries:
                    lines.append(f"  ... truncated at {max_entries} files")
                    return "\n".join(lines)
        return "\n".join(lines) or "  (empty repository)"

    def files_touched(self, agent_id: str) -> list[str]:
        if self.plan is None:
            return []
        return sorted(self.plan.files_owned_by(agent_id))
