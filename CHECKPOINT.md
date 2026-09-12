# Checkpoint — orchestrator merge, tasking + execution

Written after merging the two independently-built orchestrator implementations
(the real Gemini/GPT/IFM negotiation loop that landed on `main`, and the
tasking/ticketing/execution slice built on `feature/Shawn_orchestrator_backend`)
into one. Read this before picking up more work so effort doesn't duplicate or
drift from what's actually there.

**Update:** items 2 (merge), 4 (Mongo), and 5 (BYO keys) below are all done —
`feature/Shawn_orchestrator_backend` was merged into `main` via PR #2. `main`
was re-verified after the merge: 42/42 tests pass, web lint/build/reducer-check
all clean.

**Update 2:** item 0's security finding is **fixed**, committed directly to
`main` — `POST /participants` now issues a `participant_token` on first join;
`/participants` (re-registration), `/tasks`, `/messages`, and `/plan/approve`
all require it to act as an already-claimed `user_id`. 7 new regression tests
(`orchestrator/tests/test_participant_auth.py`) cover the exact exploit steps
from the review — re-verified the literal attack is blocked over a real
socket too. Web (`useLiveRoom.ts`, `app/live/page.tsx`) updated to carry the
token through; lint/build/`check:reducer` all still pass. 49/49 backend tests
pass.

