"""Ticket execution: CLAUDE.md's "### Agent tools during execution" clause.

The two tools are real functions here:

  read_file(room, path)                  -> str | None
  write_file(room, ticket, path, content) -> WriteResult

write_file is the enforcement described there — it rejects when the resolved
path escapes the workspace root, the path is not in the calling ticket's
files_owned, or there is no approved plan. execute_room() runs every ticket
respecting lane/depends_on and emits ticket_started/file_written/
ticket_completed onto the shared EventBus (the same one main.py's negotiate
loop publishes onto).

What the tickets write is now real generated code (coding.py), not a
placeholder stub. That matters for the guarantee this project is judged on:
the content comes from a model, and EVERY path that model asks to write is
pushed through write_file below — including one it does not own, which is
refused by this code and reported as `file_written` with accepted=false. The
refusal is therefore a property of the runtime, not a scripted demo event;
the flip side is that in a live room it only appears if a model actually
overreaches. The fixture demo (web/lib/fixtures.ts) still shows it every
time, deterministically.

A ticket whose coding call fails falls back to the placeholder stub rather
than writing nothing, so a room with no API keys still executes end to end.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .coding import TicketCode, implement_ticket, placeholder_code, placeholder_content
from .events import EventBus, utc_now
from .rooms import FileState, Phase, Room
from .schemas import Ticket

log = logging.getLogger(__name__)

#: "agent" (default) codes each ticket with a real model call. "placeholder"
#: skips that and writes stubs — the pre-coding-agent behaviour, kept as an
#: escape hatch for when latency matters more than content (a rehearsal on
#: venue wifi, a demo with no keys).
def execution_mode() -> str:
    return (os.getenv("EXECUTION_MODE", "agent").strip() or "agent").lower()


@dataclass
class WriteResult:
    accepted: bool
    reason: str | None = None


#: (room, ticket) -> the code that ticket wants to write.
Implementer = Callable[[Room, Ticket], Awaitable[TicketCode]]
#: agent_id -> (provider, api_key); main.py passes the room's BYO choice.
ProviderFor = Callable[[str], tuple[str | None, str | None]]


def _escapes_workspace(path: str) -> bool:
    if path.startswith("/") or path.startswith("\\"):
        return True
    if ":" in path:  # e.g. "C:\..." on Windows, or a URL scheme
        return True
    parts = path.replace("\\", "/").split("/")
    depth = 0
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        else:
            depth += 1
    return False


def read_file(room: Room, path: str) -> str | None:
    """CLAUDE.md's `read_file(path) -> str`. Reading is unrestricted within
    the workspace — a ticket may read anything to write its own files
    correctly; only writing is capability-scoped. A path outside the
    workspace reads as missing rather than reaching the real filesystem."""
    if _escapes_workspace(path):
        return None
    f = room.files.get(path)
    return None if f is None else f.content


def write_file(room: Room, ticket: Ticket, path: str, content: str) -> WriteResult:
    """The one enforcement point. Every rule here is a hard reject, not a
    prompt — see CLAUDE.md: "write_file rejects when: the resolved path
    escapes the workspace root; the path is not in the calling ticket's
    files_owned; or there is no approved plan." """
    if room.plan is None or room.plan.status != "approved":
        return WriteResult(accepted=False, reason="no approved plan for this room")
    if _escapes_workspace(path):
        return WriteResult(accepted=False, reason=f"path escapes workspace root: {path!r}")
    if path not in ticket.files_owned:
        # Name the real owner when there is one — same shape as the contract's
        # own sample event, fixtures/events/file_written_rejected.json.
        owner = next((t for t in room.tickets if path in t.files_owned), None)
        whose = (
            f"belongs to ticket {owner.ticket_id} (owner {owner.assigned_agent})"
            if owner is not None
            else "is not owned by any ticket"
        )
        return WriteResult(
            accepted=False,
            reason=(
                f"path_not_owned: {path} {whose}. "
                f"Agent {ticket.assigned_agent} owns: {', '.join(ticket.files_owned) or 'nothing'}."
            ),
        )

    room.files[path] = FileState(content=content, last_written_by=ticket.assigned_agent)
    return WriteResult(accepted=True)


