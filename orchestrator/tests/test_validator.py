from app.k2.validator import EmptyFilesOwnedError, validate_and_fix_tickets
from app.schemas import Ticket


def _ticket(ticket_id: str, files: list[str], **kw) -> Ticket:
    return Ticket(
        ticket_id=ticket_id,
        plan_id="plan-1",
        title=ticket_id,
        description="",
        assigned_agent=kw.pop("assigned_agent", "a1"),
        files_owned=files,
        **kw,
    )


def test_disjoint_tickets_stay_parallel():
    tickets = [
        _ticket("t1", ["src/a.py"]),
        _ticket("t2", ["src/b.py"]),
    ]

    fixed, adjustments = validate_and_fix_tickets(tickets)

    assert adjustments == []
    assert all(t.lane == "parallel" for t in fixed)


def test_overlap_demotes_later_ticket_and_adds_dependency():
    tickets = [
        _ticket("t1", ["src/auth.py"]),
        _ticket("t2", ["src/auth.py"]),
    ]

    fixed, adjustments = validate_and_fix_tickets(tickets)

    assert fixed[0].lane == "parallel"
    assert fixed[1].lane == "sequential"
    assert fixed[1].depends_on == ["t1"]
    assert len(adjustments) == 1
    assert adjustments[0].ticket_id == "t2"
    assert adjustments[0].depends_on == ["t1"]


def test_chain_of_overlaps_depends_on_most_recent_owner_not_the_first():
    # t2 collides with t1 on auth.py -> depends on t1.
    # t3 also touches auth.py -> should depend on t2 (the current owner in
    # the dependency chain), not fall back to t1 and race t2.
    tickets = [
        _ticket("t1", ["src/auth.py"]),
        _ticket("t2", ["src/auth.py"]),
        _ticket("t3", ["src/auth.py", "src/other.py"]),
    ]

    fixed, adjustments = validate_and_fix_tickets(tickets)

    assert fixed[1].depends_on == ["t1"]
    assert fixed[2].depends_on == ["t2"]
    assert [a.ticket_id for a in adjustments] == ["t2", "t3"]


def test_preexisting_sequential_ticket_is_not_touched_but_still_claims_its_files():
    tickets = [
        _ticket("t1", ["src/a.py"], lane="sequential", depends_on=["t0"]),
        _ticket("t2", ["src/a.py"]),
    ]

    fixed, adjustments = validate_and_fix_tickets(tickets)

    # t1 was already sequential and shouldn't get a spurious adjustment logged
    assert fixed[0].depends_on == ["t0"]
    # t2 still has to yield to t1's prior claim on src/a.py
    assert fixed[1].lane == "sequential"
    assert fixed[1].depends_on == ["t1"]
    assert len(adjustments) == 1
    assert adjustments[0].ticket_id == "t2"


def test_multi_file_ticket_can_collide_with_two_different_owners():
    tickets = [
        _ticket("t1", ["src/a.py"]),
        _ticket("t2", ["src/b.py"]),
        _ticket("t3", ["src/a.py", "src/b.py"]),
    ]

    fixed, adjustments = validate_and_fix_tickets(tickets)

    assert fixed[2].lane == "sequential"
    assert fixed[2].depends_on == ["t1", "t2"]


def test_empty_files_owned_raises_instead_of_guessing():
    tickets = [_ticket("t1", [])]

    try:
        validate_and_fix_tickets(tickets)
        assert False, "expected EmptyFilesOwnedError"
    except EmptyFilesOwnedError as exc:
        assert exc.ticket_ids == ["t1"]
