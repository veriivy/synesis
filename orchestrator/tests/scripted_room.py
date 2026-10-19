"""A scripted negotiation for tests that need to drive the real app's loop:
fakes for draft_poa / analyze / revise_poa that record every call's kwargs,
deadlock on one blocking issue, and expose hooks to act mid-round. Not a
test module itself (no test_ prefix) — imported by test_interjections.py
and test_lifecycle.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient

import orchestrator.main as main_module
from orchestrator.rooms import Room


def poa(agent_id: str, user_id: str, files: list[str]) -> dict:
    return {
        "poa_id": f"poa_{agent_id}",
        "agent_id": agent_id,
        "user_id": user_id,
        "summary": f"{agent_id}'s plan",
        "steps": [
            {
                "step_id": f"s{i}",
                "title": f"{agent_id} writes {path}",
                "description": path,
                "files_touched": [path],
                "rationale": "must-have",
            }
            for i, path in enumerate(files, start=1)
        ],
        "assumptions": [],
    }


class ScriptedRoom:
    def __init__(self) -> None:
        self.drafts: list[dict] = []
        self.analyses: list[dict] = []
        self.revisions: list[dict] = []
        self.files = {"a1": ["src/auth.py"], "a2": ["src/auth.py"]}
        self.diff = {
            "issue_id": "d1",
            "topic": "auth mechanism",
            "positions": {"a1": "session cookies", "a2": "bearer JWT"},
            "severity": "blocking",
            "files": ["src/auth.py"],
        }
        #: Called (in the worker thread) at the start of each fake call.
        self.on_draft: Callable[[str], None] | None = None
        self.on_analyze: Callable[[int], None] | None = None
        self.on_revise: Callable[[str, int], None] | None = None

    def draft_poa(self, **kw) -> dict:
        if self.on_draft:
            self.on_draft(kw["agent_id"])
        self.drafts.append(kw)
        return poa(kw["agent_id"], kw["user_id"], self.files[kw["agent_id"]])

    def analyze(self, **kw) -> dict:
        if self.on_analyze:
            self.on_analyze(kw["round_index"])
        self.analyses.append(kw)
        return {
            "round": kw["round_index"],
            "similarities": [],
            "differences": [dict(self.diff)],
            "converged": False,
        }

    def revise_poa(self, **kw) -> dict:
        agent_id = kw["poa"]["agent_id"]
        if self.on_revise:
            self.on_revise(agent_id, kw["round_index"])
        self.revisions.append(kw)
        return {
            "agent_id": agent_id,
            "user_id": kw["poa"]["user_id"],
            "content": "d1: HOLD",
            "addresses_issues": ["d1"],
            "poa": kw["poa"],
        }

    def revisions_for(self, agent_id: str) -> list[dict]:
        return [r for r in self.revisions if r["poa"]["agent_id"] == agent_id]


@pytest.fixture
def script(monkeypatch) -> ScriptedRoom:
    s = ScriptedRoom()
    monkeypatch.setattr(main_module, "draft_poa", s.draft_poa)
    monkeypatch.setattr(main_module, "analyze", s.analyze)
    monkeypatch.setattr(main_module, "revise_poa", s.revise_poa)
    monkeypatch.setattr(main_module, "MAX_ROUNDS", 3)
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", False)
    return s


@pytest.fixture
async def client():
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def setup_room(
    client: AsyncClient, *, weights: dict | None = None, context: str | None = None
) -> tuple[str, str, dict[str, str]]:
    """Room with Avery (u1 -> a1) and Blair (u2 -> a2), one must each.
    Returns (room_id, admin_token, {user_id: participant_token})."""
    created = (await client.post("/rooms")).json()
    room_id, admin = created["room_id"], created["admin_token"]
    if weights is not None:
        r = await client.post(f"/rooms/{room_id}/weights", json={"weights": weights, "admin_token": admin})
        assert r.status_code == 200, r.text
    if context is not None:
        r = await client.post(
            f"/rooms/{room_id}/context", json={"project_context": context, "admin_token": admin}
        )
        assert r.status_code == 200, r.text
    tokens: dict[str, str] = {}
    for user_id, name in (("u1", "Avery"), ("u2", "Blair")):
        r = await client.post(
            f"/rooms/{room_id}/participants",
            json={"user_id": user_id, "display_name": name, "provider": "claude", "model": "m"},
        )
        tokens[user_id] = r.json()["participant_token"]
        r = await client.post(
            f"/rooms/{room_id}/tasks",
            json={
                "user_id": user_id,
                "tasks": [{"text": f"{name}'s auth", "priority": "must"}],
                "participant_token": tokens[user_id],
            },
        )
        assert r.status_code == 200, r.text
    return room_id, admin, tokens


async def negotiate(client: AsyncClient, room_id: str) -> Room:
    r = await client.post(f"/rooms/{room_id}/negotiate")
    assert r.status_code == 202, r.text
    room = main_module.rooms.get(room_id)
    await asyncio.wait_for(room.task, timeout=5)
    return room


async def say(client: AsyncClient, room_id: str, user_id: str, token: str | None, content: str):
    return await client.post(
        f"/rooms/{room_id}/messages",
        json={"user_id": user_id, "content": content, "participant_token": token},
    )


def events(room_id: str, kind: str) -> list[dict]:
    return [e for e in main_module.bus.history(room_id) if e["type"] == kind]
