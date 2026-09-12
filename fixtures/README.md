# /fixtures — the frozen contract, as data

These files are the executable form of THE FROZEN CONTRACT in `CLAUDE.md`. They exist
so `/web` can be built and demoed **before `/server` returns anything real**.

**Changing anything here requires all three of us to agree, out loud.** If a fixture and
the backend disagree, the fixture is right and the backend is wrong until we re-freeze.

| File | What it is |
|---|---|
| `intents.json` | Two deliberately incompatible requirement sets. The demo input. |
| `workplan.json` | One complete workplan: tasks, interface contracts, concessions. |
| `events/*.json` | One realistic sample of every SSE event type. |
| `stream.jsonl` | The happy-path demo, one event per line, in order. Replayable. |

## Replaying the stream

The server can serve `stream.jsonl` instead of live agents — no API keys, no latency,
no chance of a model saying something embarrassing on stage:

```bash
# server/
REPLAY_FIXTURE=../fixtures/stream.jsonl uvicorn server.main:app --reload
```

This is the backup demo. Record the video against it at 1pm.

## Contract rules

- Every event has `type`, `room_id`, `ts`. `ts` is ISO-8601 UTC with a `Z`.
- `round` is 0-indexed. Round 0 is opening positions.
- Agent ids are the provider keys: `claude`, `gpt`, `gemini`, `k2`, `grok`.
- `file_written` is emitted for **rejected** writes too, with `accepted: false` and a
  non-null `reason`. The frontend must render rejections differently — they are the
  demo's money shot.
