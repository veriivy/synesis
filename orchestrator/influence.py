"""Per-person influence: how much say a participant — and the agent that
argues for them — has in the negotiation.

A weight is a lean, not a lock. It acts in exactly three places:

1. *The advocate's stance* (`stance_block`). An agent whose user holds the
   larger share is told it may HOLD on a shorter justification; the smaller
   share is told to spend its HOLDs on must-haves and COMPROMISE elsewhere.
   Prompt-level, so the model can still concede to a better argument — and
   is told to.
2. *Deadlock defaults* (`weigh_deadlock`). A blocking issue still open when
   the round cap hits used to always end as "needs a user decision". Now it
   defaults to one side when that side is clearly ahead on influence share
   plus a bonus for the position K2 judged closer to the room's goal. Close
   calls still go to the humans. This is arithmetic in code, not a prompt.
3. Nowhere else. K2 never sees the weights — it judges the diff and the goal
   on merit, which is what lets its `goal_favors` call act as a check on a
   heavy weight. And the approval gate stays unanimous: more say over what
   the plan contains is not the power to skip someone's sign-off. That veto is
   what keeps a weighted default from being a dictate.

Weights are positive numbers, relative to each other (2 vs 1 is the same as
10 vs 5). Anyone not given one weighs DEFAULT_WEIGHT.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_WEIGHT = 1.0
MAX_WEIGHT = 10.0

#: How far above/below an even split a share has to be before the stance
#: prompt calls it a larger/smaller say rather than even.
STANCE_LEAN = 0.1
#: Added to the deadlock score of whichever position K2 says better serves
#: the goal. Big enough to break an even split, small enough that a clear
#: weight lead still carries — 3:2 weights with the goal favouring the
#: lighter side come out level, and level goes to the humans.
GOAL_TIEBREAK_BONUS = float(os.getenv("GOAL_TIEBREAK_BONUS", "0.2") or 0.2)
#: A deadlock defaults to the top-scoring side only when it leads by at
#: least this much. Anything closer is the humans' call.
DECISIVE_MARGIN = float(os.getenv("INFLUENCE_DECISIVE_MARGIN", "0.15") or 0.15)


def shares(weights: Mapping[str, float], ids: Iterable[str]) -> dict[str, float]:
    """Each id's fraction of the total weight among `ids`. Missing ids weigh
    DEFAULT_WEIGHT. Sums to 1 (or is empty)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    raw = {i: max(float(weights.get(i, DEFAULT_WEIGHT)), 0.0) for i in ids}
    total = sum(raw.values())
    if total <= 0:
        return {i: 1.0 / len(ids) for i in ids}
    return {i: raw[i] / total for i in ids}


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def stance_block(display_name: str, share: float, n_parties: int) -> str:
    """The advocate's influence section. `share` is its user's fraction of
    the say among the `n_parties` negotiating."""
    even = 1.0 / max(n_parties, 1)
    header = (
        f"INFLUENCE — {display_name} holds {_pct(share)} of the say in this room "
        f"(an even split would be {_pct(even)}). The room assigns this, not you."
    )
    if share >= even + STANCE_LEAN:
        return (
            f"{header}\nYou have the larger say. You may HOLD a contested point on a "
            "shorter justification, and an issue still deadlocked at the end defaults "
            "toward your side. It is a lean, not a veto: if the other side's option "
            "better serves the goal or the facts, concede or compromise anyway. "
            "Stonewalling a better answer is a failure, not a win."
        )
    if share <= even - STANCE_LEAN:
        return (
            f"{header}\nYou have the smaller say. Spend your HOLDs on your user's must "
            "requirements and COMPROMISE readily on everything else; an issue still "
            "deadlocked at the end defaults away from you unless the goal favours your "
            "position. A smaller say never means dropping a must — that rule is unchanged."
        )
    return (
        f"{header}\nInfluence is even. Neither side gets a default; win on the merits."
    )


@dataclass(frozen=True)
class Verdict:
    """How one deadlocked blocking issue comes out under the weights.
    `winner` is an agent_id, or None when it is too close to call."""

    winner: str | None
    scores: dict[str, float]
    goal_favors: str | None

    def describe(self, agent_shares: Mapping[str, float]) -> str:
        parts = []
        for agent_id, score in sorted(self.scores.items()):
            bonus = " + goal" if agent_id == self.goal_favors else ""
            parts.append(f"{agent_id} {_pct(agent_shares.get(agent_id, 0.0))} say{bonus} = {score:.2f}")
        return "; ".join(parts)


def weigh_deadlock(diff: Mapping, agent_shares: Mapping[str, float]) -> Verdict:
    """Score each position on a still-blocking difference: the agent's
    influence share, plus GOAL_TIEBREAK_BONUS if K2 marked that position as
    the one that better serves the goal. The leader wins the default only by
    DECISIVE_MARGIN or more."""
    positions = diff.get("positions") or {}
    agents = [str(a) for a in positions if str(a) in agent_shares] or list(agent_shares)
    favored = diff.get("goal_favors")
    favored = str(favored) if favored is not None and str(favored) in agents else None

    scores = {
        a: round(agent_shares.get(a, 0.0) + (GOAL_TIEBREAK_BONUS if a == favored else 0.0), 4)
        for a in agents
    }
    if len(scores) < 2:
        return Verdict(winner=None, scores=scores, goal_favors=favored)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_score), (_, runner_up) = ranked[0], ranked[1]
    winner = top if top_score - runner_up >= DECISIVE_MARGIN - 1e-9 else None
    return Verdict(winner=winner, scores=scores, goal_favors=favored)
