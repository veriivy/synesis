# Synesis

**Each collaborator brings their own AI agent. The agents negotiate the plan before
anybody writes code.**

Merge conflicts are a code-level symptom of an intent-level disagreement nobody
surfaced. One person builds auth with sessions, another assumes JWT, git finds out at
3am. Synesis finds out at hour zero: each person's agent advocates for *their* stated
requirements, the agents argue until every conflict is conceded or compromised, a human
approves the resulting plan, and then each agent writes code into files only it owns.

If the agents converge without disagreeing, the product has failed. **Conflict is the
feature.**

---

## Layout

```
/engine     negotiation engine, agent runtime, provider clients   Python   (Audrey)
/server     FastAPI, SSE, MongoDB, git operations, repo mapping   Python   (Teammate 2)
/web        React + Vite + Tailwind frontend                      TS       (Teammate 1)
/fixtures   the frozen contract, as data — shared, by agreement only
/scripts    setup and dev runners
```

**Only edit the directory you own.** If you need a change elsewhere, say so in chat — do
not make it. We are building a tool that enforces file ownership; we live by the same
rule. `/fixtures` and THE FROZEN CONTRACT in `CLAUDE.md` change only when all three of
us agree out loud.

Everyone commits to `main`. No long-lived branches. Push every 20–30 minutes.

---

## Setup

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

```bash
# macOS / Linux / WSL
bash scripts/setup.sh
```

That makes one shared `.venv` for `/engine` and `/server`, installs `/web`'s npm
dependencies, and copies `.env.example` to `.env`. **Put your API keys in `.env`.**

### Confirm your model ids before you trust them

```powershell
cd engine
..\.venv\Scripts\python.exe -m engine.cli doctor
```

`doctor` prints which keys are set, which models each key can actually reach, and names
the ones that are wrong. Run it before every demo. A model id that looks right but isn't
fails here in two seconds instead of on stage.

### Run it

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1          # live agents
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Replay  # replay, no API keys
```

- api → http://localhost:8000/health
- web → http://localhost:5173

### Tests

```powershell
cd engine;  ..\.venv\Scripts\python.exe -m pytest -q
cd server;  ..\.venv\Scripts\python.exe -m pytest -q
cd web;     npm run typecheck
```

---

## How it works

### The negotiation (`/engine`)

Each agent is a **provider + model + a `user_id` it advocates for + that user's
requirement list**, where each requirement is `must-have` or `nice-to-have`.

1. **Round 0** — each agent sees the repo map and only its own user's requirements, and
   states how it would implement the feature.
2. **Rounds 1–3** — each agent sees the full prior transcript and must, for every
   conflicting requirement, make exactly one named move: **CONCEDE**, **HOLD** (with a
   stated reason), or **COMPROMISE**. Vague agreement is not a valid move.
3. A **moderator** agent judges each round and emits
   `{converged, premature, conflicts_remaining}`.
4. On convergence the moderator emits the workplan. On the round cap with conflicts
   still open, a `deadlock` event escalates both positions to the humans.

Agents in a round run **concurrently** and cannot see each other's message from that
same round — otherwise whoever spoke last would simply agree with whoever spoke first.

**Anti-agreement measures** (`engine/engine/prompts.py`) — the product dies quietly
without these:

- the system prompt frames each agent as an **advocate**, not a neutral assistant
- an agent may not concede a `must-have`; if two must-haves collide it must escalate
- every message must cite requirement ids
- the moderator flags **premature convergence** — agreement that cost nobody anything,
  or that rests on "flexible" / "configurable" / "either would work" instead of a
  decision — and forces another round
- a plan whose file ownership overlaps, or whose interface signatures are missing, is
  **rejected** and becomes a deadlock rather than being patched up

### Adding a provider is a config change

One interface (`engine/engine/providers/base.py`), two client implementations: the
Anthropic SDK, and one OpenAI-compatible client that covers OpenAI, Gemini, Kimi K2, and
Grok by `base_url` alone. Adding a provider is a row in
[`engine/engine/config.py`](engine/engine/config.py) plus a key in `.env` — no new code
path, no new dependency.

### The write sandbox (`/server`) — the technical story

Every write in the entire system goes through one function,
[`Workspace.write_file`](server/server/workspace.py). It refuses unless **all** of these
hold:

| Rule | Rejection reason |
|---|---|
| the room has a human-**approved** plan | `no_approved_plan` |
| the resolved absolute path stays inside the room's workspace | `path_escape` |
| the path is in the calling agent's `files_owned` | `path_not_owned` |
| the path is not inside `.git` | `protected_path` |

Agents are treated as untrusted processes holding a capability — a list of paths — and
nothing else. Every attempt, accepted or rejected, emits a `file_written` event.
**Rejections are the money shot of the demo**: the audience watches a conflict get
prevented structurally rather than resolved after the fact.

One git commit per agent per task, authored as that agent, staging only that task's
files — never `git add -A`.

---

## The frozen contract

Five endpoints, six event types, one workplan schema. Defined in `CLAUDE.md`, expressed
three ways that are kept in sync:

| Where | What |
|---|---|
| `engine/engine/schemas.py` | Pydantic models — what the backend emits |
| `web/src/types.ts` | TypeScript types — what the frontend renders |
| `/fixtures` | the same contract as data — what the frontend is *built against* |

`engine/tests/test_contract.py` asserts every fixture validates against the Pydantic
models, so the fixtures the frontend was built against cannot silently drift from what
the backend sends.

### Building the frontend without the backend

`/web` renders from `/fixtures`, so it never blocks on the server. And the server can
replay the whole demo from a recorded stream, with no API keys and no latency:

```bash
REPLAY_FIXTURE=fixtures/stream.jsonl uvicorn server.main:app --reload   # from server/
```

That is the **backup demo**. Record the video against it at 1pm, while it works.

---

## Degradation, deliberately

Everything that can fail at 3am fails soft:

- a provider with no key is **dropped with a warning**, not an error — the demo
  continues with fewer agents
- a provider that errors mid-round makes that agent visibly pass the round
- unparseable model JSON costs one round, never the run
  (`engine/engine/jsonx.py`)
- Mongo being unreachable costs persistence, never the demo (`server/server/db.py`)
- git missing from `PATH` is found in its usual Windows hiding places, including the
  copy bundled inside GitHub Desktop (`server/server/git_ops.py`)
- the SSE stream replays history on connect, so a browser refresh mid-demo does not
  wipe the transcript

---

## Deploy

Both halves, in the first two hours. Teams that deploy at hour 22 don't demo.

- **frontend → Vercel.** Root directory `web`. Set `VITE_API_BASE` to the backend URL.
- **backend → Vultr** (MLH prize). `uvicorn server.main:app --host 0.0.0.0 --port 8000`.
  Add the Vercel origin to `CORS_ORIGINS`. If you put nginx in front, SSE needs
  `proxy_buffering off` — the server already sends `X-Accel-Buffering: no`.
- **MongoDB Atlas** (MLH prize) — set `MONGODB_URI`. Optional at runtime by design.

## Cut order, decided in advance

1. Extra agents beyond two (`ENABLED_AGENTS=anthropic,openai`)
2. @-mentions
3. Workplan diff view
4. Live code streaming