**Update 3:** item 1 (no browser click-through) is **done** — not by a human,
but for real: no live browser was available, so `uvicorn` + `next dev` +
headless Playwright drove `/dev`, `/live`, and `/` end to end (join, task
intake, negotiate, round loop with a mid-round user interjection, approve,
tickets, execute, file selection, the participant_token fix from Update 2).
Found and fixed 2 real bugs this way: a hydration mismatch on `/dev` (a
synthetic timestamp computed differently during SSR vs. client hydration),
and the demo peer showing as the literal string `"u2"` instead of a name on
`/live`. Zero console errors after fixing both, on all three pages. The
rejected-write demo beat (CLAUDE.md's "single best moment") was confirmed
rendering correctly: `middleware.py` stays unwritten, `jwt.py` shows the real
content. **What this still doesn't cover:** a real negotiation's actual
render — no API keys are configured in this environment, so `/live`'s
negotiate call fails immediately (gracefully — confirmed the error renders
cleanly, not a hang) rather than showing real `poa_generated`/`analysis`
content. `/dev`'s fixture replay and `/`'s fixture replay both DO show that
full render (pre-recorded, not from a real model), so the rendering code
itself is proven; only "a real model's actual output renders correctly" is
still unverified — needs item 5's caveat (real keys) resolved together with
this one.

**Update 4:** the agents now **actually edit the code**. Execution used to
write `# TODO: implement — ...` stubs; the README called that out as a
deliberate stand-in ("there's no coding agent in this slice"). There is one
now — `orchestrator/coding.py`: one model call per ticket, on the
provider/key of the participant who owns that agent, shown the repo through
the real `read_file` tool and told exactly which paths it owns. It answers
in `=== FILE: path ===` blocks rather than JSON (a source file inside a JSON
string dies to one unescaped newline; a block format loses at most a
truncated last file), with one retry and then the old stub as the fallback,
so **a room with no API keys still executes end to end** —
`EXECUTION_MODE=placeholder` pins that behaviour deliberately.

The part that matters for the judged claim: every path the model returns is
pushed through `write_file` **unfiltered**, so an agent reaching outside its
ticket is refused by the runtime and reported as `file_written` with
`accepted: false`. That makes the refusal a property of the system rather
than a scripted event — with the honest flip side that on the live path it
only fires when a model actually overreaches; `/` and `/dev`'s fixture
replay are what show it deterministically for the pitch.

68 backend tests pass (was 50): `orchestrator/tests/test_coding.py` plus an
end-to-end test through the real app asserting the generated content lands
in the file and the unowned write is refused. No provider key exists here,
so the real wire path (`providers.chat` -> openai client -> socket ->
parser -> `write_file` -> `GET /files/{path}`) was driven against a local
OpenAI-compatible stand-in endpoint — three tickets, two dependency waves,
refusal firing, generated content readable back afterwards. Still unverified
for the same reason as Update 3: whether a real model writes *good* Python
in this format. The web side needed no change — `useLiveRoom.ts` already
re-fetches a file on an accepted `file_written`.

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
- 49 passing tests (42 at merge + 7 more from the participant_token fix,
  "Update 2" above); web lint/build/`check:reducer` all clean — both
  re-verified against `main` after the merge, not just the feature branch

**Net effect:** the project has gone from "two people have written
negotiation logic, nobody has a runnable product" to a room that goes
create → join → tasks → negotiate → approve → tickets → execute → files, for
real, with real model calls, and the core judged technical claim
(conflict-free parallel writes, validated in code) working and tested — on
`main`, not stuck on a branch.

## What's still missing before this is demo-ready

0. **Security finding — fixed.** ✅ `POST /participants`, `/tasks`,
   `/messages`, `/plan/approve` used to trust a client-supplied `user_id`
   with no ownership check — anyone who knew a `room_id` could re-register
   an existing `user_id` and, once BYO keys were wired in, hijack their
   real LLM calls (redirecting billed traffic + the full negotiation
   transcript to an attacker-controlled provider account), or spoof their
   tasks/messages/approval. Fixed via `participant_token`: issued by
   `POST /participants` on first join, required on every subsequent call
   acting as that `user_id` (`orchestrator/main.py`'s `_check_owner`).
   Regression tests for the exact exploit steps in
   `tests/test_participant_auth.py`; re-verified blocked over a real
   socket. Known residual scope, not fixed: a `user_id` that's never
   joined via `/participants` stays unprotected on `/tasks`/`/messages`
   (nothing to steal yet — this keeps the fixture-fallback demo path
   working) — someone could pre-seed bogus tasks under a `user_id` before
   the real person joins, though it'd be overwritten the moment they
   submit their own. Lower severity than the fixed issue; not addressed.
1. **Browser-tested — by an automated headless pass, not a human.** ✅
   `/dev`, `/live`, and `/` all driven end to end via Playwright (join, task
   intake, full round loop including a mid-round user interjection, approve,
   tickets, execute, file selection). Found and fixed 2 real bugs this way
   (see Update 3 above) — zero console errors afterward on all three pages.
   **Still open: a real negotiation's actual render is unverified** — no API
   keys are configured here, so `/live` only exercises the graceful-failure
   path, not real `poa_generated`/`analysis` content from an actual model.
   A human should still open `/live` at least once with real keys before
   the live demo, since "the rendering code works" and "a real model's
   output renders the way I expect" are different claims.
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

1. **Open `/live` with real API keys at least once**, now that the
   automated pass has proven the rendering code itself works — this is
   about verifying a real model's actual output renders the way you
   expect (timing, content shape), which nothing so far has tested.
2. **Pick and rehearse the demo room** — CLAUDE.md's own auth-cookie-vs-JWT
   scenario is already proven to work end to end. Decide now whether the
   live demo runs on real model calls or falls back to the pre-recorded
   fixture stream if a provider hiccups mid-pitch — and rehearse the
   fallback, since "the screen never sits still" is a stated hard
   requirement.
3. **10am–1pm mentor office hours** — go with a specific blocker, not "is
   this good." Good candidates: whether it's worth spending demo setup time
   on a real Atlas URI given Mongo is now wired but unverified against a
   real cluster, or whether the Sandia framing (capability-scoped writes,
   untrusted agents) lands well as a pitch point.
4. **1pm hard freeze, record the backup video** while everything still
   works.
5. **2–3pm rehearse the 3-minute pitch**, out loud, on venue wifi —
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
