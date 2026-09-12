"""Endpoint shapes. These assert THE FROZEN CONTRACT, not implementation detail."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.main import app, bus, rooms


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Keep clones out of the real workspaces/ directory during tests.
    rooms.workspace_root = tmp_path / "workspaces"
    rooms.workspace_root.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as c:
        yield c


def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert "git" in body and "mongo" in body


def test_create_room_returns_a_room_id(client):
    body = client.post("/rooms", json={"feature": "Add auth"}).json()
    assert set(body) == {"room_id"}
    assert body["room_id"].startswith("room_")


def test_intents_returns_ok_and_registers_the_participant(client):
    room_id = client.post("/rooms", json={"feature": "Add auth"}).json()["room_id"]
    response = client.post(
        f"/rooms/{room_id}/intents",
        json={
            "user_id": "u1",
            "agent_id": "claude",
            "requirements": [
                {"req_id": "r1", "text": "No external services.", "priority": "must-have"}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    room = client.get(f"/rooms/{room_id}").json()
    assert room["participants"][0]["user_id"] == "u1"
    assert room["participants"][0]["provider"] == "anthropic"


def test_resubmitting_a_users_intent_replaces_it(client):
    room_id = client.post("/rooms", json={}).json()["room_id"]
    for text in ("first", "second"):
        client.post(
            f"/rooms/{room_id}/intents",
            json={
                "user_id": "u1",
                "agent_id": "claude",
                "requirements": [{"req_id": "r1", "text": text, "priority": "must-have"}],
            },
        )
    room = client.get(f"/rooms/{room_id}").json()
    assert len(room["participants"]) == 1
    assert room["participants"][0]["requirements"][0]["text"] == "second"


def test_unknown_agent_id_is_a_400(client):
    room_id = client.post("/rooms", json={}).json()["room_id"]
    response = client.post(
        f"/rooms/{room_id}/intents",
        json={"user_id": "u1", "agent_id": "not-a-real-agent", "requirements": []},
    )
    assert response.status_code == 400


def test_missing_room_is_a_404(client):
    assert client.get("/rooms/room_nope").status_code == 404
    assert client.get("/rooms/room_nope/stream").status_code == 404
    assert (
        client.post("/rooms/room_nope/plan/approve", json={"approved": True}).status_code
        == 404
    )


def test_approving_before_a_plan_exists_is_a_409(client):
    room_id = client.post("/rooms", json={}).json()["room_id"]
    response = client.post(f"/rooms/{room_id}/plan/approve", json={"approved": True})
    assert response.status_code == 409


def test_files_is_empty_before_a_plan(client):
    room_id = client.post("/rooms", json={}).json()["room_id"]
    assert client.get(f"/rooms/{room_id}/files").json() == {"files": []}


def test_posting_an_intent_puts_an_event_on_the_bus(client):
    """A browser that connects mid-demo must see what it missed, not a blank screen.

    The stream endpoint is an endless generator, which TestClient cannot close cleanly
    — the replay, dedupe and framing behaviour is covered directly against the bus in
    test_events.py. Here we assert only this endpoint's own contribution: the event
    reaches the bus, so any subscriber will replay it.
    """
    room_id = client.post("/rooms", json={}).json()["room_id"]
    client.post(
        f"/rooms/{room_id}/intents",
        json={
            "user_id": "u1",
            "agent_id": "claude",
            "requirements": [{"req_id": "r1", "text": "x", "priority": "must-have"}],
        },
    )

    history = bus.history(room_id)
    assert [event["type"] for event in history] == ["intent_registered"]
    assert history[0]["room_id"] == room_id
    assert history[0]["requirement_count"] == 1
