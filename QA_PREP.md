# Judge Q&A prep

Answers grounded in what's actually built and tested, not the pitch. If a
judge pushes past the short answer, the longer version is there — but lead
with the short one. Organized roughly by how likely each question is.

---

## The ones you'll almost certainly get

**"Why not just use a CRDT / real-time collaborative editing, like Google Docs?"**
> Because the conflict isn't at the character level, it's at the intent
> level. Two agents can write perfectly valid, non-overlapping code and
> still be building incompatible things — session cookies vs. bearer
> tokens for the same `authenticate_user`. A CRDT resolves *who typed
> what, where*. It has nothing to say about *whether the two of you
> agreed on what you're building*. That's a merge conflict a CRDT can't
> see coming because it happens before either line of code exists.

**"How is this not just a ChatGPT wrapper?"**
> Three things a wrapper doesn't do: it structurally diffs two plans into
> named similarities and differences, not a paragraph summary — that's a
> moderator role, not a chat completion. It runs a bounded, adversarial
> negotiation where each agent has to name a stance (concede/hold/
> compromise) per issue, not just chat pleasantly. And the conflict-free
> execution guarantee is *asserted in code* — a Python function that
> rejects a colliding ticket — not "the model said it would be careful."
> [If pushed further: point at `orchestrator/validator.py` — it's ~80
> lines, show it if there's a laptop.]

**"How do you actually guarantee the two agents don't overwrite each other's files?"**
> Two layers. First, before any ticket is handed out, a validator checks
> that no two parallel-lane tickets share a file path — if they do, one
> gets demoted to run sequentially after the other, with a real dependency
> edge. Second, at write time, `write_file` independently re-checks: is
> this path in *this ticket's* owned files, does it escape the workspace
> root, is there even an approved plan. Both checks are plain code, not a
> prompt asking the model to behave. We have a regression test that
> reproduces a genuine must-vs-must deadlock and proves the second ticket
> gets serialized, not silently dropped or double-run.

**"What happens if the two agents just can't agree?"**
> They're capped at 3 rounds. If a difference is still blocking after
> round 3, the final plan records it as an honest "unresolved — needs an
> explicit human decision" rather than fabricating a compromise. That's
> deliberate: an AI moderator inventing a resolution nobody actually
> agreed to is worse than admitting it's stuck and handing it back to the
> humans.

---

## Technical depth / "prove it's hard"

**"Did you use LangChain / AutoGen / CrewAI for the multi-agent part?"**
> No — the negotiation loop, the moderator, the ticket validator, and the
> execution scheduler are all plain Python against a `chat()` interface we
> wrote ourselves. That's a deliberate choice, not just NIH: a framework
> would hide exactly the part we wanted to be the contribution — the
> structural diff and the disjointness proof.

**"What's the actual novel technical piece here?"**
> Two things, both provable, not just claimed: the ticket decomposition
> validator (parallel tickets are asserted disjoint in code, with a demote-
> to-sequential fallback, not "trust the model"), and the capability-scoped
> `write_file` tool — an agent physically cannot write outside the specific
> files its ticket owns, checked on every call, independent of whether the
> model "intended" to stay in its lane.

**"Could a malicious or prompt-injected agent write outside its assigned files?"**
> That's exactly what `write_file` is built to prevent, and it's not
> prompt-based. It rejects on three independent checks: the path isn't in
> the calling ticket's `files_owned`, the resolved path escapes the
> workspace root (so `../../etc/passwd`-style traversal fails even if a
> ticket somehow claimed it), or there's no approved plan yet. All three
> are unit-tested directly against the enforcement function, not inferred
> from behavior.

**"Does an agent actually write the code, or is the execution step faked?"**
> It writes it. Each ticket is a real model call, on that user's own
> provider, given the repo through a `read_file` tool and told exactly
> which paths it owns — it returns whole files, and they land in the
> workspace. The interesting part is that we don't trust the output:
> *every* path a model asks to write goes through `write_file`, so if it
> reaches for a file outside its ticket, the runtime refuses it and the
> refusal shows up in the event stream. Which is also the honest caveat
> — on a live run that refusal only appears if a model actually
> overreaches. The one in the recorded demo is a real captured event, not
> a mock, but it's from a run where that happened.

**"How long does a negotiation round actually take? Isn't 3 rounds of LLM calls slow for a demo?"**
> Both opening plans generate in parallel, and both agents' replies each
> round run in parallel too — so a full 3-round negotiation is up to 9
> individual model calls, but only about 6 sequential round-trips of
> latency, not 9. Execution adds one call per ticket on top of that, again
> parallel within a dependency wave. [Be honest if asked for a wall-clock
> number: this hasn't been timed end-to-end against real providers in this
> session — say so rather than guess. If execution latency threatens the
> 3-minute window on the day, `EXECUTION_MODE=placeholder` skips the
> coding calls and writes stubs instantly — the negotiation, the
> decomposition and the enforcement all still run.]

---

## Skeptical / "is this actually useful" questions

**"Real merge conflicts come from editing the same lines, not from intent disagreements — isn't this solving a rare case?"**
> It's solving the expensive case, not the common one. A line-level
> conflict costs someone 30 seconds and a rebase. An intent-level conflict
> — two people who built genuinely incompatible features and only find out
> at merge time — costs a redesign conversation *after* both sides already
> wrote the code. That's the one worth catching before either line exists.

**"What if a user disagrees with what their own agent negotiated away?"**
> Nothing executes without both users explicitly approving the final plan
> — *that* part is a real code gate (`all(approvals.values())`), not a
> suggestion, and it's tied to a verified identity now, not just a
> claimed one. Before it even gets that far, a user can interject
> mid-round to steer their own agent. One honest caveat: "don't concede a
> must-have without asking me" is currently a system-prompt instruction to
> the advocate model, not a code-enforced rule the way file ownership is
> — a real model could in principle ignore it. The approval gate is the
> backstop for exactly that case: even if an agent negotiated something
> the user doesn't like, they can still just say no.

**"How does this scale past 2 users?"**
> Honestly: it doesn't yet, and we're not claiming it does. The
> architecture (pairwise structural diff, moderator-driven convergence)
> generalizes conceptually to N agents, but everything we built and tested
> this weekend is the 2-user case. Worth saying plainly if asked, not
> worth overclaiming.

---

## Security / Sandia track

**"What's your security model? Can one user see the other's API key?"**
> API keys are held in memory for the room's lifetime only, never logged,
> never persisted — even the MongoDB snapshot layer excludes the field at
> the schema level, so there's no code path that could leak it into
> storage by accident. [If asked for proof: there's a direct test —
> `test_snapshot_never_includes_an_api_key` — asserting exactly that.]

**"Is there anything you found and fixed that's worth mentioning?"** *(only bring this up if it comes up naturally — it's a strong answer if asked, but don't volunteer "we had a vulnerability" unprompted in a pitch)*
> We ran a security review against our own diff before merging and found
> a real one: participant identity wasn't verified, so once BYO API keys
> were wired in, anyone who knew a room's ID could re-register an existing
> user and redirect their real, billed LLM calls to an attacker's account.
> Fixed with a per-participant token issued at first join, required on
> every subsequent action as that identity — and we wrote regression tests
> that reproduce the literal exploit and assert it's now rejected. That's
> the kind of thing we think this track cares about: not "we didn't have
> bugs," but "we have a process that catches them before a demo."

---

## Track / prize relevance

**"Why is this 'multiplayer' and not just 'multi-agent'?"**
> Multi-agent is the mechanism. Multiplayer is the actual constraint: two
> *humans*, each with veto power, each represented by an advocate — not a
> neutral assistant — that won't concede their must-haves without them
> saying so. Remove either human and the interesting part (an actual
> disagreement that has to be negotiated, not just delegated) disappears.

**"What would you build next with more time?"**
> In priority order: a real second-browser flow (right now one browser
> plays both participants for the demo), surfacing the rejected-write
> moment more visibly in the UI instead of just the event log, and
> opponent-aware negotiation — right now each agent drafts its opening
> plan blind to the other's, which is realistic but means the first
> round's conflict is somewhat lucky rather than guaranteed.

---

## If something breaks live

Have this ready, don't apologize for it — CLAUDE.md's own plan already
assumes this can happen:
> "If a provider call fails mid-demo, it automatically retries once and
> falls back to our own moderator model rather than stalling — that's the
> same resilience path a production deployment would need anyway."

And the actual fallback: a pre-recorded run of the identical flow, in case
live model latency or a flaky key threatens the 3-minute window.
