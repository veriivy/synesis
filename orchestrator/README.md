# orchestrator (tasking slice)

Scope of what's here right now: **K2's plan -> tickets step only**
(CLAUDE.md steps 5/6 of the K2 loop). Given a FinalPlan that already passed
negotiation and got both approvals, produce a Ticket list with disjoint
`files_owned` per parallel-lane ticket.

## What's in this slice

- `app/schemas.py` — `FinalPlan` / `Ticket` (and their nested types), typed
  from CLAUDE.md's frozen `### Schemas` section.
- `app/k2/validator.py` — `validate_and_fix_tickets()`. The actual
  collision-freedom guarantee: asserts no two parallel-lane tickets share a
  path in `files_owned`; demotes a later conflicting ticket to `sequential`
  with a `depends_on` edge instead; raises `EmptyFilesOwnedError` rather than
  guessing ownership for a ticket K2 left empty. Pure code, no model calls —
  this is "validated in code, not by prompt."
- `app/k2/ticketing.py` — `decompose_plan_to_tickets()`. Prompts K2 to
  propose tickets for a plan's steps, runs them through the validator above,
  and on `EmptyFilesOwnedError` asks K2 to regenerate (bounded retries)
  instead of failing outright.
- `app/jsonx.py` — the "timeout, one retry, JSON-repair fallback" wrapper
  CLAUDE.md requires around every model call, shared by any future caller
  (PoA generation, K2's round analysis) — not reimplemented here per-caller.
- `app/providers/stub.py` — `StubChatProvider`, a scripted fake `chat()` for
  tests/dev. **Not a real provider.** Real Anthropic/OpenAI/Gemini clients
  are out of scope for this slice — build against `app.jsonx.ChatProvider`.

## What's explicitly NOT in this slice

- No FastAPI app / HTTP endpoints yet (`POST /rooms/{id}/tasks`, `/negotiate`,
  `/plan/approve`, `/execute`, `/stream`, etc.).
- No PoA generation, no K2 round-analysis/negotiation loop.
- No real provider clients.
- No `write_file` / execution-time enforcement (path-escape checks, etc.) —
  that's a separate contract clause for later.
- Nothing here touches the shared `/fixtures` directory (frozen contract) —
  `tests/fixtures/final_plan.json` is a local fixture for this slice's own
  tests only.

## Running the tests

```
pip install -r requirements.txt
pytest
```
