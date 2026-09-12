"""Ticket execution: CLAUDE.md's "### Agent tools during execution" clause.

write_file() is the actual enforcement described there — it rejects when
the resolved path escapes the workspace root, the path is not in the
calling ticket's files_owned, or there is no approved plan. execute_room()
calls it for every ticket, respecting lane/depends_on, and emits
ticket_started/file_written/ticket_completed onto the shared EventBus (the
same one main.py's negotiate loop publishes onto).

No real coding agent exists in this slice (agents.py only drafts/revises
PoAs), so the "content" a ticket writes is a placeholder stub, not
generated code. What's real here is the enforcement and the scheduling
(parallel tickets run concurrently; sequential ones wait on depends_on) —
not the code the tickets produce. write_file's rejection paths are covered
by tests/test_execution.py directly, not staged as a scripted demo moment,
since the ticket validator already guarantees the tickets it's given here
never legitimately collide.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .events import EventBus, utc_now
from .rooms import FileState, Phase, Room
from .schemas import Ticket


@dataclass
class WriteResult:
    accepted: bool
    reason: str | None = None


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
        return WriteResult(
            accepted=False,
            reason=f"ticket {ticket.ticket_id} does not own {path!r} (owns {ticket.files_owned})",
        )

    room.files[path] = FileState(content=content, last_written_by=ticket.assigned_agent)
    return WriteResult(accepted=True)


def _placeholder_content(ticket: Ticket, path: str) -> str:
    return (
        f'"""{ticket.title}. Owned by {ticket.assigned_agent} (ticket {ticket.ticket_id})."""\n\n'
        f"# TODO: implement — {ticket.description}\n"
    )


async def _run_ticket(room: Room, ticket: Ticket, bus: EventBus) -> None:
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

    ok = True
    for path in ticket.files_owned:
        result = write_file(room, ticket, path, _placeholder_content(ticket, path))
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
        ok = ok and result.accepted

    status = "done" if ok else "failed"
    for t in room.tickets:
        if t.ticket_id == ticket.ticket_id:
            t.status = status
    await bus.publish(
        room.room_id,
        {"type": "ticket_completed", "ticket_id": ticket.ticket_id, "status": status, "ts": utc_now()},
    )


async def execute_room(room: Room, bus: EventBus) -> None:
    """Runs every ticket to completion, respecting lane/depends_on: all
    tickets whose dependencies are already satisfied run concurrently; a
    ticket with unmet depends_on waits. CLAUDE.md's cut-order item #2 ("run
    parallel-only, still show depends_on in the UI") is not applied here —
    since this executor only ever produces placeholder writes, actually
    respecting sequential ordering costs nothing and is more honest than
    faking it.
    """
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

        await asyncio.gather(*(_run_ticket(room, t, bus) for t in ready))
        for t in ready:
            done.add(t.ticket_id)
            del remaining[t.ticket_id]

    room.phase = Phase.DONE
