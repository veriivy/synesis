"""A finished room, saved to disk and served back read-only — CLAUDE.md's
latency section: "keep one pre-warmed completed room as a fallback."

ROOM_ARCHIVE_DIR set: every room whose execution finishes is written to
<dir>/<room_id>.json — its whole event log plus the files as written.

PREWARM_ROOMS (comma-separated paths): loaded at startup as read-only
replay rooms. A path is one of those archives, or a .jsonl event log such
as fixtures/stream.jsonl. Open one in the live UI at /live?room=<room_id>:
the SSE stream replays it like any room, GET /files serves the files as
they were written, and every endpoint that would change it answers 409 —
in particular POST /execute, which the UI fires on seeing tickets_created.

An archive holds nothing secret: events never carry an api_key or a
token, and files are workspace content.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .rooms import FileState, Phase, Room

_ROOM_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ROOT = Path(__file__).resolve().parents[1]


def archive_dir() -> Path | None:
    raw = os.getenv("ROOM_ARCHIVE_DIR", "").strip()
    return Path(raw) if raw else None


def save(room: Room, events: list[dict]) -> Path | None:
    """Write the room's archive, or do nothing when ROOM_ARCHIVE_DIR is
    unset. Blocking — call through asyncio.to_thread."""
    directory = archive_dir()
    if directory is None or not _ROOM_ID.match(room.room_id):
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{room.room_id}.json"
    payload = {
        "room_id": room.room_id,
        "events": events,
        "files": {
            p: {"content": f.content, "last_written_by": f.last_written_by} for p, f in room.files.items()
        },
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)  # never leave a half-written archive behind
    return path


def load(path: str | Path) -> tuple[str, list[dict], dict[str, FileState] | None]:
    """(room_id, events, files) from an archive, or from a .jsonl event log
    (files None: the replay serves the seeded workspace). Raises ValueError
    on anything that isn't one."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    files: dict[str, FileState] | None = None
    if path.suffix == ".jsonl":
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
        room_id = str(events[0].get("room_id") or "") if events else ""
    else:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: not a room archive")
        events = data.get("events")
        room_id = str(data.get("room_id") or "")
        raw_files = data.get("files")
        if raw_files is None:
            raw_files = {}
        if not isinstance(raw_files, dict):
            raise ValueError(f"{path}: files is not an object")
        files = {
            str(p): FileState(content=str(f.get("content") or ""), last_written_by=f.get("last_written_by"))
            for p, f in raw_files.items()
            if isinstance(f, dict)
        }
    if not isinstance(events, list) or not all(isinstance(e, dict) and e.get("type") for e in events):
        raise ValueError(f"{path}: events must be a list of typed event objects")
    if not _ROOM_ID.match(room_id):
        raise ValueError(f"{path}: missing or unusable room_id {room_id!r}")
    return room_id, events, files


def replay_room(path: str | Path) -> tuple[Room, list[dict]]:
    """A read-only Room for an archive, plus the events to seed its stream."""
    room_id, events, files = load(path)
    room = Room(room_id=room_id, phase=Phase.DONE, replay=True)
    if files is not None:
        room.files = files
    return room, events


def prewarm_paths() -> list[Path]:
    """PREWARM_ROOMS, each relative path resolved against the repo root
    (where fixtures/ lives) unless it exists relative to the cwd."""
    paths = []
    for raw in os.getenv("PREWARM_ROOMS", "").split(","):
        if raw.strip():
            path = Path(raw.strip())
            paths.append(path if path.is_absolute() or path.exists() else ROOT / path)
    return paths
