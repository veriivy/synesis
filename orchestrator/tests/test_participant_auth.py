"""The participant_token fix for the identity-spoofing / BYO-key-hijack
finding: POST /participants issues a token on first join; re-registering
an already-claimed user_id, or acting as one via /tasks, /messages, or
/plan/approve, now requires it. Each test here is a direct regression
test for the exploit steps from the security review.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from orchestrator.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _join(client: AsyncClient, room_id: str, user_id: str, **extra) -> dict:
    body = {"user_id": user_id, "display_name": user_id, "provider": "claude", "model": "m", **extra}
    r = await client.post(f"/rooms/{room_id}/participants", json=body)
    return r


@pytest.mark.asyncio
async def test_first_join_issues_a_token(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]

    r = await _join(client, room_id, "u1")

    assert r.status_code == 201
    token = r.json()["participant_token"]
    assert isinstance(token, str) and len(token) > 20


@pytest.mark.asyncio
async def test_the_exploit_is_fixed_attacker_cannot_hijack_an_existing_participants_key(
    client: AsyncClient,
):
    """This is the literal exploit from the security review: attacker
    knows room_id, re-POSTs /participants for the victim's user_id with
    their own api_key, with no token — must now be rejected."""
    room_id = (await client.post("/rooms")).json()["room_id"]
    victim_join = await _join(client, room_id, "u1", api_key="sk-victim-real-key")
    assert victim_join.status_code == 201

    attacker_attempt = await _join(client, room_id, "u1", api_key="sk-attacker-key")

    assert attacker_attempt.status_code == 403
    # And the victim's key must still be the one on record.
    from orchestrator.main import rooms

    room = rooms.get(room_id)
    assert room.participants["u1"].api_key == "sk-victim-real-key"


@pytest.mark.asyncio
async def test_rejoining_with_the_correct_token_is_allowed(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]
    first = await _join(client, room_id, "u1", api_key="sk-original")
    token = first.json()["participant_token"]

    r = await _join(client, room_id, "u1", api_key="sk-updated", participant_token=token)

    assert r.status_code == 201
    assert r.json()["participant_token"] == token  # same token handed back, not rotated

    from orchestrator.main import rooms

    assert rooms.get(room_id).participants["u1"].api_key == "sk-updated"


@pytest.mark.asyncio
async def test_tasks_requires_the_owning_participants_token(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]
    await _join(client, room_id, "u1")

    spoofed = await client.post(
        f"/rooms/{room_id}/tasks",
        json={"user_id": "u1", "tasks": [{"text": "attacker-injected requirement", "priority": "must"}]},
    )

    assert spoofed.status_code == 403


@pytest.mark.asyncio
async def test_messages_requires_the_owning_participants_token(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]
    await _join(client, room_id, "u1")

    spoofed = await client.post(
        f"/rooms/{room_id}/messages",
        json={"user_id": "u1", "content": "fabricated message attributed to u1"},
    )

    assert spoofed.status_code == 403


@pytest.mark.asyncio
async def test_approve_requires_the_owning_participants_token(client: AsyncClient):
    room_id = (await client.post("/rooms")).json()["room_id"]
    await _join(client, room_id, "u1")
    await _join(client, room_id, "u2")

    from orchestrator.main import rooms
    from orchestrator.schemas import FinalPlan

    room = rooms.get(room_id)
    room.plan = FinalPlan(
        plan_id="p1", rounds_used=1, summary="s", steps=[], approvals={"u1": False, "u2": False}
    )

    spoofed = await client.post(
        f"/rooms/{room_id}/plan/approve", json={"user_id": "u1", "approved": True}
    )

    assert spoofed.status_code == 403
    assert room.plan.approvals["u1"] is False, "the spoofed approval must not have been recorded"


@pytest.mark.asyncio
async def test_unclaimed_user_id_still_works_token_free_fixture_demo_path(client: AsyncClient):
    """No /participants call at all for "u1" — the fixture-fallback demo
    path never registers real participants. Nothing to protect yet, so
    this must keep working exactly as before the fix."""
    room_id = (await client.post("/rooms")).json()["room_id"]

    r = await client.post(
        f"/rooms/{room_id}/tasks",
        json={"user_id": "u1", "tasks": [{"text": "x", "priority": "want"}]},
    )

    assert r.status_code == 200
