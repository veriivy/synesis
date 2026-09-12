"""The coding agent: block parsing, the retry/fallback chain, and the fact
that a model's output is pushed through write_file rather than trusted.

No network and no key: every model call here is a scripted `chat_fn`, the
same way tests/test_ticketing.py fakes `chat`. What is real is the parser,
the context builder, the fallback rules, and execute_room's enforcement of
whatever the coder hands back.
"""

from __future__ import annotations

import pytest

from orchestrator.coding import (
    TicketCode,
    build_repo_view,
    implement_ticket_sync,
    parse_file_blocks,
    placeholder_code,
)
from orchestrator.events import EventBus
from orchestrator.execution import execute_room, read_file
from orchestrator.rooms import FileState, Room
from orchestrator.schemas import FinalPlan, Ticket


def _approved_plan() -> FinalPlan:
    return FinalPlan(
        plan_id="plan-1",
        rounds_used=1,
        summary="both mechanisms behind one authenticate_user",
        steps=[],
        approvals={"u1": True, "u2": True},
        status="approved",
    )


def _ticket(ticket_id: str, files: list[str], **kw) -> Ticket:
    return Ticket(
        ticket_id=ticket_id,
        plan_id="plan-1",
        title=f"ticket {ticket_id}",
        description="do the thing",
        assigned_agent=kw.pop("assigned_agent", "a1"),
        files_owned=files,
        **kw,
    )


def _room(paths: dict[str, str] | None = None) -> Room:
    room = Room(room_id="r1")
    room.plan = _approved_plan()
    for path, content in (paths or {}).items():
        room.files[path] = FileState(content=content)
    return room


# --- parsing ---


def test_parse_file_blocks_reads_several_files():
    raw = (
        "=== FILE: src/auth/session.py ===\n"
        "def issue_session():\n    return 1\n"
        "=== END FILE ===\n"
        "=== FILE: src/auth/jwt.py ===\n"
        "def verify():\n    return None\n"
        "=== END FILE ===\n"
    )

    files = parse_file_blocks(raw)

    assert list(files) == ["src/auth/session.py", "src/auth/jwt.py"]
    assert files["src/auth/session.py"].startswith("def issue_session():")


def test_parse_file_blocks_keeps_a_truncated_tail_instead_of_losing_everything():
    """The reason this is not JSON: a cut-off response costs the last file,
    not the whole ticket."""
    raw = (
        "=== FILE: a.py ===\nx = 1\n=== END FILE ===\n"
        "=== FILE: b.py ===\ny = 2  # cut off here, no footer\n"
    )

    files = parse_file_blocks(raw)

    assert files["a.py"] == "x = 1\n"
    assert files["b.py"].startswith("y = 2")


def test_parse_file_blocks_tolerates_prose_and_markdown_fences():
    raw = (
        "Sure! Here are the files:\n\n"
        "=== FILE: `src/app.py` ===\n"
        "```python\n"
        "from fastapi import FastAPI\n"
        "```\n"
        "=== END FILE ===\n"
    )

    files = parse_file_blocks(raw)

    assert files == {"src/app.py": "from fastapi import FastAPI\n"}


def test_parse_file_blocks_drops_an_empty_block_rather_than_blanking_a_file():
    assert parse_file_blocks("=== FILE: a.py ===\n\n=== END FILE ===\n") == {}


def test_parse_file_blocks_returns_nothing_for_a_response_with_no_blocks():
    assert parse_file_blocks("I cannot help with that.") == {}


def test_parse_file_blocks_accepts_a_files_json_object():
    raw = '{"files": {"src/auth/jwt.py": "def encode():\\n    return 1\\n"}}'
    assert parse_file_blocks(raw) == {"src/auth/jwt.py": "def encode():\n    return 1\n"}


# --- context building ---


def test_build_repo_view_always_includes_owned_files_even_over_budget():
    room = _room({"owned.py": "O" * 500, "other.py": "X" * 500})

    view = build_repo_view(
        paths=list(room.files),
        read_file=lambda p: read_file(room, p),
        owned=["owned.py"],
        limit=10,  # smaller than either file
    )

    assert "--- owned.py ---" in view
    assert "--- other.py ---" not in view
    assert "not shown" in view and "other.py" in view


