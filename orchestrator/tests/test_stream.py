"""SSE transport (events.py): resume after a dropped connection, heartbeats
while a model call keeps the room quiet, and no duplicate frames.

Before: every reconnect replayed the room from the top into a client that
still held its state, so each event reached the reducer twice; an event
published mid-replay was sent twice even on a first connect; and a quiet
stream sent nothing at all for as long as a K2 call took.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import orchestrator.events as events_module
import orchestrator.main as main_module
from orchestrator.events import HEARTBEAT_FRAME, EventBus, parse_last_event_id


async def take(frames, n: int, timeout: float = 1.0) -> list[str]:
    out: list[str] = []

    async def run() -> None:
        async for frame in frames:
            out.append(frame)
            if len(out) == n:
                return

    await asyncio.wait_for(run(), timeout)
    return out


def ids(frames: list[str]) -> list[int | None]:
    def one(frame: str) -> int | None:
        first = frame.split("\n", 1)[0]
        return int(first[4:]) if first.startswith("id: ") else None

    return [one(f) for f in frames]


async def bus_with(n: int, room_id: str = "r1") -> EventBus:
    bus = EventBus()
    for i in range(n):
        await bus.publish(room_id, {"type": "user_message", "content": f"m{i}"})
    return bus


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setattr(events_module, "HEARTBEAT_SECONDS", 0.05)


@pytest.mark.asyncio
async def test_a_fresh_connection_gets_the_whole_history_with_ids():
    bus = await bus_with(3)
    stream = bus.stream("r1")

    frames = await take(stream, 3)
    await stream.aclose()

    assert ids(frames) == [0, 1, 2]
    assert json.loads(frames[2].split("data: ", 1)[1])["content"] == "m2"


@pytest.mark.asyncio
async def test_a_reconnect_resumes_after_the_last_event_it_saw(fast_heartbeat):
    bus = await bus_with(4)
    stream = bus.stream("r1", last_event_id=1)

    frames = await take(stream, 3)
    await stream.aclose()

    assert ids(frames) == [2, 3, None], "m2, m3, then only a heartbeat — never m0/m1 again"
    assert frames[2] == HEARTBEAT_FRAME


@pytest.mark.asyncio
async def test_an_event_published_mid_replay_is_sent_once(fast_heartbeat):
    bus = await bus_with(2)
    stream = bus.stream("r1")

    first = await take(stream, 1)
    await bus.publish("r1", {"type": "user_message", "content": "late"})
    rest = await take(stream, 3)
    await stream.aclose()

    assert ids(first + rest) == [0, 1, 2, None]


@pytest.mark.asyncio
async def test_live_events_follow_the_replay():
    bus = await bus_with(1)
    stream = bus.stream("r1")
    await take(stream, 1)

    async def publish_soon() -> None:
        await asyncio.sleep(0.01)
        await bus.publish("r1", {"type": "round_complete", "round": 1})

    asyncio.get_running_loop().create_task(publish_soon())
    (frame,) = await take(stream, 1)
    await stream.aclose()

    assert frame.startswith("id: 1\nevent: round_complete\n")


@pytest.mark.asyncio
async def test_a_quiet_stream_sends_heartbeats(fast_heartbeat):
    bus = EventBus()
    stream = bus.stream("r1")

    frames = await take(stream, 2)
    await stream.aclose()

    assert frames == [HEARTBEAT_FRAME, HEARTBEAT_FRAME]


@pytest.mark.asyncio
async def test_closing_a_stream_unsubscribes_it():
    bus = await bus_with(1)
    stream = bus.stream("r1")
    await take(stream, 1)
    assert len(bus._subs["r1"]) == 1

    await stream.aclose()

    assert bus._subs["r1"] == []


@pytest.mark.asyncio
async def test_an_id_this_room_never_issued_replays_everything():
    bus = await bus_with(2)
    stream = bus.stream("r1", last_event_id=57)

    frames = await take(stream, 2)
    await stream.aclose()

    assert ids(frames) == [0, 1]


@pytest.mark.parametrize(
    "raw, expected",
    [(None, None), ("", None), ("abc", None), ("-1", None), ("0", 0), (" 12 ", 12)],
)
def test_last_event_id_is_parsed_as_untrusted_input(raw, expected):
    assert parse_last_event_id(raw) == expected


@pytest.mark.asyncio
async def test_the_endpoint_reads_last_event_id(fast_heartbeat):
    room_id = main_module.rooms.create().room_id
    for i in range(3):
        await main_module.bus.publish(room_id, {"type": "user_message", "content": f"m{i}"})

    response = await main_module.stream(room_id, last_event_id="1")
    frames = await take(response.body_iterator, 2)
    await response.body_iterator.aclose()

    assert ids(frames) == [2, None]
