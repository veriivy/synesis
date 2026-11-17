"""Every blocking issue a negotiation raised — not just the ones still open
at the end.

CLAUDE.md step 4: the FinalPlan carries "a resolutions entry per blocking
issue". It used to be built from the last round's analysis alone, so an
issue one side conceded in round 2 — the demo's "one concedes" moment —
vanished from the plan as if it had never come up. The ledger follows each
blocking issue_id across rounds (k2.analyze is shown last round's
differences so open issues keep their ids), remembers the move each
advocate named on it, and records the round it stopped being blocking.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

MOVES = ("CONCEDE", "HOLD", "COMPROMISE")

# In free text only the upper-case form counts as a named move: advocates
# are told to name it ("d1: CONCEDE"), and prose like "no compromise is
# possible" must not read as one.
_SHOUTED_MOVE = re.compile(r"\b(CONCED(?:E|ES|ED|ING)|HOLD(?:S|ING)?|COMPROMIS(?:E|ES|ED|ING))\b")
_ISSUE_ID = re.compile(r"\bd\d+\b")


def _move(word: str) -> str | None:
    word = word.strip().upper()
    if word.startswith("CONCED"):
        return "CONCEDE"
    if word.startswith("HOLD"):
        return "HOLD"
    if word.startswith("COMPROMIS"):
        return "COMPROMISE"
    return None


def moves_of(reply: dict, issue_ids: Iterable[str]) -> dict[str, str]:
    """issue_id -> the move an advocate's reply named on it. Reads the
    reply's structured `moves` first (agents.revise_poa asks for one), then
    falls back to the speech: a clause naming exactly one move applies it
    to every issue_id in that clause. Model output — anything else is
    ignored, never guessed."""
    wanted = {str(i) for i in issue_ids}
    found: dict[str, str] = {}
    raw = reply.get("moves")
    if isinstance(raw, dict):
        for issue_id, word in raw.items():
            move = _move(str(word))
            if move and str(issue_id) in wanted:
                found[str(issue_id)] = move
    for clause in re.split(r"[\n;.]+", str(reply.get("content") or "")):
        ids = [i for i in _ISSUE_ID.findall(clause) if i in wanted]
        named = {_move(w) for w in _SHOUTED_MOVE.findall(clause)}
        if ids and len(named) == 1:
            move = named.pop()
            for issue_id in ids:
                found.setdefault(issue_id, move)
    return found


@dataclass
class Issue:
    issue_id: str
    topic: str
    positions: dict[str, str]
    first_round: int
    last_blocking_round: int
    settled_round: int | None = None
    #: agent_id -> the move it named the last time this issue was blocking.
    moves: dict[str, str] = field(default_factory=dict)

    @property
    def settled(self) -> bool:
        return self.settled_round is not None


class IssueLedger:
    def __init__(self) -> None:
        self.issues: dict[str, Issue] = {}

    def observe(self, analysis: dict, round_index: int) -> list[Issue]:
        """Fold one round's analysis in. Returns the issues this round
        settled: blocking before, not blocking now (gone, or down to
        minor). An issue that comes back blocking is open again."""
        blocking: dict[str, dict] = {}
        for diff in analysis.get("differences") or []:
            if not isinstance(diff, dict) or str(diff.get("severity", "")).lower() != "blocking":
                continue
            issue_id = str(diff.get("issue_id") or "").strip()
            if issue_id:
                blocking[issue_id] = diff

        for issue_id, diff in blocking.items():
            positions = diff.get("positions") if isinstance(diff.get("positions"), dict) else {}
            issue = self.issues.get(issue_id)
            if issue is None:
                self.issues[issue_id] = Issue(
                    issue_id=issue_id,
                    topic=str(diff.get("topic") or issue_id),
                    positions={str(k): str(v) for k, v in positions.items()},
                    first_round=round_index,
                    last_blocking_round=round_index,
                )
                continue
            issue.topic = str(diff.get("topic") or issue.topic)
            issue.positions = {str(k): str(v) for k, v in positions.items()} or issue.positions
            issue.last_blocking_round = round_index
            issue.settled_round = None

        settled_now = []
        for issue in self.issues.values():
            if not issue.settled and issue.issue_id not in blocking:
                issue.settled_round = round_index
                settled_now.append(issue)
        return settled_now

    def record_moves(self, reply: dict, issue_ids: Iterable[str]) -> None:
        agent_id = str(reply.get("agent_id") or "")
        for issue_id, move in moves_of(reply, issue_ids).items():
            if issue_id in self.issues:
                self.issues[issue_id].moves[agent_id] = move

    def settled(self) -> list[Issue]:
        return [i for i in self.issues.values() if i.settled]


def _join(names: list[str]) -> str:
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + f", and {names[-1]}"


def how_settled(issue: Issue, name: Callable[[str], str] = str) -> str:
    """Who moved, in words: "a2 (Blair) conceded"."""
    by_move = {m: sorted(a for a, said in issue.moves.items() if said == m) for m in MOVES}
    if by_move["CONCEDE"]:
        return f"{_join([name(a) for a in by_move['CONCEDE']])} conceded"
    if by_move["COMPROMISE"]:
        return f"settled on {_join([name(a) for a in by_move['COMPROMISE']])}'s compromise"
    if issue.moves:
        return "nobody conceded, but K2 no longer finds it blocking"
    return "K2 no longer finds it blocking"


def resolution_text(issue: Issue, name: Callable[[str], str] = str) -> tuple[str, str]:
    """(outcome, rationale) for a FinalPlan resolution on a settled issue.
    When exactly one side conceded, the outcome also names the position
    that stands — the approval's decision record needs the what, not just
    the who."""
    conceded = [a for a, said in issue.moves.items() if said == "CONCEDE"]
    standing = [a for a in issue.positions if a not in conceded]
    if len(conceded) == 1 and len(standing) == 1:
        outcome = (
            f"Resolved in round {issue.settled_round}: {name(conceded[0])} conceded to "
            f"{name(standing[0])} — {issue.positions[standing[0]].rstrip('.')}."
        )
    else:
        outcome = f"Resolved in round {issue.settled_round}: {how_settled(issue, name)}."
    span = (
        f"round {issue.first_round}"
        if issue.first_round == issue.last_blocking_round
        else f"rounds {issue.first_round}–{issue.last_blocking_round}"
    )
    stated = "; ".join(f"{name(a)}: {p}" for a, p in sorted(issue.positions.items()))
    rationale = f"{issue.topic} was blocking in {span}" + (f". Positions then — {stated}." if stated else ".")
    return outcome, rationale
