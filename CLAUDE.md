# CLAUDE.md — Project Context

## What we are building

A collaborative coding tool where each human collaborator brings their own AI agent.
The agents negotiate an implementation plan **on behalf of their user's stated
requirements**, surface and resolve conflicts before any code is written, get human
approval, and then write code into a shared repository under strict per-agent file
ownership.

**The thesis:** merge conflicts are a code-level symptom of an intent-level
disagreement that nobody surfaced. One person builds auth with sessions, another
assumes JWT, git finds out at 3am. This tool finds out at hour zero by making the
agents advocate for their users and reconcile the interface contract up front.

**This is NOT** a chat room where LLMs politely agree. If the agents converge without
disagreeing, the product has failed. Conflict is the feature.

## Hard constraints

- **HackCMU 2026.** Hacking starts Fri 9:00pm. **Submission closes Sat 4:00pm.**
  We submit by 3:00pm, not 3:55pm.
- Judging: 3-minute presentation + live demo.
- Judging criteria: Originality, Technical Difficulty (explicitly "real technical
  challenges vs ChatGPT wrapper"), Demo Quality, Usefulness, Track Relevance.
- **Track: Multiplayer.**
- Team of 3. Strict directory ownership (below).
- Everything must be built from scratch during the event.

## Repos

**Repo 1 (this one) — the product.** Monorepo:

```
/engine     — negotiation engine, agent runtime, provider clients  (Python)
/server     — FastAPI, SSE, MongoDB, git operations, repo mapping  (Python)
/web        — React + Vite + Tailwind frontend                     (TypeScript)
/fixtures   — shared contract samples (see below)
CLAUDE.md
.env.example
```

**Repo 2 — the demo target.** A separate, small, pristine repo (500–1,500 LOC with a
passing test suite) that our agents clone and write into. Never edited by hand. The
server clones it into a per-room scratch workspace; agents write only there. We can
re-clone for a fresh demo run at any time.

## Directory ownership (enforced socially, and it is the point)

| Owner | Directory |
|---|---|
| Audrey | `/engine` |
| Teammate 1 | `/web` |
| Teammate 2 | `/server` |
| All (by agreement only) | `/fixtures` |

Everyone commits to `main`. No long-lived branches. Push every 20–30 minutes.
We are building a tool that enforces file ownership — we live by the same rule.

## THE FROZEN CONTRACT

Frozen before code. Changing any of this requires all three of us to agree, out loud.
`/fixtures` contains one realistic sample of every event type and one complete
workplan, so the frontend is built against fixtures and does not block on the backend.

### Endpoints

```
POST /rooms                        -> { room_id }
POST /rooms/{id}/intents           -> { ok }        body: { user_id, agent_id, requirements[] }
GET  /rooms/{id}/stream            -> SSE stream of events (below)
POST /rooms/{id}/plan/approve      -> { ok }        body: { approved: bool, notes?: string }
GET  /rooms/{id}/files             -> { files: [{ path, agent_id, diff }] }
```

### SSE event types (exact field names)

```jsonc
{ "type": "agent_message",  "room_id": "...", "round": 1, "agent_id": "claude",
  "user_id": "u1", "content": "...", "ts": "..." }

{ "type": "round_complete", "room_id": "...", "round": 1, "ts": "..." }

{ "type": "plan_proposed",  "room_id": "...", "plan": { /* workplan */ }, "ts": "..." }

{ "type": "deadlock",       "room_id": "...", "round": 3,
  "unresolved": [{ "issue": "...", "positions": { "claude": "...", "gpt": "..." } }],
  "ts": "..." }

{ "type": "file_written",   "room_id": "...", "agent_id": "claude",
  "path": "src/auth/session.py", "bytes": 1240,
  "accepted": true, "reason": null, "ts": "..." }

{ "type": "commit",         "room_id": "...", "agent_id": "claude", "sha": "...",
  "message": "...", "files": ["..."], "ts": "..." }
```

### Workplan schema

```jsonc
{
  "plan_id": "...",
  "repo": { "url": "...", "commit": "..." },
  "rounds_used": 2,
  "status": "proposed",              // proposed | approved | rejected
  "tasks": [{
    "task_id": "t1",
    "title": "...",
    "description": "...",
    "owner_agent": "claude",
    "owner_user": "u1",
    "files_owned": ["src/auth/session.py"],
    "depends_on": ["t2"]
  }],
  "interface_contracts": [{
    "contract_id": "c1",
    "name": "authenticate_user",
    "kind": "function",              // function | http_endpoint | schema
    "signature": "def authenticate_user(token: str) -> User | None",
    "producer_task": "t1",
    "consumer_tasks": ["t3"],
    "notes": "..."
  }],
  "concessions": [{
    "agent": "gpt",
    "gave_up": "JWT-based auth",
    "accepted": "session cookies",
    "reason": "u1 marked 'no external services' as a must-have"
  }],
  "unresolved": []
}
```

## The negotiation engine (`/engine`) — the novel part

### Provider abstraction

One interface, many providers. Adding a provider must be a config change, not a code
change — each additional provider is a sponsor prize.

Targets: Anthropic (Claude), OpenAI (GPT), Google (Gemini), IFM (K2), xAI (Grok).
Keys in `.env`; see `.env.example`. Confirm exact model IDs at runtime, don't assume.

### Agent identity

Each agent is: a provider + model, a `user_id` it advocates for, and that user's
requirement list. Each requirement is tagged `must-have` or `nice-to-have`.

### Loop

1. **Round 0 — opening positions.** Each agent receives the repo map and its own
   user's requirements, and states how it would implement the feature.
2. **Rounds 1..N (N = 3, hard cap).** Each agent sees all prior messages and must, for
   every conflicting requirement, do exactly one of: concede, hold with a stated
   reason, or propose a compromise. Vague agreement is not a valid move.
3. **Moderator agent** runs after each round and emits
   `{ conflicts_remaining: [...], converged: bool }`.
4. On convergence (or at the round cap with conflicts resolved), the moderator emits
   the workplan JSON.
5. On round cap **with** conflicts remaining, emit a `deadlock` event with each side's
   position and escalate to the humans.

### Anti-agreement measures (critical — the demo dies without these)

- System prompt frames each agent as an **advocate**, not a neutral assistant.
- An agent may not concede a `must-have` without an explicit user override.
- Every message must reference specific requirement IDs.
- The moderator flags premature convergence: if no agent has stated a real trade-off,
  it forces another round.

### Agent tools

```
read_file(path)  -> str
write_file(path, content) -> { accepted: bool, reason?: str }
```

### `write_file` enforcement — this IS the technical story

Every write goes through one function. Reject if:

1. There is no approved plan for the room.
2. The resolved absolute path escapes the room's workspace root (path traversal).
3. The path is not in the calling agent's `files_owned` for an approved task.

Every attempt — accepted or rejected — emits a `file_written` event. **Rejections are
the money shot of the demo**: conflicts are prevented structurally, not resolved after
the fact. Treat agents as untrusted processes under least privilege.

One git commit per agent per task, authored as that agent.

## Stack

- Backend: Python + FastAPI. SSE for streaming. Git via `subprocess`.
- Persistence: **MongoDB Atlas** (rooms, transcripts, workplans) — MLH prize.
- Frontend: React + Vite + Tailwind. Deployed to Vercel.
- Backend deploy: **Vultr** — MLH prize.
- Built using **Cursor** — Cursor prize.
- Windows dev machine: run under WSL if git/subprocess behaviour gets strange.

**Deploy both halves in the first two hours.** Teams that deploy at hour 22 don't demo.

## Sponsor prize integrations (free or near-free)

| Prize | What it takes |
|---|---|
| IFM | K2 as one negotiating agent |
| MLH Gemini | Gemini as one negotiating agent |
| MLH MongoDB Atlas | persistence layer (needed anyway) |
| MLH Vultr | backend deploy target |
| Cursor / xAI | built in Cursor; Grok as a fifth agent |
| Sandia (cybersecurity) | framing: agents as untrusted processes, capability-based sandboxing, path-escape prevention — no extra build |

Skip Solana and ElevenLabs. Obvious bolt-ons.

## Timeline

- **9pm–1am** — Build block 1. Target: engine produces a valid workplan from two
  conflicting intent sets; server has rooms + SSE + clone; web renders fixtures live.
- **1am — INTEGRATION CHECKPOINT.** Real events flowing engine → server → web. Ugly is
  fine. **If not integrated by 1am, cut to two agents immediately.**
- **1am–4am** — Execution layer: tool-calling, `write_file` enforcement, git commits,
  approval gate UI.
- **2am–8am** — Staggered sleep, three-hour shifts, always two people awake.
- **8am–11am** — Add remaining providers, wire Mongo, confirm Vultr deploy.
- **10am–1pm** — Mentor office hours, TEP Simmons B. Go with a specific blocker.
- **11am–1pm** — Harden: API retries, malformed-JSON handling, round caps.
- **1pm — HARD FEATURE FREEZE. Record the backup demo video while it works.**
- **2–3pm** — Rehearse the 3-minute pitch out loud, three times, on venue wifi.
- **3pm — SUBMIT the Google form.**

## Cut order (decided in advance, executed without debate)

1. Extra agents beyond two
2. @-mentions
3. Workplan diff view
4. Live code streaming

## Demo script (3:00)

- **0:00–0:25** — Problem: two people, incompatible assumptions, git finds out at merge.
- **0:25–1:45** — Live: conflicting requirements in, agents negotiate, a concession is
  made, humans approve.
- **1:45–2:35** — Agents write code into owned files. Show commits. **Show a write
  getting rejected for touching another agent's file.**
- **2:35–3:00** — Why it isn't a wrapper: negotiation protocol, capability-based
  sandboxing, provider-agnostic architecture.

## Working rules for Claude Code

- Only modify files in the directory owned by the person running you. If a change is
  needed elsewhere, say so — do not make it.
- Never change `/fixtures` or anything in THE FROZEN CONTRACT without being told the
  team agreed.
- Prefer small, working increments over complete abstractions. This ships in 19 hours.
- Do not add dependencies without saying why.
- Every LLM call needs a timeout, a retry, and a malformed-JSON fallback. Model output
  is untrusted input.
- Commit early and often with plain messages.