def agent_implementer(provider_for: ProviderFor | None = None) -> Implementer:
    """The default: each ticket's files are written by a real model call,
    with the repo it can read handed over through read_file."""

    async def implement(room: Room, ticket: Ticket) -> TicketCode:
        if execution_mode() == "placeholder":
            return placeholder_code(ticket)
        provider, api_key = provider_for(ticket.assigned_agent) if provider_for else (None, None)
        return await implement_ticket(
            ticket=ticket,
            all_tickets=list(room.tickets),
            plan=room.plan,
            context=room.project_context or None,
            read_file=lambda path: read_file(room, path),
            paths=list(room.files.keys()),
            provider=provider,
            api_key=api_key,
        )

    return implement


async def placeholder_implementer(room: Room, ticket: Ticket) -> TicketCode:
    """No model call at all. Used by tests, and by EXECUTION_MODE=placeholder."""
    return placeholder_code(ticket)


async def _run_ticket(room: Room, ticket: Ticket, bus: EventBus, implement: Implementer) -> None:
    await bus.publish(
        room.room_id,
        {
            "type": "ticket_started",
            "ticket_id": ticket.ticket_id,
            "agent_id": ticket.assigned_agent,
            "ts": utc_now(),
        },
    )
    for t in room.tickets:
        if t.ticket_id == ticket.ticket_id:
            t.status = "running"

    try:
        code = await implement(room, ticket)
    except Exception as exc:  # noqa: BLE001 — a coder blowing up fails one ticket, not the room
        log.exception("coding failed for %s", ticket.ticket_id)
        code = placeholder_code(ticket, error=str(exc))

    if code.error:
        await bus.publish(
            room.room_id,
            {
                "type": "error",
                "where": f"execute.{ticket.ticket_id}",
                "detail": f"coding agent unavailable, wrote placeholders: {code.error}",
                "ts": utc_now(),
            },
        )

    # Every path the model asked for, in its own order — an unowned one is
    # refused below rather than filtered out here. Then any file the ticket
    # owns that the model did not produce, so a ticket always writes its own
    # files.
    to_write = dict(code.files)
    for path in ticket.files_owned:
        to_write.setdefault(path, placeholder_content(ticket, path))

    accepted_paths: set[str] = set()
    for path, content in to_write.items():
        result = write_file(room, ticket, path, content)
        if result.accepted:
            accepted_paths.add(path)
        await bus.publish(
            room.room_id,
            {
                "type": "file_written",
                "ticket_id": ticket.ticket_id,
                "agent_id": ticket.assigned_agent,
                "path": path,
                "accepted": result.accepted,
                "reason": result.reason,
                "ts": utc_now(),
            },
        )

    # A refused write is the runtime working as designed, not the ticket
    # failing: the ticket is done when everything it owns has been written.
    ok = all(path in accepted_paths for path in ticket.files_owned)
    status = "done" if ok else "failed"
    for t in room.tickets:
        if t.ticket_id == ticket.ticket_id:
            t.status = status
    await bus.publish(
        room.room_id,
        {"type": "ticket_completed", "ticket_id": ticket.ticket_id, "status": status, "ts": utc_now()},
    )


async def execute_room(
    room: Room, bus: EventBus, *, implement: Implementer | None = None
) -> None:
    """Runs every ticket to completion, respecting lane/depends_on: all
    tickets whose dependencies are already satisfied run concurrently (their
    coding calls included, so two agents really do write at the same time);
    a ticket with unmet depends_on waits. CLAUDE.md's cut-order item #2
    ("run parallel-only") is not applied — sequential ordering is what lets
    a later ticket read the code an earlier one actually wrote.
    """
    run = implement or agent_implementer()
    remaining = {t.ticket_id: t for t in room.tickets}
    done: set[str] = set()

    while remaining:
        ready = [t for t in remaining.values() if all(dep in done for dep in t.depends_on)]
        if not ready:
            # a cycle or a dangling depends_on — shouldn't happen given the
            # validator, but don't hang the room if it does.
            for t in remaining.values():
                t.status = "failed"
                await bus.publish(
                    room.room_id,
                    {"type": "ticket_completed", "ticket_id": t.ticket_id, "status": "failed", "ts": utc_now()},
                )
            return

        await asyncio.gather(*(_run_ticket(room, t, bus, run) for t in ready))
        for t in ready:
            done.add(t.ticket_id)
            del remaining[t.ticket_id]

    room.phase = Phase.DONE
