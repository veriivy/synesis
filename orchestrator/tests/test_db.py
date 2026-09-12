"""db.py's no-op behavior when MONGODB_URI isn't set — the default in
this test environment (no .env, no real Atlas cluster available here).
Everything in db.py is designed to never raise regardless of whether Mongo
is configured; that's the property these tests check. Behavior against a
*real* cluster isn't covered here — there isn't one available in this
environment — see orchestrator/README.md for what was and wasn't verified.
"""

from __future__ import annotations

import pytest

from orchestrator import db


@pytest.fixture(autouse=True)
def no_mongodb_uri(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    db._client.cache_clear()
    yield
    db._client.cache_clear()


def test_get_db_returns_none_without_a_uri():
    assert db.get_db() is None


@pytest.mark.asyncio
async def test_record_event_is_a_safe_no_op_without_a_uri():
    # Must not raise, must not require a running Mongo.
    await db.record_event({"type": "poa_generated", "room_id": "r1"})


@pytest.mark.asyncio
async def test_save_room_is_a_safe_no_op_without_a_uri():
    await db.save_room({"room_id": "r1", "phase": "waiting"})


@pytest.mark.asyncio
async def test_save_room_is_a_safe_no_op_without_a_room_id():
    # A malformed snapshot shouldn't raise either, URI or not.
    await db.save_room({"phase": "waiting"})
