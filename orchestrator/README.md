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

`EXECUTION_MODE` (default `agent`) controls what `POST /execute` writes.
On `agent`, each ticket costs one model call, so a four-ticket plan in two
dependency waves is two waves of real latency — the tickets within a wave
code concurrently, not serially. Set `EXECUTION_MODE=placeholder` to get
the old instant TODO stubs when you are rehearsing timing rather than
showing content.

`MONGODB_URI` is also optional — unset, every room stays in-memory only,
same as before this was added. Set it (an Atlas `mongodb+srv://...`
connection string) to get durable persistence; see "What's real" below
for exactly what that does and doesn't cover.

## The full sequence

```
POST /rooms                              -> { room_id, admin_token }
POST /rooms/{id}/goal                    (optional, admin_token — the fixed north star)
POST /rooms/{id}/weights                 (optional, admin_token — per-user influence)
POST /rooms/{id}/participants   x2       (first join = a1, second = a2;
                                          response carries a participant_token —
                                          required on every subsequent call for
                                          that user_id: /tasks, /messages, /approve)
POST /rooms/{id}/context                 (optional, admin_token; frozen once
                                          negotiation starts)
POST /rooms/{id}/tasks          x2       (optional — see below)
POST /rooms/{id}/negotiate      -> 202   (drafts run in parallel, off the event loop)
  GET /rooms/{id}/stream: poa_generated x2, then per round: analysis,
  agent_message x2, round_complete — up to MAX_ROUNDS (.env, default 3) —
  ending in plan_proposed
POST /rooms/{id}/messages       any time (queued for the next round's K2 +
                                          advocates — see "Human interjections")
POST /rooms/{id}/plan/approve   x2       (the 2nd approval decomposes
                                          tickets and emits tickets_created;
                                          approved:false + notes -> POST
                                          /negotiate again resumes with them)
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

## Room governance: the goal and influence weights

Both are optional, both are set by whoever created the room (the
`admin_token` from `POST /rooms` — a participant must not be able to hand
themselves a 10x weight), and both freeze with a 409 the moment negotiation
starts. A room that sets neither prompts and resolves exactly as before.

**The goal** (`goal.py`) — `POST /rooms/{id}/goal`
`{ admin_token, statement, success_criteria: [...] }`. One overarching goal
for the room's whole life, published as `goal_set` with a `fingerprint` (a
hash of the exact text) that every prompt block also carries. Every stage gets
it, at a strength that fades as the work gets concrete:

| stage | anchor | what the prompt carries |
|---|---|---|
| opening draft | 1.00 | statement + criteria, *primary* — say which criterion a position serves |
| round 1 / 2 / 3 | 0.88 / 0.70 / 0.52 | statement + criteria; *primary*, then *tiebreaker* |
| coding a ticket | 0.20 | statement only, *sanity check* — the ticket is the brief |

K2 gets the goal too, and adds `goal_favors` (an agent_id or null) to each
difference: which position better serves the goal, judged on merit. Each
`analysis` event carries the round's `goal_anchor`.

**Influence** (`influence.py`) — `POST /rooms/{id}/weights`
`{ admin_token, weights: { "u1": 2, "u2": 1 } }`. Relative, `0 < w <= 10`,
default 1, mergeable, settable before a user joins; published as
`weights_updated` with each user's `shares`. A weight is a lean, not a lock.
It acts in exactly two places:

1. **The advocate's stance** — the larger share may HOLD on a shorter
   justification; the smaller share is told to spend HOLDs on musts and
   COMPROMISE elsewhere. Both are told the lean is not a veto, and a smaller
   say never licenses dropping a `must`.
2. **Deadlock defaults** (`weigh_deadlock`, in code) — a blocking issue still
   open at the round cap scores each side as *influence share +
   `GOAL_TIEBREAK_BONUS` (0.2) if K2's `goal_favors` names it*. A lead of
   `INFLUENCE_DECISIVE_MARGIN` (0.15) or more makes that side's position the
   default resolution; anything closer stays "needs a user decision". So an
   even room breaks ties by the goal; 2:1 carries an issue the goal is neutral
   on but not one the goal backs the other side on; 3:1 carries both.

What it deliberately does **not** touch: K2 never sees the weights (its
`goal_favors` has to be an independent check on them), and plan approval is
still unanimous — a weighted default is a proposal the lighter user can
reject. A default is also structural, not just a note. K2 lists the `files`
each difference is about, and those files are taken out of the losing side's
steps, but only where the winner also writes them. That way the loser's coder
doesn't write the contested file after the winner, and no file ends up with
nobody writing it. The coder is shown the resolutions as well.

## Human interjections and re-negotiation (`interjections.py`)

`POST /messages` used to publish to the chat and then be cleared at the end
of the round without any model reading it. Now the loop drains the queue at
the **top** of each round (CLAUDE.md step 3a) and hands the batch to:

- **K2** — by name only, never with influence; a human's words are input to
  its diff, not a vote.
- **Both advocates** — labelled `YOUR USER` or `other side`, plus each
  speaker's `% say` when the room has weights. An advocate's own user's
  words are instructions, and an explicit one is now the only way it may
  concede a `must` (the anti-mush rule finally has its exception). The other
  side's words are input, weighed by their say, and never license dropping a
  `must`.

A message sent mid-round lands in the next round rather than vanishing. A
batch K2 reads in the final round (when no advocate turn follows) is put
back on the queue. Only joined participants are queued: an unverified
`user_id` still shows in the chat but steers nothing, and the response's
`queued` says which happened. Messages are capped at 4000 chars.

**Rejecting with a reason.** `approved: false` with `notes` publishes the
notes as that user's `user_message` and queues them. A second
`POST /negotiate` then **resumes from the latest PoAs** instead of
redrafting: it emits a `moderator_message`, round numbers keep counting
(4, 5, 6…), and the objection is on the table in the first resumed round.

**Lifecycle guards.**
- `/negotiate` is refused once the plan is approved, since a new plan would
  make `write_file` refuse a running execution.
- Votes are refused while a negotiation is running.
- Only users listed on the plan may vote.
- A repeat approval is a 409, not a second decomposition. If ticketing
  failed, approving again retries it.

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
- The coding agent (`coding.py`): each ticket's files are written by a real
  model call, on the provider/key of the participant who owns that agent
  (same BYO threading as the negotiation loop). The coder is shown the
  repo through `execution.read_file`, told exactly which paths it owns, and
  asked for whole files in a line-delimited `=== FILE: path ===` block
  format rather than JSON — a source file inside a JSON string loses
  everything to one unescaped newline or a truncated tail, where a block
  format loses at most the last file. One retry on an unparseable reply,
  then the placeholder stub, which is CLAUDE.md's timeout/retry/fallback
  rule for every model call. **Every path the model returns goes through
  `write_file`, unfiltered** — so a coder reaching for a file its ticket
  does not own is refused by the runtime and reported as `file_written`
  with `accepted: false`, which is also why that refusal is no longer a
  staged moment on the live path: it happens when a model actually
  overreaches, and the fixture demo (`web/lib/fixtures.ts`) is what shows
  it deterministically. A ticket whose coding call fails still writes its
  own files as stubs and emits a clean `error` event, so a room with no
  keys at all still executes end to end. `EXECUTION_MODE=placeholder`
  turns the whole thing off and restores the old stub behaviour.
  Tests: `tests/test_coding.py` (parser, budgeted context, the retry and
  fallback chain, refusal of an unowned path, a later ticket reading what
  an earlier one actually wrote) plus
  `tests/test_end_to_end.py::test_execution_writes_the_coding_agents_output_and_refuses_an_unowned_path`
  through the live app. **What is not verified here: a real model's actual
  code.** No provider key exists in this environment, so the real path —
  `providers.chat` -> the OpenAI client -> a socket -> `parse_file_blocks`
  -> `write_file` -> `GET /files/{path}` — was driven against a local
  OpenAI-compatible stand-in endpoint instead (three tickets, two waves,
  the refusal firing on the unowned path, the generated content readable
  back off the room afterwards). Everything but the model itself is
  therefore exercised over the wire; whether a given model reliably
  produces good Python in this format is the one thing still waiting on a
  key. Same caveat as the negotiation loop's own output, "Update 3" in
  `CHECKPOINT.md`.
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
  goal.py         the room's fixed north star + its per-stage anchor strength
  influence.py    per-user weights -> shares, advocate stance, deadlock tiebreak
  interjections.py  human messages/rejection notes -> K2 and advocate prompt sections
  events.py       EventBus: per-room dict-event log + SSE fan-out + db.record_event hook
  rooms.py        Room / RoomStore (in-memory) + room_snapshot (-> db.save_room)
  schemas.py      FinalPlan/Ticket/Step/... — typed, for validator.py + ticketing.py
  workspace.py    seeded demo repo, mirrors web/lib/workspace.ts
  validator.py    the disjoint-file-ownership guarantee
  ticketing.py    plan -> tickets, K2 call + validator
  coding.py       the coding agent: one model call per ticket -> real file content
  execution.py    read_file/write_file tools + the ticket executor
  db.py           MongoDB Atlas persistence, optional, no-ops without MONGODB_URI
  main.py         the FastAPI app — every endpoint in the frozen contract
  tests/
    test_validator.py     pure, no model calls
    test_ticketing.py     scripted fake chat_fn, no network
    test_execution.py     write_file + execute_room in isolation
    test_coding.py        the coder: block parsing, retry/fallback, and that
                           execute_room enforces whatever it hands back
    test_rooms.py         room_snapshot's shape and its api_key exclusion
    test_db.py            db.py's no-op behavior without MONGODB_URI
    test_byo_keys.py      provider_key_for/resolve, and that a participant's
                           own provider+key actually reaches draft_poa
    test_participant_auth.py  the participant_token fix — regression tests
                           for the exact identity-spoofing/key-hijack
                           exploit steps from the security review
    test_interjections.py  who sees which human message, labelled how, in which
                           round; reject-with-notes -> resumed negotiation
    test_lifecycle.py     audit regressions: approval/negotiate guards, /context
                           auth, parallel drafts, project brief, file hand-over
    scripted_room.py      shared recording fakes for the two files above
    test_governance.py    the goal's fade, the weight tiebreak table, admin-only
                           + frozen-after-start, and both wired through the app
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