def test_read_file_refuses_to_escape_the_workspace():
    room = _room({"src/a.py": "x = 1\n"})

    assert read_file(room, "src/a.py") == "x = 1\n"
    assert read_file(room, "../../etc/passwd") is None
    assert read_file(room, "/etc/passwd") is None


# --- the retry/fallback ---


def _implement(ticket: Ticket, replies: list[str], room: Room | None = None) -> TicketCode:
    room = room or _room({"src/a.py": "old\n"})
    calls: list[dict] = []

    def chat_fn(**kwargs) -> str:
        calls.append(kwargs)
        if not replies:
            raise AssertionError("coder called more times than the script allows")
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    code = implement_ticket_sync(
        ticket=ticket,
        all_tickets=[ticket],
        plan=room.plan,
        context="shared context",
        read_file=lambda p: read_file(room, p),
        paths=list(room.files),
        chat_fn=chat_fn,
    )
    code.calls = calls  # type: ignore[attr-defined]
    return code


def test_implement_ticket_returns_the_models_files():
    ticket = _ticket("t1", ["src/a.py"])

    code = _implement(ticket, ["=== FILE: src/a.py ===\nnew = True\n=== END FILE ===\n"])

    assert code.source == "agent"
    assert code.files == {"src/a.py": "new = True\n"}
    assert code.error is None


def test_implement_ticket_retries_once_on_an_unparseable_reply():
    ticket = _ticket("t1", ["src/a.py"])

    code = _implement(
        ticket,
        ["sorry, no blocks here", "=== FILE: src/a.py ===\nsecond = True\n=== END FILE ===\n"],
    )

    assert code.source == "agent"
    assert code.files == {"src/a.py": "second = True\n"}
    assert len(code.calls) == 2  # type: ignore[attr-defined]


def test_implement_ticket_falls_back_to_a_placeholder_after_two_bad_replies():
    ticket = _ticket("t1", ["src/a.py"])

    code = _implement(ticket, ["nope", "still nope"])

    assert code.source == "placeholder"
    assert "no parseable file blocks" in code.error
    assert "TODO: implement" in code.files["src/a.py"]


def test_implement_ticket_falls_back_when_the_provider_cannot_be_called():
    """No key, no network, provider down — the room still executes."""
    ticket = _ticket("t1", ["src/a.py"])

    code = _implement(ticket, [RuntimeError("no API key for openai")])

    assert code.source == "placeholder"
    assert "no API key" in code.error
    assert len(code.calls) == 1  # type: ignore[attr-defined]


def test_the_prompt_tells_the_coder_what_it_owns_and_what_it_must_not_write():
    mine = _ticket("t1", ["src/auth/session.py"], assigned_agent="a1")
    theirs = _ticket("t2", ["src/auth/middleware.py"], assigned_agent="a2")
    room = _room({"src/auth/session.py": "stub\n", "src/auth/middleware.py": "stub\n"})
    captured: dict = {}

    def chat_fn(**kwargs) -> str:
        captured.update(kwargs)
        return "=== FILE: src/auth/session.py ===\nx = 1\n=== END FILE ===\n"

    implement_ticket_sync(
        ticket=mine,
        all_tickets=[mine, theirs],
        plan=room.plan,
        context=None,
        read_file=lambda p: read_file(room, p),
        paths=list(room.files),
        chat_fn=chat_fn,
    )

    # The system prompt has two ownership sections; each path belongs to
    # exactly one of them.
    mine_section, others_section = captured["system"].split("belong to other tickets")
    assert "src/auth/session.py" in mine_section
    assert "src/auth/middleware.py" not in mine_section
    assert "src/auth/middleware.py" in others_section
    assert "ticket t2, agent a2" in others_section
    # And it can read the file it does not own, in the user prompt.
    assert "--- src/auth/middleware.py ---" in captured["user"]


# --- enforcement of whatever the coder said ---


