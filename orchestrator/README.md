# orchestrator

A FastAPI implementation of CLAUDE.md's `### Orchestrator endpoints` and
`### The K2 orchestration loop`, combining two branches of work:

- **The real negotiation loop** (`main.py`, `agents.py`, `k2.py`,
  `providers.py`) — real Gemini (a1) / ChatGPT (a2) / IFM-K2 calls, ported
  from what landed on `main` first. This is the canonical base: it owns
  the process-wide `EventBus`/`RoomStore`, the round loop, and provider
  selection. Nothing here was rewritten; `agents.py` gained one new
  function (`draft_poa`, below) and `main.py` gained the endpoints and
  FinalPlan-building described next.
- **Tasking, ticket decomposition, and execution** (`schemas.py`,
  `validator.py`, `ticketing.py`, `execution.py`, `workspace.py`, the
  extra fields on `rooms.Room`) — the rest of CLAUDE.md's endpoint
  contract: task intake, the disjoint-file-ownership guarantee, and
  `write_file` enforcement.

## Run it

From the **repo root** (not from inside `orchestrator/` — it's imported as
a package, `orchestrator.main`, same as `k2mod.py` already does):

```
pip install -r requirements.txt
uvicorn orchestrator.main:app --reload
```

Copy `.env.example` to `.env` and fill in whichever provider keys you
have; missing/failed keys fall back to IFM (`providers.chat`'s `resolve`).
With **no** keys at all, task intake/context/participants/files all still
work, but `/negotiate` and `/plan/approve`'s ticket decomposition will
fail and report a clean `error` event over SSE rather than hang or crash
— verified manually against a running instance (`curl -N` on
`/rooms/{id}/stream`).

`MONGODB_URI` is also optional — unset, every room stays in-memory only,
same as before this was added. Set it (an Atlas `mongodb+srv://...`
connection string) to get durable persistence; see "What's real" below
for exactly what that does and doesn't cover.

## The full sequence

```
POST /rooms
POST /rooms/{id}/participants   x2       (first join = a1, second = a2;
                                          response carries a participant_token —
                                          required on every subsequent call for
                                          that user_id: /tasks, /messages, /approve)
POST /rooms/{id}/context                 (optional)
POST /rooms/{id}/tasks          x2       (optional — see below)
POST /rooms/{id}/negotiate      -> 202
  GET /rooms/{id}/stream: poa_generated x2, then per round: analysis,
  agent_message x2, round_complete — up to MAX_ROUNDS (.env, default 3) —
  ending in plan_proposed
POST /rooms/{id}/plan/approve   x2       (the 2nd approval decomposes
                                          tickets and emits tickets_created)
POST /rooms/{id}/execute        -> 202   (ticket_started/file_written/
                                          ticket_completed per ticket)
GET  /rooms/{id}/files, /rooms/{id}/files/{path}
```

**If tasks were submitted** (`POST /tasks` for both joined participants),
`negotiate` drafts both agents' OPENING PoAs live from that room's actual
tasks (`agents.draft_poa`, a new function alongside the existing
`revise_poa`) instead of the fixture PoAs. **If no tasks were submitted**
at all, it falls back to `fixtures/PoA1.json`/`PoA2.json` exactly as
`main.py` originally did — that demo path is unchanged and still the
default for a bare `POST /rooms` -> `POST /negotiate` with no setup.

Once the round loop ends (converged, or `MAX_ROUNDS` reached),
`_build_final_plan` merges whatever the two PoAs currently say into a
`FinalPlan` and publishes `plan_proposed`. There's no separate per-issue
concede/hold/compromise bookkeeping the way an earlier draft of this had —
it doesn't need any: if a real model's revised PoA drops a step, that step
is just gone from the merge. Any difference still marked `"blocking"` in
the *last* round's analysis gets an honest `"Unresolved after N round(s)"`
resolution rather than a fabricated one.

## What's real vs. a deliberate stand-in

**Real, with tests:**
- The negotiation loop's actual model calls (`providers.chat`, `k2.analyze`,
  `agents.draft_poa`/`revise_poa`) — these are what was already running
  before this merge; this README doesn't re-test them, `tests/` fakes them
  at the exact names `main.py` imports (`main.draft_poa`, `main.analyze`,
  `main.revise_poa`) so the suite never needs a real key or the network.
- The disjoint-file-ownership validator (`validator.py`): the actual
  code-level assertion CLAUDE.md asks for, not a prompt. Demotes a
  conflicting parallel ticket to `sequential` with a `depends_on` edge;
  raises rather than guessing at an empty `files_owned`.
- `write_file` enforcement (`execution.py`): rejects a path that escapes
  the workspace root, a path outside the calling ticket's `files_owned`,
  or any write before the plan is approved — proven directly in
  `tests/test_execution.py`, not staged as a demo moment.
- `ticketing.py`'s JSON-repair retry, matching the pattern `k2.analyze`
  already uses (one retry, then let it fail loudly).
