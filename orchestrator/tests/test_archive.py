"""Replay rooms (archive.py): CLAUDE.md's "keep one pre-warmed completed
room as a fallback". A finished room is written to ROOM_ARCHIVE_DIR;
PREWARM_ROOMS loads archives (or a .jsonl event log like the fixtures) back
at startup as read-only rooms the live UI can open with ?room=<id>.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import orchestrator.main as main_module
from orchestrator import archive
from orchestrator.rooms import FileState, Room
from orchestrator.tests.scripted_room import (  # noqa: F401 — fixtures
    ScriptedRoom,
    client,
    events,
    negotiate,
    script,
    setup_room,
)
from orchestrator.tests.test_lifecycle import _approve_both, _execute, real_ticketing  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
EVENTS = [
    {"type": "participant_joined", "room_id": "room_saved1", "user_id": "u1", "ts": "t0"},
    {"type": "file_written", "room_id": "room_saved1", "path": "src/auth.py", "accepted": True, "ts": "t1"},
]


def write_archive(tmp_path: Path, room_id: str = "room_saved1") -> Path:
    room = Room(room_id=room_id)
    room.files = {"src/auth.py": FileState(content="def login(): ...\n", last_written_by="a1")}
    return archive.save(room, EVENTS)


@pytest.fixture
def archive_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("ROOM_ARCHIVE_DIR", str(tmp_path))
    return tmp_path


def test_save_does_nothing_without_an_archive_dir(monkeypatch):
    monkeypatch.delenv("ROOM_ARCHIVE_DIR", raising=False)

    assert archive.save(Room(room_id="room_x"), EVENTS) is None


def test_an_archive_round_trips(archive_dir):
    path = write_archive(archive_dir)

    room_id, loaded, files = archive.load(path)

    assert path == archive_dir / "room_saved1.json"
    assert room_id == "room_saved1" and loaded == EVENTS
    assert files["src/auth.py"].content == "def login(): ...\n"
    assert files["src/auth.py"].last_written_by == "a1"
    assert not list(archive_dir.glob("*.tmp")), "written via a temp file, then renamed"


def test_the_fixture_stream_loads_as_a_replay():
    room, loaded = archive.replay_room(REPO / "fixtures" / "stream.jsonl")

    assert room.room_id == "room_demo01" and room.replay
    assert loaded[0]["type"] == "participant_joined" and len(loaded) > 10


@pytest.mark.parametrize(
    "content, problem",
    [
        ("[1, 2]", "not a room archive"),
        ('{"room_id": "r1", "events": [{"no": "type"}]}', "typed event"),
        ('{"room_id": "../../etc", "events": []}', "room_id"),
        ('{"room_id": "r1", "events": [], "files": []}', "files"),
    ],
)
def test_a_bad_archive_is_refused(tmp_path, content, problem):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=problem):
        archive.load(path)


def test_relative_prewarm_paths_resolve_against_the_repo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREWARM_ROOMS", "fixtures/stream.jsonl, ")

    assert archive.prewarm_paths() == [REPO / "fixtures" / "stream.jsonl"]


# --- served by the app ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_prewarmed_room_is_served_read_only(client, archive_dir):
    path = write_archive(archive_dir, room_id="room_saved2")

    loaded = main_module.prewarm([path, archive_dir / "missing.json"])

    assert loaded == ["room_saved2"], "a bad path is skipped, not fatal"
    r = await client.get("/rooms/room_saved2/files/src/auth.py")
    assert r.json() == {"path": "src/auth.py", "content": "def login(): ...\n", "last_written_by": "a1"}
    assert main_module.bus.history("room_saved2") == EVENTS
    for path, body in [
        ("execute", {}),
        ("negotiate", {}),
        ("participants", {"user_id": "u3", "display_name": "C", "provider": "claude", "model": "m"}),
        ("messages", {"user_id": "u1", "content": "hi"}),
        ("plan/approve", {"user_id": "u1", "approved": True}),
    ]:
        r = await client.post(f"/rooms/room_saved2/{path}", json=body)
        assert r.status_code == 409 and "read-only replay" in r.json()["detail"], path


@pytest.mark.asyncio
async def test_a_finished_execution_is_archived(client, script, real_ticketing, archive_dir):
    room_id, _, tokens = await setup_room(client)
    room = await negotiate(client, room_id)
    await _approve_both(client, room_id, tokens)

    await _execute(client, room_id)

    saved = json.loads((archive_dir / f"{room_id}.json").read_text(encoding="utf-8"))
    assert [e["type"] for e in saved["events"]] == [e["type"] for e in main_module.bus.history(room_id)]
    written = [t.files_owned[0] for t in room.tickets]
    assert all(saved["files"][p]["last_written_by"] in {"a1", "a2"} for p in written)
    assert "participant_token" not in json.dumps(saved) and "api_key" not in json.dumps(saved)