@pytest.mark.asyncio
async def test_execution_writes_agent_content_and_refuses_a_path_it_does_not_own():
    """The demo beat, as a property of the runtime: the coder asks for a file
    outside its ticket and is refused, while its own file lands for real."""
    room = _room({"src/a.py": "old\n", "src/b.py": "untouched\n"})
    room.tickets = [_ticket("t1", ["src/a.py"], assigned_agent="a1")]
    bus = EventBus()

    async def coder(_room: Room, ticket: Ticket) -> TicketCode:
        return TicketCode(
            files={
                "src/a.py": "def real_code():\n    return 1\n",
                "src/b.py": "# I do not own this\n",
            }
        )

    await execute_room(room, bus, implement=coder)

    assert room.files["src/a.py"].content == "def real_code():\n    return 1\n"
    assert room.files["src/a.py"].last_written_by == "a1"
    assert room.files["src/b.py"].content == "untouched\n", "a refused write must change nothing"

    writes = [e for e in bus.history("r1") if e["type"] == "file_written"]
    refused = [e for e in writes if not e["accepted"]]
    assert [e["path"] for e in refused] == ["src/b.py"]
    assert "path_not_owned" in refused[0]["reason"]
    # Being refused is the runtime working, not the ticket failing.
    assert room.tickets[0].status == "done"


@pytest.mark.asyncio
async def test_execution_fills_in_any_owned_file_the_coder_skipped():
    room = _room({"src/a.py": "old\n", "src/c.py": "old\n"})
    room.tickets = [_ticket("t1", ["src/a.py", "src/c.py"])]
    bus = EventBus()

    async def lazy_coder(_room: Room, ticket: Ticket) -> TicketCode:
        return TicketCode(files={"src/a.py": "only this one\n"})

    await execute_room(room, bus, implement=lazy_coder)

    assert room.files["src/a.py"].content == "only this one\n"
    assert "TODO: implement" in room.files["src/c.py"].content
    assert room.tickets[0].status == "done"


@pytest.mark.asyncio
async def test_execution_reports_a_coder_failure_as_an_error_event():
    room = _room({"src/a.py": "old\n"})
    room.tickets = [_ticket("t1", ["src/a.py"])]
    bus = EventBus()

    async def broken_coder(_room: Room, ticket: Ticket) -> TicketCode:
        return placeholder_code(ticket, error="no API key for openai")

    await execute_room(room, bus, implement=broken_coder)

    errors = [e for e in bus.history("r1") if e["type"] == "error"]
    assert errors and errors[0]["where"] == "execute.t1"
    assert "no API key" in errors[0]["detail"]
    # The room still finishes rather than hanging or half-writing.
    assert room.tickets[0].status == "done"
    assert "TODO: implement" in room.files["src/a.py"].content


@pytest.mark.asyncio
async def test_a_coder_that_raises_does_not_take_down_the_room():
    room = _room({"src/a.py": "old\n"})
    room.tickets = [_ticket("t1", ["src/a.py"])]
    bus = EventBus()

    async def exploding_coder(_room: Room, ticket: Ticket) -> TicketCode:
        raise RuntimeError("boom")

    await execute_room(room, bus, implement=exploding_coder)

    assert room.tickets[0].status == "done"
    assert "TODO: implement" in room.files["src/a.py"].content
    assert any(e["type"] == "error" for e in bus.history("r1"))


@pytest.mark.asyncio
async def test_a_later_ticket_reads_the_code_an_earlier_one_wrote():
    """Why sequential lanes are still honoured now that writes are real:
    t2 depends on t1 and must see t1's actual output through read_file."""
    room = _room({"src/a.py": "old\n", "src/b.py": "old\n"})
    room.tickets = [
        _ticket("t1", ["src/a.py"], assigned_agent="a1", lane="parallel"),
        _ticket("t2", ["src/b.py"], assigned_agent="a2", lane="sequential", depends_on=["t1"]),
    ]
    bus = EventBus()
    seen: dict[str, str | None] = {}

    async def coder(room_: Room, ticket: Ticket) -> TicketCode:
        seen[ticket.ticket_id] = read_file(room_, "src/a.py")
        return TicketCode(files={ticket.files_owned[0]: f"written by {ticket.ticket_id}\n"})

    await execute_room(room, bus, implement=coder)

    assert seen["t1"] == "old\n"
    assert seen["t2"] == "written by t1\n", "t2 should read what t1 actually wrote"
