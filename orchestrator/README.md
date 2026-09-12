# orchestrator

A real FastAPI implementation of CLAUDE.md's `### Orchestrator endpoints`
and `### The K2 orchestration loop`: room creation, task intake, the K2
round-negotiation loop (PoA generation, per-round analysis, agent replies,
resolutions), plan approval, ticket decomposition with the disjoint-file-
ownership guarantee, a simulated execution phase with real `write_file`
enforcement, and a real `GET /rooms/{id}/stream` SSE endpoint.

## Run it

```
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then either drive it directly (see the curl walkthrough in this file's git
history / PR description), or point the web app at it — see `web/app/live`
and `web/.env.local.example` (`NEXT_PUBLIC_ORCHESTRATOR_URL`).

## What's real vs. what's a stand-in

**Real, with tests:**
- The full HTTP + SSE contract in `app/main.py`, matching CLAUDE.md's
  endpoint list and event shapes exactly (`app/schemas.py`, `app/events.py`
  are transcriptions of the frozen contract, cross-checked against the
  real `/fixtures` in this repo — see `tests/test_ticketing.py` and
  `tests/test_validator.py`, which parse `fixtures/plan.json` and
  `fixtures/tickets.json` directly).
- The K2 round loop (`app/k2/negotiation.py`): both PoAs generated in
  parallel, up to 3 rounds of analyze -> both agents reply in parallel ->
  reconcile, the anti-mush forced-probe-round rule, and a FinalPlan with a
  `resolutions` entry per blocking issue — including issues nobody
  conceded on, recorded honestly as unresolved rather than hidden.
- The disjoint-file-ownership validator (`app/k2/validator.py`): the actual
  code-level assertion, not a prompt. Demotes a conflicting parallel ticket
  to `sequential` with a `depends_on` edge; rejects (and asks K2 to
  regenerate) a ticket with empty `files_owned`.
- `write_file` enforcement (`app/k2/execution.py`): rejects a path that
  escapes the workspace root, a path outside the calling ticket's
  `files_owned`, or any write before the plan is approved. Proven by
  `tests/test_execution.py` directly, not staged as a demo moment.
- The timeout/retry/JSON-repair wrapper every model call goes through
  (`app/jsonx.py`) — CLAUDE.md: "Every model call: timeout, one retry, and
  a JSON-repair fallback. Model output is untrusted input."

**Real, but a deliberate stand-in:**
- K2's own calls (analysis, ticketing) use a real OpenAI-compatible client
  (`app/providers/ifm.py`, same pattern as the root `k2mod.py`) **only if**
  `IFM_API_KEY`/`IFM_BASE_URL`/`IFM_MODEL` are set — see `app/config.py`.
  Without them, K2's calls fall back to a deterministic template provider.
- Per-user agent providers (PoA generation, agent replies) are **always**
  the template provider (`app/providers/templating.py`) — real
  Claude/Gemini/GPT clients are out of scope for this slice. Swapping one
  in is a change to `app/config.py`'s `get_agent_poa_provider` /
  `get_agent_reply_provider` only; nothing in `app/k2/` needs to change,
  since they only see the `ChatProvider` interface.
- The template providers have **no semantic understanding of task text** —
  read `app/providers/templating.py`'s module docstring before trusting a
  demo with them. Two users' tasks only produce a real conflict for K2 to
  negotiate if their text happens to name the same file/feature (the same
  trick `fixtures/PoA1.json`/`PoA2.json` use deliberately).
- Ticket execution writes placeholder content (`# TODO: implement — ...`),
  not real generated code — there's no coding agent in this slice. What's
  real is the scheduling (parallel tickets run concurrently, sequential
  ones wait on `depends_on`) and the enforcement, not what gets written.

**Not built at all:**
- Mongo persistence (`app/rooms.py` is in-memory, single-process, gone on
  restart — see that file's docstring for why).
- Multi-browser rooms — `web/app/live` assumes one real browser as u1 and
  seeds a fixed demo participant as u2.
- Everything CLAUDE.md's roadmap already marks as future work.

## Layout

```
app/
  schemas.py        FinalPlan / Ticket / PoA / Analysis / ... (frozen contract types)
  events.py         SSE event models + RoomBus (per-room log + fan-out)
  rooms.py          In-memory Room / RoomStore
  workspace.py      Seeded demo repo (mirrors web/lib/workspace.ts)
  jsonx.py          timeout + retry + JSON-repair wrapper around provider.chat()
  config.py         env-driven provider selection (real IFM vs. template fallback)
  providers/
    stub.py          scripted fake provider, for unit tests
    templating.py    deterministic non-LLM providers (PoA/analysis/reply/ticketing)
    ifm.py           real OpenAI-compatible client for K2 (IFM), k2mod.py's pattern
  k2/
    poa.py           per-agent PoA generation
    analysis.py      K2's per-round structural diff
    reply.py         an agent's concede/hold/compromise response
    negotiation.py   the round loop end to end
    ticketing.py     plan -> tickets (existing slice)
    validator.py     the disjoint-ownership guarantee (existing slice)
    execution.py     write_file enforcement + the simulated executor
  main.py            the FastAPI app
tests/
  test_validator.py, test_ticketing.py   unit-level, from before this slice
  test_end_to_end.py                     full room lifecycle through the real app,
                                          including a genuine must-vs-must deadlock
                                          that forces the validator to serialize two
                                          tickets over the same file
```

## Running the tests

```
pip install -r requirements.txt
pytest
```

`tests/test_end_to_end.py` asserts against `Room.bus.log` (the in-memory
event log) rather than consuming `GET /rooms/{id}/stream` over HTTP:
httpx's `ASGITransport` collects an entire streamed response before
returning it, so it can't be used in-process to read a live SSE stream
that — correctly — never ends. The real wire format was checked manually
against a running `uvicorn` instance over a real socket (`curl -N`), not in
the automated suite.