- BYO keys (`db.py`'s sibling feature, `_agent_provider_and_key` in
  `main.py`): if the participant who owns an agent joined with their own
  `provider` + `api_key`, that's what `draft_poa`/`revise_poa` call
  `providers.chat` with — not the static `AGENT_A1_PROVIDER`/
  `AGENT_A2_PROVIDER` env default. `Participant.api_key` is excluded from
  `model_dump()` (`Field(exclude=True)`), so it can never end up in an SSE
  event or a Mongo snapshot — see `tests/test_rooms.py`'s
  `test_snapshot_never_includes_an_api_key`.
- Participant ownership (`_check_owner` in `main.py`): a security review
  found that `POST /participants` (and `/tasks`, `/messages`,
  `/plan/approve`) trusted a client-supplied `user_id` outright — once BYO
  keys made a participant record consequential (it drives a real, billed
  outbound call), anyone who knew a `room_id` could re-register an
  existing `user_id` and redirect that person's agent calls, or spoof
  their tasks/messages/approval. Fixed: `POST /participants` issues a
  `participant_token` the first time a `user_id` joins; that token is
  required on every subsequent call acting as that `user_id` (missing or
  wrong -> 403). A `user_id` that's never joined stays unprotected —
  nothing to steal yet, and it keeps the fixture-fallback demo path
  (which never calls `/participants`) working unchanged. Regression tests
  for the exact exploit steps: `tests/test_participant_auth.py`.
- Mongo persistence (`db.py`): every SSE event is durably recorded (the
  "transcript"), and a room snapshot is saved at each milestone
  (`room_snapshot` in `rooms.py`). Optional — unset `MONGODB_URI`, and
  every function in `db.py` is a no-op, checked directly in
  `tests/test_db.py`. **Not covered by an integration test against a real
  Atlas cluster** — none was available in this environment; only the
  no-URI no-op path and the snapshot's own shape/exclusions are tested.

**Real, but a deliberate stand-in:**
- Ticket execution writes placeholder content
  (`# TODO: implement — ...`), not real generated code — there's no
  coding agent in this slice. What's real is the scheduling (parallel
  tickets run concurrently, sequential ones wait on `depends_on`) and the
  enforcement, not what gets written.
- `agents.draft_poa`'s opening PoA has no cross-agent visibility (by
  design — it's the *opening* plan) and, like the rest of this codebase,
  can only produce a real K2-diffable conflict if the two users' task text
  actually describes overlapping work. There's no synthetic guarantee of
  that the way a deterministic template could force.

**Not built at all:**
- Resuming an in-progress negotiation after a restart — Mongo persists a
  durable record, but the asyncio.Task actually driving a room's round
  loop is still gone the moment the process is. See `db.py`'s module
  docstring.
- Multi-browser rooms — `web/app/live` assumes one real browser as u1 and
  seeds a fixed demo participant as u2.
- Everything CLAUDE.md's roadmap already marks as future work.

## Layout

```
orchestrator/
  __init__.py
  providers.py    one chat() for Claude/GPT/Gemini/IFM, provider resolution + fallback,
                   BYO api_key override, provider_key_for (self-reported name -> registry key)
  k2.py           K2's per-round structural diff (analyze) + extract_object
  agents.py       draft_poa (opening) / revise_poa (per-round) for a1/a2
  events.py       EventBus: per-room dict-event log + SSE fan-out + db.record_event hook
  rooms.py        Room / RoomStore (in-memory) + room_snapshot (-> db.save_room)
  schemas.py      FinalPlan/Ticket/Step/... — typed, for validator.py + ticketing.py
  workspace.py    seeded demo repo, mirrors web/lib/workspace.ts
  validator.py    the disjoint-file-ownership guarantee
  ticketing.py    plan -> tickets, K2 call + validator
  execution.py    write_file enforcement + the simulated ticket executor
  db.py           MongoDB Atlas persistence, optional, no-ops without MONGODB_URI
  main.py         the FastAPI app — every endpoint in the frozen contract
  tests/
    test_validator.py     pure, no model calls
    test_ticketing.py     scripted fake chat_fn, no network
    test_execution.py     write_file + execute_room in isolation
    test_rooms.py         room_snapshot's shape and its api_key exclusion
    test_db.py            db.py's no-op behavior without MONGODB_URI
    test_byo_keys.py      provider_key_for/resolve, and that a participant's
                           own provider+key actually reaches draft_poa
    test_participant_auth.py  the participant_token fix — regression tests
                           for the exact identity-spoofing/key-hijack
                           exploit steps from the security review
    test_end_to_end.py    full room lifecycle through the real app, LLM
                           calls faked at the names main.py imported them
                           under, everything else (ticketing, validator,
                           execution) runs for real
```

## Running the tests

From the repo root:

```
pip install -r requirements.txt
pytest
```

`tests/test_end_to_end.py` asserts against `bus.history(room_id)` rather
than consuming `GET /rooms/{id}/stream` over HTTP: httpx's `ASGITransport`
collects an entire streamed response before returning it, so it can't be
used in-process to read a live SSE stream that — correctly — never ends.
The real wire format (including that the SSE frames carry a named
`event: <type>` line, which `web/lib/useLiveRoom.ts` listens for
explicitly rather than relying on `onmessage`) was checked manually
against a running `uvicorn` instance over a real socket, not in the
automated suite.
