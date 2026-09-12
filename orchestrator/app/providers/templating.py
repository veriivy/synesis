"""Deterministic, non-LLM stand-ins for the ChatProvider interface.

These exist so the whole K2 loop (PoA generation, analysis, agent replies,
ticket decomposition) runs end-to-end with zero API keys and zero network
calls — useful for local dev, CI, and as the orchestrator's default until a
real Claude/Gemini/GPT/IFM key is configured (CLAUDE.md's "BYO with server
fallback"; this is the fallback of the fallback).

They have NO semantic understanding of task text. Each caller (poa.py,
analysis.py, reply.py, k2/ticketing.py) embeds a `CONTEXT_JSON: {...}`
block in its prompt with exactly the structured data these providers act
on — a real provider ignores that marker entirely and just reads English.

Because these providers can't understand meaning, they can't invent a
genuine cross-agent conflict from arbitrary text either — two users' tasks
only collide here if their text happens to produce the same file slug. For
a live demo that shows K2 finding a real conflict, give both users task
text that names the same feature, the way fixtures/PoA1.json and
fixtures/PoA2.json were deliberately written to collide on
src/auth/middleware.py.
"""

from __future__ import annotations

import json
import re


def slugify(text: str, max_words: int = 4) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:max_words]
    return "-".join(words) or "task"


def _extract_context(messages: list[dict[str, str]]) -> dict:
    content = messages[-1]["content"]
    marker = "CONTEXT_JSON: "
    idx = content.index(marker) + len(marker)
    end = content.find("\nEND_CONTEXT_JSON", idx)
    blob = content[idx:end] if end != -1 else content[idx:]
    return json.loads(blob)


class TemplatePoAProvider:
    """One step per submitted task. files_touched is a deterministic slug
    of the task text, not a real judgment about what files a change needs."""

    async def chat(self, *, messages, system, model, api_key=None) -> str:
        ctx = _extract_context(messages)
        tasks = ctx["tasks"]
        user_id = ctx["user_id"]
        steps = []
        for i, t in enumerate(tasks, start=1):
            slug = slugify(t["text"])
            steps.append(
                {
                    "step_id": f"s{i}",
                    "title": t["text"][:60],
                    "description": t["text"],
                    "files_touched": [f"src/{slug}.py"],
                    "rationale": f"{t['priority']}-have from {user_id}'s task list.",
                }
            )
        preview = "; ".join(t["text"][:40] for t in tasks) or "no tasks submitted"
        summary = f"Address {user_id}'s {len(tasks)} task(s): {preview}"
        return json.dumps({"summary": summary, "steps": steps, "assumptions": []})


class TemplateAnalysisProvider:
    """Diffs two PoAs by files_touched overlap. A path both agents claim is
    a difference; severity is "blocking" if either side's step rationale
    says "must-have" (which TemplatePoAProvider always writes verbatim, so
    this is a reliable marker for template-generated PoAs — not something a
    real model's prose could be trusted to contain)."""

    async def chat(self, *, messages, system, model, api_key=None) -> str:
        ctx = _extract_context(messages)
        round_ = ctx["round"]
        resolved = set(ctx.get("resolved_paths", []))
        poa_a, poa_b = ctx["poa_a"], ctx["poa_b"]

        by_path_a: dict[str, dict] = {
            p: s for s in poa_a["steps"] for p in s["files_touched"]
        }
        by_path_b: dict[str, dict] = {
            p: s for s in poa_b["steps"] for p in s["files_touched"]
        }

        differences = []
        similarities = []
        shared = sorted(set(by_path_a) & set(by_path_b))
        n = 0
        for path in shared:
            step_a, step_b = by_path_a[path], by_path_b[path]
            if path in resolved:
                similarities.append(
                    {
                        "topic": f"Ownership of {path}",
                        "detail": f"Resolved in an earlier round: see the plan's resolutions.",
                    }
                )
                continue
            n += 1
            blocking = "must-have" in step_a["rationale"] or "must-have" in step_b["rationale"]
            differences.append(
                {
                    "issue_id": f"d{n}",
                    "topic": f"Both plans write {path}",
                    "positions": {
                        poa_a["agent_id"]: step_a["title"],
                        poa_b["agent_id"]: step_b["title"],
                    },
                    "severity": "blocking" if blocking else "minor",
                }
            )

        if not shared:
            similarities.append(
                {
                    "topic": "No overlapping files",
                    "detail": "The two plans touch disjoint files_touched sets.",
                }
            )

        converged = not any(d["severity"] == "blocking" for d in differences)
        return json.dumps(
            {
                "round": round_,
                "similarities": similarities,
                "differences": differences,
                "converged": converged,
            }
        )


