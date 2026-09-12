# Checkpoint — orchestrator merge, tasking + execution

Written after merging the two independently-built orchestrator implementations
(the real Gemini/GPT/IFM negotiation loop that landed on `main`, and the
tasking/ticketing/execution slice built on `feature/Shawn_orchestrator_backend`)
into one. Read this before picking up more work so effort doesn't duplicate or
drift from what's actually there.

**Update:** items 2 (merge), 4 (Mongo), and 5 (BYO keys) below are all done —
`feature/Shawn_orchestrator_backend` was merged into `main` via PR #2. `main`
was re-verified after the merge: 42/42 tests pass, web lint/build/reducer-check
all clean. A security review of the branch also ran before/around the merge —
see the new item at the top of "What's still missing," it found one real,
unresolved issue worth reading before demo prep continues.

## Where things stand

**On `main` (merged, working — PR #2 landed):**
- Real Gemini (a1) / ChatGPT (a2) / IFM-K2 negotiation loop — `providers.py`,
  `agents.py`, `k2.py`
- Web frontend — fixture-driven `/` and `/dev` pages, fully working
  reducer/event-sourcing architecture (`web/lib/roomReducer.ts`), plus
  `web/app/live` — a real page that talks to the real backend over SSE
- The full endpoint contract wired onto the negotiation loop: task intake,
  `FinalPlan` building, the disjoint-file-ownership validator, ticket
  decomposition, `write_file` enforcement, execution
- MongoDB Atlas persistence (`orchestrator/db.py`) — every SSE event durably
  recorded, a room snapshot saved at each milestone, entirely optional
  (no-ops without `MONGODB_URI`)
- BYO provider keys threaded through (`main._agent_provider_and_key`) — a
  participant's own `provider` + `api_key` from `POST /participants` now
  reaches `agents.draft_poa`/`revise_poa`'s `providers.chat` call, instead of
  always using the server's static `AGENT_A1_PROVIDER`/`AGENT_A2_PROVIDER` +
  env key
- 42 passing tests; web lint/build/`check:reducer` all clean — both
  re-verified against `main` after the merge, not just the feature branch

**Net effect:** the project has gone from "two people have written
negotiation logic, nobody has a runnable product" to a room that goes
create → join → tasks → negotiate → approve → tickets → execute → files, for
real, with real model calls, and the core judged technical claim
(conflict-free parallel writes, validated in code) working and tested — on
`main`, not stuck on a branch.

## What's still missing before this is demo-ready

0. **Security finding, unresolved: participant/task/message identity
   spoofing enables BYO-key hijack.** `POST /participants`, `/tasks`, and
   `/messages` all trust a client-supplied `user_id` with no ownership
   check — a second `POST /participants` with `user_id: "u1"` silently
   overwrites the real u1's stored `provider`/`api_key`. Because BYO keys
   are now wired in (item 5 below), this has a real, live consequence:
   whoever knows the `room_id` (routinely shared as `?room=<id>`) can
   redirect u1's agent's real LLM calls — carrying the full negotiation
   transcript — to a provider account they control, mid-negotiation, no
   race condition needed. Confirmed via an independent second-pass review
   (confidence 8/10), distinct from "the whole app has no login system"
   (that framing was checked too and rejected as the actual issue — see the
   session transcript's security-review turn for the full reasoning). Fix:
   issue a per-participant token at first join, require it on subsequent
   calls for that `user_id`, reject re-registration without it. Worth
   fixing before any demo that uses real BYO keys; lower priority if the
   demo runs entirely on server-side keys with the fixture-fallback path.
1. **Nobody's clicked through it in a browser yet.** Everything above is
   verified via curl/pytest, not by opening `/live` and watching two rounds
   of negotiation render. This is the highest-priority *usability* next
   step — do it before anything else, because UI bugs (a card that doesn't
   render `poa_generated` correctly, a state transition that never fires)
   are exactly what automated tests won't catch and what the judges will
   actually see.
2. **The PR is merged.** ✅ `feature/Shawn_orchestrator_backend` -> `main`
   via PR #2. Re-verified on `main` post-merge: 42/42 tests pass, web
   lint/build/`check:reducer` all clean.
3. **No real multi-browser room.** `/live` hardcodes a fixed demo peer for
   u2. Fine for a solo click-through; not fine for "two developers,
   incompatible assumptions" as an actual live demo unless one person plays
   both parts convincingly.
4. **Mongo is wired.** ✅ `orchestrator/db.py`: every SSE event is durably
   recorded, room snapshots save at each milestone (created, plan_proposed,
   tickets_created, execution finished). Still optional — no `MONGODB_URI`
   set anywhere yet, so it's currently running exactly as before (in-memory
   only) until someone pastes a real Atlas connection string into `.env`.
   **Not verified against a real cluster** — no credentials were available
   in this environment; only the no-URI no-op path and the snapshot's shape
   are tested (`tests/test_db.py`, `tests/test_rooms.py`). Get an Atlas URI
   and actually run a room against it before trusting this in the demo.
5. **BYO keys are threaded through.** ✅ A participant's `provider` +
   `api_key` from `POST /participants` now determines which real provider
   serves their agent (`main._agent_provider_and_key`), overriding the
   static `AGENT_A1_PROVIDER`/`AGENT_A2_PROVIDER` env default when there's a
   participant to resolve it from. `api_key` still never appears in an SSE
   event or a Mongo snapshot (`Participant.api_key`'s `Field(exclude=True)`,
   checked directly in `tests/test_rooms.py`). Covered by
   `tests/test_byo_keys.py`, including an end-to-end check that the right
   provider+key actually reaches `draft_poa`. **Not tested against a real
   pasted key hitting a real API** — the test suite fakes the call, same as
   everything else in this repo that would otherwise need a live key.

## Suggested order of operations from here

Given CLAUDE.md's own timeline, this checkpoint lands roughly at the
"8am–11am: wire providers, confirm K2" milestone — the merge above basically
*is* that checkpoint, done a bit more thoroughly. Next:

1. **Decide on the identity-spoofing fix** (item 0 above) — at minimum,
   decide whether the demo avoids the exposure entirely by not using real
   BYO keys, or whether it's worth a quick token check before relying on
   BYO keys live.
2. **Click through `/live` in a real browser**, both users' worth of it,
   watch for anything that renders wrong. Fix what you find.
3. **Pick and rehearse the demo room** — CLAUDE.md's own auth-cookie-vs-JWT
   scenario is already proven to work end to end. Decide now whether the
   live demo runs on real model calls or falls back to the pre-recorded
   fixture stream if a provider hiccups mid-pitch — and rehearse the
   fallback, since "the screen never sits still" is a stated hard
   requirement.
4. **10am–1pm mentor office hours** — go with a specific blocker, not "is
   this good." Good candidates: whether it's worth spending demo setup time
   on a real Atlas URI given Mongo is now wired but unverified against a
   real cluster, or whether the Sandia framing (capability-scoped writes,
   untrusted agents) lands well as a pitch point.
5. **1pm hard freeze, record the backup video** while everything still
   works.
6. **2–3pm rehearse the 3-minute pitch**, out loud, on venue wifi —
   specifically the moment CLAUDE.md flags: *showing a write getting
   rejected*. That's now real and testable (`orchestrator/tests/test_execution.py`),
   so rehearse actually triggering it, not just asserting it in a test.

## How to make it stronger, if there's time left after the above

Ranked by payoff-per-minute, not by interestingness:

- **A genuine two-browser demo.** Right now the strongest technical claim
  ("two independent agents, two independent users, conflict caught in
  code") is *simulated* by one browser and a scripted peer. If you can get
  a second laptop/tab acting as a real second participant before the demo,
  that's a materially stronger version of the same pitch, not a new
  feature.
- **Surface the rejected-write moment explicitly in the UI**, not just in
  the event log. It's the single best beat in the demo per CLAUDE.md's own
  notes — make sure a judge glancing at the screen can't miss it (a toast,
  a red flash on the file tree, anything).
- **A visible fallback indicator when a provider call fails and falls back
  to IFM** (`providers.chat`'s `resolve()` already does this silently).
  Turning a silent robustness feature into a visible one ("Gemini quota hit
  — falling back to K2") is free technical-difficulty credit and reads as
  *more* impressive, not less, because it shows the failure-handling live.
- **Walk-forward confidence in the round loop**: right now a must-vs-must
  deadlock produces an honest "unresolved, needs a human" resolution —
  that's correct, but if there's 20 minutes, letting a **user's mid-round
  interjection** (CLAUDE.md's cut item #1, but the `/messages` endpoint
  already exists) actually break a deadlock during the live demo would be a
  strong, cheap "look, a human can step in" beat.
- **Sandia framing is under-leveraged right now.** There are already real
  capability-scoped writes and path-escape rejection with tests proving it
  — that's exactly their track's ask, and it's currently buried in a
  README rather than in the pitch. Costs zero build time, just say it
  explicitly in the 2:35–3:00 window.
