# CLAUDE.md — Project Context

## What we are building

A shared IDE where multiple humans each connect their own AI agent (Claude, Gemini, or
GPT). The humans and agents share one chat. Users state their tasks and requirements;
each user's agent drafts a Plan of Action. **K2 (IFM) then moderates**: it structurally
diffs the two plans, pushes the similarities and differences back to both agents, and
drives a bounded argument until they converge. Users can interject at any round. K2
produces a final Plan of Action requiring approval from both users, then decomposes it
into conflict-free tickets with disjoint file ownership, which the agents execute
against the shared codebase.

## The thesis

Merge conflicts are a code-level symptom of an intent-level disagreement nobody
surfaced. This tool surfaces it before a line is written — and the moderator does
structural plan diffing and conflict-free work decomposition, not summarization.

## Hard constraints

- HackCMU 2026. Submission closes **Sat 4:00pm**. We submit by **3:00pm**.
- Judging: 3-minute presentation + live demo.
- Criteria: Originality, Technical Difficulty ("real technical challenges vs ChatGPT
  wrapper"), Demo Quality, Usefulness, Track Relevance.
- **Track: Multiplayer.**
- Team of 3. Built from scratch during the event.
- **MVP scope: a predefined codebase and a predefined project context.** The "users
  discuss the idea to generate context" step is seeded, not built, tonight.

## Repo layout (one monorepo)

```
/orchestrator     Python + FastAPI. The K2 loop. Deployed to Vultr.
/web              Next.js. Deployed to Vercel.
  app/(ui)/**       pages
  components/**     UI
  styles/**
  app/api/**        API routes — thin proxy to orchestrator
  lib/**            MongoDB access layer
/fixtures         one sample of every SSE event + a full plan + a full ticket set
/workspace-seed   the predefined demo codebase. Set once. Never hand-edited after.
```

## Stack

- Orchestrator: Python + FastAPI on **Vultr**. Long-running; Vercel functions time out
  and the negotiation loop runs for minutes.
- Frontend: **Next.js on Vercel**. API routes proxy to the orchestrator.
- Persistence: **MongoDB Atlas**.
- Transport: **SSE + POST**. Client POSTs messages, subscribes to one event stream.
- Keys: **BYO with server fallback.** A user may paste their own key; if absent, the
  orchestrator uses a server-side env key. Never log a user key, never persist one to
  Mongo — hold it in memory for the room's lifetime only.
- Code panel: **file tree + syntax-highlighted read-only viewer.** No collaborative
  editing. No CRDTs. Not negotiable.

Deploy both halves within the first two hours.

## THE FROZEN CONTRACT

Frozen before code. Changing anything here requires all three of us to agree, out loud.
`/fixtures` holds a realistic sample of every event, plan, and ticket set, so the
frontend builds against fixtures and never blocks on the orchestrator.

### Orchestrator endpoints

```
POST /rooms                        -> { room_id }
POST /rooms/{id}/participants      body { user_id, display_name, provider, model, api_key? }
POST /rooms/{id}/context           body { project_context }        # MVP: seeded
POST /rooms/{id}/tasks             body { user_id, tasks: [{ text, priority }] }
POST /rooms/{id}/negotiate         -> 202, starts the K2 loop
POST /rooms/{id}/messages          body { user_id, content }       # mid-loop interjection
POST /rooms/{id}/plan/approve      body { user_id, approved, notes? }
POST /rooms/{id}/execute           -> 202, agents work the tickets
GET  /rooms/{id}/stream            -> SSE
GET  /rooms/{id}/files             -> { tree: [...] }
GET  /rooms/{id}/files/{path}      -> { path, content, last_written_by }
```

`provider` is `claude` | `gemini` | `gpt`. `priority` is `must` | `want`.

### SSE events (exact field names)

Every event carries `room_id` and `ts`.

```jsonc
{ "type": "participant_joined",  "user_id": "u1", "provider": "claude", "model": "..." }
{ "type": "poa_generated",       "agent_id": "a1", "user_id": "u1", "poa": { } }
{ "type": "analysis",            "round": 1, "similarities": [], "differences": [],
                                 "converged": false }
{ "type": "agent_message",       "round": 1, "agent_id": "a1", "content": "...",
                                 "addresses_issues": ["d1","d2"] }
{ "type": "user_message",        "round": 1, "user_id": "u1", "content": "..." }
{ "type": "moderator_message",   "round": 1, "content": "..." }
{ "type": "round_complete",      "round": 1 }
{ "type": "plan_proposed",       "plan": { } }
{ "type": "approval_updated",    "user_id": "u1", "approved": true }
{ "type": "plan_approved",       "plan_id": "..." }
{ "type": "context_updated",     "version": 2, "content": "..." }
{ "type": "tickets_created",     "tickets": [] }
{ "type": "ticket_started",      "ticket_id": "t1", "agent_id": "a1" }
{ "type": "file_written",        "ticket_id": "t1", "agent_id": "a1", "path": "...",
                                 "accepted": true, "reason": null }
{ "type": "ticket_completed",    "ticket_id": "t1", "status": "done" }
{ "type": "error",               "where": "...", "detail": "..." }
```

### Schemas

```jsonc
// PoA — one per agent
{ "poa_id": "...", "agent_id": "a1", "user_id": "u1",
  "summary": "...",
  "steps": [{ "step_id": "s1", "title": "...", "description": "...",
              "files_touched": ["src/auth.py"], "rationale": "..." }],
  "assumptions": ["..."] }

// K2 analysis — one per round
{ "round": 1,
  "similarities": [{ "topic": "...", "detail": "..." }],
  "differences": [{ "issue_id": "d1", "topic": "...",
                    "positions": { "a1": "...", "a2": "..." },
                    "severity": "blocking" }],     // blocking | minor
  "converged": false }

// FinalPlan
{ "plan_id": "...", "rounds_used": 2, "summary": "...",
  "steps": [],                                      // same shape as PoA steps
  "resolutions": [{ "issue_id": "d1", "outcome": "...", "rationale": "..." }],
  "approvals": { "u1": true, "u2": false },
  "status": "proposed" }                            // proposed | approved | rejected

// Ticket
{ "ticket_id": "t1", "plan_id": "...", "title": "...", "description": "...",
  "assigned_agent": "a1",
  "files_owned": ["src/auth.py"],
  "depends_on": [], "lane": "parallel",             // parallel | sequential
  "status": "pending" }                             // pending | running | done | failed

// SharedContext
{ "room_id": "...", "version": 2, "content": "...", "updated_at": "..." }
```

## The K2 orchestration loop (`/orchestrator`) — the novel part

### Sequence

1. On `POST /negotiate`: load shared context + both task lists.
2. Generate both PoAs **in parallel**. Emit `poa_generated` twice.
3. Round loop, **hard cap 3**:
   a. Drain the user-message queue; include any interjections as input to this round.
   b. K2 produces the analysis: similarities, differences, severity per difference.
      Emit `analysis`.
   c. If `converged` or no `blocking` differences remain, break.
   d. Send the analysis to both agents. Each must respond to **every blocking issue**
      with exactly one of: concede, hold (with reason), or propose a compromise.
      Run both in parallel. Emit `agent_message` twice.
   e. Emit `round_complete`.
4. K2 writes the FinalPlan with a `resolutions` entry per blocking issue. Emit
   `plan_proposed`.
5. Wait for approval from **both** users. On the second approval: update the shared
   context (emit `context_updated`), then decompose into tickets.
6. Emit `tickets_created`, then execute on `POST /execute`.

### Ticket decomposition — this is the technical claim

K2 proposes `files_owned` per ticket. **Then we validate in code, not by prompt:**

- Assert that no two `parallel`-lane tickets share any path in `files_owned`.
- On overlap, demote the later ticket to `sequential` with a `depends_on` edge.
- Reject and regenerate if K2 returns a ticket with an empty `files_owned`.

The guarantee that parallel agents cannot collide comes from this assertion, not from
asking a model nicely. Say exactly that to the judges.

### Agent tools during execution

```
read_file(path)            -> str
write_file(path, content)  -> { accepted: bool, reason?: str }
```

`write_file` rejects when: the resolved path escapes the workspace root; the path is
not in the calling ticket's `files_owned`; or there is no approved plan. Every attempt
emits `file_written` with `accepted` either way. **A rejected write is the single best
moment in the demo** — show it.

### Anti-mush measures (without these there is no demo)

- Each agent is prompted as an **advocate** for its user, not a neutral assistant.
- An agent may not concede a `must` requirement without an explicit user instruction.
- Every agent message must cite the `issue_id`s it addresses.
- If round 1 yields no blocking differences despite differing task lists, K2 runs one
  forced probe round before declaring convergence.

### Provider abstraction

One `chat(messages, system, model, api_key=None) -> str` interface. Adding a provider
is a config change. **K2 must run through this same interface** so the moderator can be
swapped to Claude in one line if IFM is slow, rate-limited, or out of credits. Demo on
K2; keep the fallback warm.

### Latency — the number-one demo risk

Twelve or more sequential model calls at 5–8s each is 90+ seconds of dead air in a
3-minute pitch. Required: both PoAs in parallel, both agent replies each round in
parallel, stream tokens into the chat as they arrive, cap at 3 rounds, and keep one
pre-warmed completed room as a fallback. The screen never sits still.

Every model call: timeout, one retry, and a JSON-repair fallback. Model output is
untrusted input.

## Timeline

- **Now–1am** — Orchestrator: providers + PoA generation + K2 analysis, runnable from a
  terminal. Web: chat and code panels rendering `/fixtures`. Platform: Mongo connected,
  both deploys live, SSE proxy passing fixture events through.
- **1am — INTEGRATION CHECKPOINT.** A real `poa_generated` event travels orchestrator to
  Next.js API route to browser. Ugly is fine. **If not integrated by 1am, cut the round
  loop to a single analysis pass and ship that.**
- **1am–4am** — Full round loop, approval gate, ticket decomposition plus the
  disjointness validator, `write_file` enforcement.
- **2am–8am** — Staggered sleep, three-hour shifts, two people awake at all times.
- **8am–11am** — Wire Gemini and GPT as selectable providers. Confirm K2. Verify the
  Vultr and Vercel deploys from a phone hotspot, not campus wifi.
- **10am–1pm** — Mentor office hours, TEP Simmons B. Go with a specific blocker.
- **11am–1pm** — Harden: retries, malformed JSON, round caps, reconnect on SSE drop.
- **1pm — HARD FEATURE FREEZE. Record the backup demo video while it works.**
- **2–3pm** — Rehearse the 3-minute pitch three times, out loud, on venue wifi.
- **3pm — SUBMIT the Google form.**

## Cut order (decided now, executed without debate)

1. Mid-loop user interjection
2. The `sequential` lane — run parallel-only, still show `depends_on` in the UI
3. BYO keys — fall back to server keys
4. Diff highlighting in the file viewer

## Sponsor prizes

| Prize | What it takes |
|---|---|
| IFM | K2 is the moderator — core to the architecture |
| MLH Vultr | orchestrator deploy target |
| MLH MongoDB Atlas | rooms, transcripts, plans, tickets |
| MLH Gemini | Gemini as a selectable agent provider |
| Cursor / xAI | build in Cursor |
| Sandia (cybersecurity) | framing: agents as untrusted processes, capability-scoped file writes, path-escape prevention — no extra build |

Skip Solana and ElevenLabs.

## Demo script (3:00)

- **0:00–0:25** — Two developers, incompatible assumptions, git finds out at merge time.
- **0:25–1:45** — Live: two users enter conflicting requirements, two agents draft
  plans, K2 diffs them and names the blocking conflicts, the agents argue, one concedes.
- **1:45–2:35** — Both users approve. K2 decomposes into tickets with disjoint file
  ownership. Agents write in parallel. **Show a write getting rejected.**
- **2:35–3:00** — Why it isn't a wrapper: structural plan diffing, a provably
  conflict-free decomposition validated in code, capability-scoped agent writes.

## Working rules for Claude Code

- Only modify files in the directory owned by the person running you. If a change is
  needed elsewhere, say so — do not make it.
- Never change `/fixtures` or THE FROZEN CONTRACT without being told the team agreed.
- Small working increments over complete abstractions. This ships in ~18 hours.
- No new dependencies without saying why.
- Every model call: timeout, retry, malformed-output fallback.
- Commit early and often, plain messages.