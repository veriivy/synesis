"""The sandbox is the technical claim in the pitch. It gets the most tests.

Every one of these is an attack the demo asserts we survive. If one of them regresses,
the "capability-based sandboxing" line in the 2:35 slot becomes false.
"""

from __future__ import annotations

import json

import pytest
from engine.schemas import Workplan

from server.workspace import MAX_WRITE_BYTES, Workspace

PLAN = {
    "plan_id": "p1",
    "tasks": [
        {
            "task_id": "t1",
            "title": "tokens",
            "owner_agent": "claude",
            "owner_user": "u1",
            "files_owned": ["src/auth/tokens.py", "tests/test_auth_tokens.py"],
            "depends_on": [],
        },
        {
            "task_id": "t2",
            "title": "middleware",
            "owner_agent": "gpt",
            "owner_user": "u2",
            "files_owned": ["src/auth/middleware.py"],
            "depends_on": ["t1"],
        },
    ],
    "interface_contracts": [],
    "concessions": [],
    "unresolved": [],
}


@pytest.fixture
def plan() -> Workplan:
    return Workplan.model_validate(json.loads(json.dumps(PLAN)))


@pytest.fixture
def ws(tmp_path, plan) -> Workspace:
    root = tmp_path / "room_x"
    root.mkdir()
    return Workspace(room_id="room_x", root=root, plan=plan, plan_approved=True)


# --- rule 1: no approved plan -----------------------------------------------


def test_no_plan_at_all_refuses_every_write(tmp_path):
    ws = Workspace(room_id="r", root=tmp_path)
    outcome = ws.write_file("claude", "src/auth/tokens.py", "x")
    assert not outcome.accepted
    assert outcome.reason.startswith("no_approved_plan")


def test_proposed_but_unapproved_plan_refuses_writes(tmp_path, plan):
    ws = Workspace(room_id="r", root=tmp_path, plan=plan, plan_approved=False)
    outcome = ws.write_file("claude", "src/auth/tokens.py", "x")
    assert not outcome.accepted
    assert outcome.reason.startswith("no_approved_plan")
    assert not (tmp_path / "src").exists(), "nothing may touch disk before approval"


def test_approval_is_what_unlocks_writing(tmp_path, plan):
    ws = Workspace(room_id="r", root=tmp_path, plan=plan, plan_approved=False)
    assert not ws.write_file("claude", "src/auth/tokens.py", "x").accepted

    ws.plan_approved = True
    assert ws.write_file("claude", "src/auth/tokens.py", "x").accepted


# --- rule 2: containment -----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "../../escape.py",
        "../../../../../../etc/passwd",
        "src/../../escape.py",
        "src/auth/../../../escape.py",
        "./../escape.py",
        "src/auth/../../..",
    ],
)
def test_traversal_is_refused(ws, path):
    outcome = ws.write_file("claude", path, "pwned")
    assert not outcome.accepted
    assert outcome.reason.startswith("path_escape") or outcome.reason.startswith(
        "path_not_owned"
    )


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "C:\\Windows\\System32\\drivers\\etc\\hosts", "\\\\server\\share\\x"],
)
def test_absolute_paths_are_refused(ws, path):
    outcome = ws.write_file("claude", path, "pwned")
    assert not outcome.accepted
    assert outcome.reason.startswith("path_escape")


def test_backslash_separators_are_normalised_not_trusted(ws):
    """A Windows-style spelling of an owned path is the same file, and is allowed."""
    assert ws.write_file("claude", "src\\auth\\tokens.py", "x").accepted
    assert (ws.root / "src" / "auth" / "tokens.py").is_file()


def test_backslash_traversal_is_still_refused(ws):
    outcome = ws.write_file("claude", "..\\..\\escape.py", "pwned")
    assert not outcome.accepted
    assert outcome.reason.startswith("path_escape")


def test_nul_byte_is_refused(ws):
    outcome = ws.write_file("claude", "src/auth/tokens.py\x00.txt", "x")
    assert not outcome.accepted
    assert "NUL" in outcome.reason


def test_empty_path_is_refused(ws):
    assert not ws.write_file("claude", "", "x").accepted
    assert not ws.write_file("claude", "   ", "x").accepted


