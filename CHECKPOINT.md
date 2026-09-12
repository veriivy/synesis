# Checkpoint — orchestrator merge, tasking + execution

Written after merging the two independently-built orchestrator implementations
(the real Gemini/GPT/IFM negotiation loop that landed on `main`, and the
tasking/ticketing/execution slice built on `feature/Shawn_orchestrator_backend`)
into one. Read this before picking up more work so effort doesn't duplicate or
drift from what's actually there.

## Where things stand

**On `main` (merged, working):**
- Real Gemini (a1) / ChatGPT (a2) / IFM-K2 negotiation loop — `providers.py`,
  `agents.py`, `k2.py`
- Web frontend — fixture-driven `/` and `/dev` pages, fully working
  reducer/event-sourcing architecture (`web/lib/roomReducer.ts`)

**On `feature/Shawn_orchestrator_backend` (pushed, PR open, not yet merged):**
- The full endpoint contract wired onto that real loop: task intake,
  `FinalPlan` building, the disjoint-file-ownership validator, ticket
  decomposition, `write_file` enforcement, execution
- `web/app/live` — a real page that talks to the real backend over SSE, not
  fixtures
- 28 passing tests, verified manually end-to-end over a real socket

**Net effect:** the project has gone from "two people have written
negotiation logic, nobody has a runnable product" to one merge away from a
room that goes create → join → tasks → negotiate → approve → tickets →
execute → files, for real, with real model calls, and the core judged
technical claim (conflict-free parallel writes, validated in code) working
and tested.

## What's still missing before this is demo-ready

1. **Nobody's clicked through it in a browser yet.** Everything above is
   verified via curl/pytest, not by opening `/live` and watching two rounds
   of negotiation render. This is the highest-priority next step — do it
   before anything else, because UI bugs (a card that doesn't render
   `poa_generated` correctly, a state transition that never fires) are
   exactly what automated tests won't catch and what the judges will
   actually see.
2. **The PR isn't merged.** It's a big diff (two orchestrators became one) —
   get eyes on it and merge before more work stacks on top of `main` and
   creates a third divergence.
3. **No real multi-browser room.** `/live` hardcodes a fixed demo peer for
   u2. Fine for a solo click-through; not fine for "two developers,
   incompatible assumptions" as an actual live demo unless one person plays
   both parts convincingly.
4. **Mongo isn't wired.** Rooms are in-memory globals — a server restart
   loses everything. Low risk for a demo (one process, one sitting) but
   worth knowing before anyone restarts the server mid-rehearsal.
5. **BYO keys aren't threaded through `/live`'s participant join** —
   `api_key` is collected by `SetupModal` and sent to `/participants`, but
   nothing in the backend currently reads a per-participant key instead of
   the server's env key for that user's provider calls.

## Suggested order of operations from here

Given CLAUDE.md's own timeline, this checkpoint lands roughly at the
"8am–11am: wire providers, confirm K2" milestone — the merge above basically
*is* that checkpoint, done a bit more thoroughly. Next:

1. **Merge the PR.**
2. **Click through `/live` in a real browser**, both users' worth of it,
   watch for anything that renders wrong. Fix what you find.
3. **Pick and rehearse the demo room** — CLAUDE.md's own auth-cookie-vs-JWT
   scenario is already proven to work end to end. Decide now whether the
   live demo runs on real model calls or falls back to the pre-recorded
   fixture stream if a provider hiccups mid-pitch — and rehearse the
   fallback, since "the screen never sits still" is a stated hard
   requirement.
4. **10am–1pm mentor office hours** — go with a specific blocker, not "is
   this good." Good candidates: BYO-key wiring, or whether the Sandia
   framing (capability-scoped writes, untrusted agents) lands well as a
   pitch point.
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