class TemplateReplyProvider:
    """An agent's response to K2's per-round differences.

    Deterministic stance rule, matching CLAUDE.md's anti-mush measure ("An
    agent may not concede a must requirement without an explicit user
    instruction") — and needs no visibility into the other agent's
    priorities, only its own PoA and the difference's severity (which
    TemplateAnalysisProvider already computed as "blocking" iff either side
    called the path a must-have):
      - severity "blocking" and my priority "want"  -> concede
      - severity "blocking" and my priority "must"  -> hold (a real must
        can only be conceded on the user's explicit instruction, which this
        stand-in never receives — K2 has to propose a structural compromise
        once the round cap is hit instead)
      - severity "minor" (both sides "want", by construction) -> hold,
        unless I'm the lexicographically later agent_id, who concedes —
        an arbitrary but deterministic tie-break.
    """

    async def chat(self, *, messages, system, model, api_key=None) -> str:
        ctx = _extract_context(messages)
        agent_id = ctx["agent_id"]
        other_agent_id = ctx["other_agent_id"]
        my_poa = ctx["my_poa"]
        differences = ctx["differences"]

        priority_by_path: dict[str, str] = {}
        for step in my_poa["steps"]:
            priority = "must" if "must-have" in step["rationale"] else "want"
            for path in step["files_touched"]:
                priority_by_path[path] = priority

        lines = []
        addresses = []
        stances = {}
        for diff in differences:
            issue_id = diff["issue_id"]
            path = diff["topic"].removeprefix("Both plans write ")
            mine = priority_by_path.get(path, "want")
            addresses.append(issue_id)

            if diff["severity"] == "blocking" and mine == "want":
                stances[issue_id] = "concede"
                lines.append(f"{issue_id}: conceding {path} — this was a want-have for me.")
            elif diff["severity"] == "blocking":
                stances[issue_id] = "hold"
                lines.append(
                    f"{issue_id}: holding {path} — this is a must-have for me; "
                    f"I can't concede without my user's explicit instruction."
                )
            elif agent_id > other_agent_id:
                stances[issue_id] = "concede"
                lines.append(f"{issue_id}: conceding {path} — neither side needs it badly; you take it.")
            else:
                stances[issue_id] = "hold"
                lines.append(f"{issue_id}: holding {path} for now.")

        content = " ".join(lines) if lines else "No blocking issues addressed to me this round."
        return json.dumps({"content": content, "addresses_issues": addresses, "stances": stances})


class TemplateTicketingProvider:
    """One ticket per plan step, files_owned = that step's files_touched,
    assigned to whichever agent originally proposed it (from CONTEXT_JSON's
    per-step "owner", set by k2/ticketing.py from Room.step_owner). Any
    resulting file collision is left for validate_and_fix_tickets to catch
    and demote — this provider makes no attempt to avoid or predict one."""

    async def chat(self, *, messages, system, model, api_key=None) -> str:
        ctx = _extract_context(messages)
        tickets = []
        for i, step in enumerate(ctx["steps"], start=1):
            tickets.append(
                {
                    "ticket_id": f"t{i}",
                    "title": step["title"],
                    "description": step["title"],
                    "assigned_agent": step.get("owner") or "a1",
                    "files_owned": step["files_touched"] or [f"src/unspecified-{i}.py"],
                    "lane": "parallel",
                    "depends_on": [],
                }
            )
        return json.dumps(tickets)
