"""The event bus: fan-out, replay on connect, and SSE framing.

Tested directly rather than over HTTP because the stream is an endless generator — the
transport is thin, the semantics are what can break.
"""

from __future__ import annotations

import asyncio
import json

from server.events import EventBus, sse_frame


def parse_frame(frame: str) -> tuple[str, dict]:
    lines = frame.strip().splitlines()
    event_type = next(line[len("event: ") :] for line in lines if line.startswith("event: "))
    data = next(line[len("data: ") :] for line in lines if line.startswith("data: "))
    return event_type, json.loads(data)


# --- framing -----------------------------------------------------------------


def test_frame_names_the_event_type_so_the_client_can_subscribe():
    frame = sse_frame({"type": "agent_message", "content": "hi"}, 7)
    assert "id: 7" in frame
    assert "event: agent_message" in frame
    assert frame.endswith("\n\n"), "a frame that is not blank-line terminated never fires"


def test_frame_data_is_one_line():
    frame = sse_frame({"type": "x", "content": "line one\nline two"})
    data_lines = [line for line in frame.splitlines() if line.startswith("data: ")]
    assert len(data_lines) == 1, "a newline in the payload must not split the frame"


# --- history and replay ------------------------------------------------------


async def test_history_accumulates_per_room():
    bus = EventBus()
    await bus.publish("a", {"type": "commit", "sha": "1"})
    await bus.publish("b", {"type": "commit", "sha": "2"})
    await bus.publish("a", {"type": "commit", "sha": "3"})

    assert [e["sha"] for e in bus.history("a")] == ["1", "3"]
    assert [e["sha"] for e in bus.history("b")] == ["2"]
    assert bus.history("nonexistent") == []


async def test_a_late_subscriber_replays_everything_it_missed():
    """A browser refresh mid-demo must not wipe the transcript."""
    bus = EventBus()
    await bus.publish("r", {"type": "agent_message", "round": 0, "content": "first"})
    await bus.publish("r", {"type": "round_complete", "round": 0})

    stream = bus.stream("r")
    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    assert parse_frame(first) == ("agent_message", {"type": "agent_message", "round": 0, "content": "first"})
    assert parse_frame(second)[0] == "round_complete"


async def test_replay_can_be_turned_off():
    bus = EventBus()
    await bus.publish("r", {"type": "commit", "sha": "old"})

    stream = bus.stream("r", replay=False)
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.05)
    await bus.publish("r", {"type": "commit", "sha": "new"})

    frame = await asyncio.wait_for(task, timeout=2)
    assert parse_frame(frame)[1]["sha"] == "new"
    await stream.aclose()


async def test_live_events_reach_every_subscriber():
    bus = EventBus()
    a, b = bus.stream("r"), bus.stream("r")
    # Start both generators so they are subscribed before anything is published.
    task_a = asyncio.create_task(anext(a))
    task_b = asyncio.create_task(anext(b))
    await asyncio.sleep(0.05)

    await bus.publish("r", {"type": "commit", "sha": "abc"})

    for task in (task_a, task_b):
        assert parse_frame(await asyncio.wait_for(task, timeout=2))[1]["sha"] == "abc"

    await a.aclose()
    await b.aclose()


async def test_events_do_not_leak_between_rooms():
    bus = EventBus()
    stream = bus.stream("room_a")
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.05)

    await bus.publish("room_b", {"type": "commit", "sha": "other-room"})
    await asyncio.sleep(0.05)
    assert not task.done()

    await bus.publish("room_a", {"type": "commit", "sha": "mine"})
    assert parse_frame(await asyncio.wait_for(task, timeout=2))[1]["sha"] == "mine"

    await stream.aclose()


async def test_closing_a_stream_unsubscribes_it():
    """A closed browser tab must not leave a queue behind that fills up forever."""
    bus = EventBus()
    stream = bus.stream("r")
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.05)
    assert len(bus._subscribers["r"]) == 1

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await stream.aclose()
    assert "r" not in bus._subscribers


async def test_frame_ids_increment_per_client():
    bus = EventBus()
    for i in range(3):
        await bus.publish("r", {"type": "commit", "sha": str(i)})

    stream = bus.stream("r")
    ids = [(await anext(stream)).splitlines()[0] for _ in range(3)]
    await stream.aclose()
    assert ids == ["id: 1", "id: 2", "id: 3"]
