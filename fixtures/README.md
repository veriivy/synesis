# /fixtures — the frozen contract, as data

These files are the executable form of THE FROZEN CONTRACT in `CLAUDE.md`.
Frontend and K2 tests build against them so neither blocks on a live orchestrator.

| File | Schema |
|---|---|
| `context.json` | SharedContext (MVP seed) |
| `tasks.json` | Both users' task lists (`must` / `want`) |
| `PoA1.json` | PoA for agent `a1` (Claude / u1) — session cookies |
| `PoA2.json` | PoA for agent `a2` (GPT / u2) — JWT |
| `analysis.json` | K2 analysis, round 1, not converged |
| `analysis_converged.json` | K2 analysis, round 2, converged |
| `plan.json` | FinalPlan after both approvals |
| `tickets.json` | Ticket set with disjoint `files_owned` |
| `events/*.json` | One realistic sample of every SSE event type |
| `stream.jsonl` | Happy-path demo, one event per line, in order |

The two PoAs are deliberately incompatible (sessions vs JWT, overlapping
`src/auth/middleware.py`). That is the input K2 is supposed to diff.