def test_symlink_out_of_the_workspace_is_refused(ws, tmp_path):
    """Resolution happens before the ownership check, so a symlink cannot smuggle a
    write past it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = ws.root / "src"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")

    outcome = ws.write_file("claude", "src/auth/tokens.py", "pwned")
    assert not outcome.accepted
    assert outcome.reason.startswith("path_escape")


# --- rule 3: ownership -------------------------------------------------------


def test_agent_can_write_its_own_file(ws):
    outcome = ws.write_file("claude", "src/auth/tokens.py", "def f():\n    return 1\n")
    assert outcome.accepted
    assert outcome.reason is None
    assert outcome.bytes_written == 22
    assert (ws.root / "src/auth/tokens.py").read_text() == "def f():\n    return 1\n"


def test_agent_cannot_write_another_agents_file(ws):
    """The demo's money shot."""
    outcome = ws.write_file("gpt", "src/auth/tokens.py", "sneaky")
    assert not outcome.accepted
    assert outcome.reason.startswith("path_not_owned")
    assert "t1" in outcome.reason and "claude" in outcome.reason
    assert not (ws.root / "src/auth/tokens.py").exists(), "nothing reaches disk"


def test_rejection_reason_names_what_the_agent_does_own(ws):
    outcome = ws.write_file("gpt", "src/auth/tokens.py", "x")
    assert "src/auth/middleware.py" in outcome.reason


def test_file_owned_by_nobody_is_refused(ws):
    outcome = ws.write_file("claude", "src/routes/notes.py", "x")
    assert not outcome.accepted
    assert "not owned by any task" in outcome.reason


def test_unknown_agent_owns_nothing(ws):
    outcome = ws.write_file("gemini", "src/auth/tokens.py", "x")
    assert not outcome.accepted
    assert "(nothing)" in outcome.reason


def test_an_agent_with_two_tasks_can_write_both(tmp_path, plan):
    plan.tasks[1].owner_agent = "claude"
    ws = Workspace(room_id="r", root=tmp_path, plan=plan, plan_approved=True)
    assert ws.write_file("claude", "src/auth/tokens.py", "a").accepted
    assert ws.write_file("claude", "src/auth/middleware.py", "b").accepted


# --- rule 4: git metadata ----------------------------------------------------


@pytest.mark.parametrize("path", [".git/config", ".git/hooks/post-commit", ".git/HEAD"])
def test_git_metadata_is_never_writable(ws, path):
    outcome = ws.write_file("claude", path, "pwned")
    assert not outcome.accepted
    assert outcome.reason.startswith("protected_path")


# --- limits ------------------------------------------------------------------


def test_oversized_write_is_refused(ws):
    outcome = ws.write_file("claude", "src/auth/tokens.py", "x" * (MAX_WRITE_BYTES + 1))
    assert not outcome.accepted
    assert outcome.reason.startswith("size_limit")
    assert not (ws.root / "src/auth/tokens.py").exists()


def test_utf8_byte_count_not_character_count(ws):
    outcome = ws.write_file("claude", "src/auth/tokens.py", "héllo")
    assert outcome.accepted
    assert outcome.bytes_written == 6  # 5 characters, 6 bytes


# --- reads -------------------------------------------------------------------


def test_reads_are_open_across_the_repo(ws):
    ws.write_file("claude", "src/auth/tokens.py", "shared code")
    assert ws.read_file("gpt", "src/auth/tokens.py") == "shared code"


def test_reads_cannot_escape_the_workspace(ws):
    assert ws.read_file("claude", "../../../../etc/passwd").startswith("error: path_escape")


def test_reading_a_missing_file_is_an_error_string_not_an_exception(ws):
    assert "does not exist" in ws.read_file("claude", "src/nope.py")


# --- repo map ----------------------------------------------------------------


def test_repo_map_lists_files_and_skips_noise(ws):
    (ws.root / "src").mkdir(parents=True, exist_ok=True)
    (ws.root / "src" / "app.py").write_text("x = 1")
    (ws.root / ".git").mkdir(exist_ok=True)
    (ws.root / ".git" / "config").write_text("secret")
    (ws.root / "node_modules").mkdir(exist_ok=True)
    (ws.root / "node_modules" / "junk.js").write_text("junk")

    listing = ws.repo_map()
    assert "src/app.py" in listing
    assert ".git" not in listing
    assert "node_modules" not in listing
