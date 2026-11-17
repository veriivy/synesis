"""K2 analysis — the same call k2mod.py uses, imported by the orchestrator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .providers import chat, moderator_provider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

SYSTEM = """\
You are K2, the moderator of a design negotiation. You do not advocate for either user.
You structurally diff two Plans of Action. You do not pick a winner.

Return ONE compact JSON object. No markdown, no commentary, no truncated strings.
{
  "round": 1,
  "similarities": [{ "topic": "...", "detail": "..." }],
  "differences": [{
    "issue_id": "d1",
    "topic": "...",
    "positions": { "a1": "...", "a2": "..." },
    "severity": "blocking",
    "files": ["src/..."]
  }],
  "converged": false
}

Rules:
- At most 4 similarities and 4 differences. Each detail/position is ONE short sentence.
- issue_id values are d1, d2, d3, ...
- positions keys are the agent_ids from the PoAs.
- files lists the paths (from the PoAs' files_touched) this difference is about. [] if none.
- severity is "blocking" or "minor". Two must-haves colliding is blocking.
- converged is true only if there are no blocking differences.
- When a prior-round transcript is present, diff the LATEST PoAs, not the opening drafts.
- When last round's differences are given, an issue that is still open keeps its
  issue_id; a new issue takes the next unused number. Never reuse a settled issue's id.
- Finish the JSON. Never cut a string mid-word.
"""

RETRY_NOTE = (
    "\n\nYour previous reply was not complete, valid JSON. Reply again, shorter: at most "
    "2 similarities and 3 differences, one short sentence each. Compact JSON only."
)

PROBE_RULE = (
    "\n\nPROBE ROUND. Last round you found no blocking differences, but the two users "
    "asked for different things (see Task lists). Before this room is declared converged, "
    "check specifically for conflicts the plans paper over: the same file written two "
    "ways, incompatible interfaces or data formats, one plan assuming something the other "
    "user's must-have rules out, steps that depend on an order the other plan breaks. "
    "Report each real one with an honest severity. Do not invent a conflict: if the plans "
    "genuinely agree, say so with converged true."
)


def load_fixture(name: str) -> Any:
    path = FIXTURES / name
    if not path.is_file():
        raise FileNotFoundError(f"missing fixture: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _repair_truncated_object(text: str) -> str | None:
    """Close a JSON object that was cut off mid-generation (token cap)."""
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in s:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    if in_str:
        s += '"'
    s += "".join(reversed(stack))
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    try:
        json.loads(s)
    except json.JSONDecodeError:
        return None
    return s


def extract_object(text: str) -> dict:
    """Model output is untrusted. Recover a JSON object or fail clearly."""
    text = text.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    repaired = _repair_truncated_object(text)
    if repaired:
        candidates.append(repaired)

    last_error: Exception | None = None
    for raw in candidates:
        for variant in (raw, re.sub(r",(\s*[}\]])", r"\1", raw)):
            try:
                data = json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(data, dict):
                return data
    raise ValueError(f"K2 did not return JSON ({last_error}):\n{text[:800]}")


def blocking_ids(analysis: dict) -> list[str]:
    ids = []
    for diff in analysis.get("differences") or []:
        if not isinstance(diff, dict):
            continue
        if str(diff.get("severity", "")).lower() == "blocking":
            issue_id = str(diff.get("issue_id") or "").strip()
            if issue_id:
                ids.append(issue_id)
    return ids


def has_converged(analysis: dict) -> bool:
    """Blocking diffs win over a model's converged:true tick."""
    if blocking_ids(analysis):
        return False
    return bool(analysis.get("converged"))


GOAL_RULE = (
    "\n\nThe NORTH STAR above is the room's fixed goal. On every difference, add "
    '"goal_favors": the agent_id whose position better serves that goal, or null if '
    "neither clearly does. Judge on merit only — it can break a deadlock, so do not "
    "split the difference to be polite."
)


def _normalize_differences(analysis: dict) -> None:
    """goal_favors and files are untrusted model output like the rest:
    a goal_favors that isn't one of that difference's own position keys
    becomes null, and files becomes a clean list of path strings."""
    for diff in analysis.get("differences") or []:
        if not isinstance(diff, dict):
            continue
        if "goal_favors" in diff:
            favored = diff.get("goal_favors")
            positions = diff.get("positions") if isinstance(diff.get("positions"), dict) else {}
            diff["goal_favors"] = (
                str(favored) if favored is not None and str(favored) in positions else None
            )
        if "files" in diff:
            files = diff.get("files")
            diff["files"] = [str(p) for p in files if p] if isinstance(files, list) else []


def analyze(
    *,
    context: Any,
    tasks: Any,
    poa1: Any,
    poa2: Any,
    round_index: int = 1,
    transcript: str = "",
    goal_block: str = "",
    interjections: str = "",
    previous_differences: list[dict] | None = None,
    probe: bool = False,
) -> dict:
    """One K2 call. Blocking — run in a thread from the FastAPI event loop.

    `goal_block` is goal.goal_block(...) for this round — the room's north
    star at this round's anchor strength, or "" if the room has none. K2
    gets the goal but never the participants' weights: it judges on merit,
    and influence.weigh_deadlock does the weighting in code afterwards.
    `interjections` is interjections.for_moderator(...) — the humans'
    messages since the last analysis, by name only, for the same reason.
    `previous_differences`: last round's differences, so an open issue
    keeps its issue_id and main.py can tell which issues got settled.
    `probe`: CLAUDE.md's forced probe round — the last round found no
    blocking differences although the task lists differ; look again.

    If both attempts come back unreadable, the result is marked
    `moderator_failed` so the loop never mistakes "K2 said nothing" for
    "K2 found nothing".
    """
    extra = ""
    if transcript.strip():
        clipped = transcript.strip()
        if len(clipped) > 6000:
            clipped = clipped[-6000:]
        extra = (
            "\n\nPrior-round transcript (latest PoAs matter; ignore fluff):\n" + clipped
        )
    previous = ""
    if previous_differences:
        previous = "\n\nLast round's differences:\n" + json.dumps(previous_differences, indent=2)
    user = (
        (goal_block + GOAL_RULE + "\n\n" if goal_block else "")
        + "Shared context:\n"
        + json.dumps(context, indent=2)
        + "\n\nTask lists:\n"
        + json.dumps(tasks, indent=2)
        + "\n\nPoA1:\n"
        + json.dumps(poa1, indent=2)
        + "\n\nPoA2:\n"
        + json.dumps(poa2, indent=2)
        + extra
        + previous
        + ("\n\n" + interjections if interjections else "")
        + (PROBE_RULE if probe else "")
        + f"\n\nDiff the current positions. Round is {round_index}. "
        "Compact JSON only. Finish the object."
    )
    try:
        analysis = extract_object(
            chat(system=SYSTEM, user=user, provider=moderator_provider(), max_tokens=8192)
        )
    except ValueError:
        # The retry carries the same inputs. It used to send only "Round N,
        # reply with JSON" — no plans at all — so a "successful" retry was
        # K2 diffing nothing.
        try:
            analysis = extract_object(
                chat(
                    system=SYSTEM,
                    user=user + RETRY_NOTE,
                    provider=moderator_provider(),
                    max_tokens=8192,
                )
            )
        except ValueError:
            # Do not kill the room. Round 3 often truncates; keep going.
            analysis = {
                "round": round_index,
                "similarities": [],
                "differences": [],
                "converged": False,
                "moderator_failed": True,
                "note": "moderator JSON unreadable twice; this round has no analysis",
            }
    analysis["round"] = round_index
    analysis.setdefault("similarities", [])
    analysis.setdefault("differences", [])
    _normalize_differences(analysis)
    analysis["converged"] = has_converged(analysis)
    return analysis
