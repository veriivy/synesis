"""The disjoint-file-ownership guarantee for ticket decomposition.

CLAUDE.md: "K2 proposes files_owned per ticket. Then we validate in code,
not by prompt: assert that no two parallel-lane tickets share any path in
files_owned. On overlap, demote the later ticket to sequential with a
depends_on edge. Reject and regenerate if K2 returns a ticket with an empty
files_owned. The guarantee that parallel agents cannot collide comes from
this assertion, not from asking a model nicely."

This module is that assertion. It never talks to a model.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Ticket


class EmptyFilesOwnedError(Exception):
    """Raised when K2 proposes a ticket with no files_owned. The caller
    (app/k2/ticketing.py) is expected to reject and regenerate, not fix
    this up here — an empty ownership claim isn't something code should
    guess at."""

    def __init__(self, ticket_ids: list[str]):
        self.ticket_ids = ticket_ids
        super().__init__(f"tickets with empty files_owned: {ticket_ids}")


@dataclass
class Adjustment:
    """A record of a demotion the validator made, for the tickets_created
    event / demo narration — "show a write getting rejected" has a sibling
    moment here: show a ticket getting demoted."""

    ticket_id: str
    depends_on: list[str]
    reason: str


def validate_and_fix_tickets(tickets: list[Ticket]) -> tuple[list[Ticket], list[Adjustment]]:
    """Mutates `tickets` in place (lane/depends_on only — never files_owned)
    so that no two parallel-lane tickets share a path, then asserts that
    invariant holds. Returns the same list and a log of what was demoted.

    Processes tickets in the order given (i.e. the order K2 proposed them
    in), so a chain of overlapping tickets each depends on the most recent
    prior owner of the file it collides with, not all on the first one —
    that's what actually prevents a race between tickets 2 and 3 once both
    have been pushed behind ticket 1.
    """
    empty = [t.ticket_id for t in tickets if not t.files_owned]
    if empty:
        raise EmptyFilesOwnedError(empty)

    owner_of: dict[str, str] = {}
    adjustments: list[Adjustment] = []

    for ticket in tickets:
        if ticket.lane == "parallel":
            conflicts = sorted({owner_of[path] for path in ticket.files_owned if path in owner_of})
            if conflicts:
                ticket.lane = "sequential"
                ticket.depends_on = sorted(set(ticket.depends_on) | set(conflicts))
                adjustments.append(
                    Adjustment(
                        ticket_id=ticket.ticket_id,
                        depends_on=conflicts,
                        reason=f"files_owned overlaps ticket(s) {conflicts}",
                    )
                )
        for path in ticket.files_owned:
            owner_of[path] = ticket.ticket_id

    _assert_parallel_disjoint(tickets)
    return tickets, adjustments


def _assert_parallel_disjoint(tickets: list[Ticket]) -> None:
    seen: dict[str, str] = {}
    for ticket in tickets:
        if ticket.lane != "parallel":
            continue
        for path in ticket.files_owned:
            if path in seen:
                raise AssertionError(
                    f"parallel-lane collision on {path!r}: "
                    f"tickets {seen[path]!r} and {ticket.ticket_id!r}"
                )
            seen[path] = ticket.ticket_id
