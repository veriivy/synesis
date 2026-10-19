"""The room's north star: one overarching goal, fixed for the room's life.

Two properties, both enforced in code rather than asked for:

*It does not change.* The room's creator can set and re-set it during setup;
the moment negotiation starts it is frozen (main.py refuses a change with 409).
Its fingerprint — a hash of the exact text — goes out with every prompt block
and on the `goal_set` event, so anyone can check that the standard the agents
are being held to is the one that was agreed.

*It fades as the work gets concrete.* At the opening draft the goal is the
primary yardstick: nothing else exists yet. During negotiation it is the
tiebreaker, while K2's analysis and the other plan increasingly become what the
agents argue over. By the time a coder is writing one ticket, the approved plan
and the ticket are its guide and the goal is a sanity check. `anchor_strength`
is that curve as one number per stage; `goal_block` turns the number into how
much of the goal a prompt carries and how hard it leans on it. The number never
reaches zero — the goal is in every prompt, it just stops being the headline.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Stage = Literal["draft", "negotiate", "execute"]

#: The weakest the goal ever gets. Above zero on purpose: "refer back
#: throughout" means the coder still sees it, as a guardrail.
ANCHOR_FLOOR = 0.2
#: anchor_strength at or above this: the goal is the primary yardstick.
PRIMARY_AT = 0.75
#: At or above this (and below PRIMARY_AT): the goal is the tiebreaker.
#: Below it: background sanity check, statement only.
REFERENCE_AT = 0.45

# Where each stage sits on the 0 -> 1 "how concrete is the work" axis.
# Negotiation rounds are spread evenly between these two points.
_NEGOTIATE_FROM = 0.15
_NEGOTIATE_TO = 0.6


class Goal(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    success_criteria: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("statement")
    @classmethod
    def _strip_statement(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("statement must not be blank")
        return value

    @field_validator("success_criteria")
    @classmethod
    def _strip_criteria(cls, value: list[str]) -> list[str]:
        return [c.strip() for c in value if c and c.strip()]

    @property
    def fingerprint(self) -> str:
        """Short, stable hash of the exact goal text. Same goal -> same
        fingerprint, on any machine, in any process."""
        canonical = json.dumps(
            {"statement": self.statement, "success_criteria": self.success_criteria},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    def public(self) -> dict:
        """The wire shape: what `goal_set` and room_snapshot carry."""
        return {
            "statement": self.statement,
            "success_criteria": list(self.success_criteria),
            "fingerprint": self.fingerprint,
        }


def _progress(stage: Stage, round_index: int, max_rounds: int) -> float:
    if stage == "draft":
        return 0.0
    if stage == "execute":
        return 1.0
    if stage == "negotiate":
        rounds = max(max_rounds, 1)
        r = min(max(round_index, 1), rounds)
        frac = (r - 1) / (rounds - 1) if rounds > 1 else 0.0
        return _NEGOTIATE_FROM + (_NEGOTIATE_TO - _NEGOTIATE_FROM) * frac
    raise ValueError(f"unknown stage: {stage!r}")


def anchor_strength(stage: Stage, round_index: int = 1, max_rounds: int = 1) -> float:
    """How hard prompts at this point in the workflow lean on the goal, in
    [ANCHOR_FLOOR, 1.0]. Non-increasing from draft -> each negotiation
    round -> execute. With the default MAX_ROUNDS=3:

        draft 1.00 · round 1 0.88 · round 2 0.70 · round 3 0.52 · execute 0.20
    """
    return round(1.0 - (1.0 - ANCHOR_FLOOR) * _progress(stage, round_index, max_rounds), 2)


def anchor_tier(strength: float) -> str:
    if strength >= PRIMARY_AT:
        return "primary"
    if strength >= REFERENCE_AT:
        return "reference"
    return "background"


def goal_block(goal: Goal | None, strength: float) -> str:
    """The goal as a prompt section, sized to `strength`. Empty when the
    room has no goal, so a room that never set one prompts exactly as it
    did before this module existed."""
    if goal is None:
        return ""
    tier = anchor_tier(strength)
    header = (
        f"NORTH STAR — the room's overarching goal. Fixed for this room "
        f"(fingerprint {goal.fingerprint}). Anchor {strength:.2f}: {tier}."
    )
    criteria = "\n".join(f"- {c}" for c in goal.success_criteria)
    criteria_block = f"\nSuccess criteria:\n{criteria}" if criteria else ""

    if tier == "primary":
        return (
            f"{header}\n{goal.statement}{criteria_block}\n"
            "This is the standard the work is judged against. Prefer positions that "
            "serve the success criteria, and say which criterion a position serves. "
            "A position that serves none of them is a weak one."
        )
    if tier == "reference":
        return (
            f"{header}\n{goal.statement}{criteria_block}\n"
            "Use it as the tiebreaker: when two options both satisfy the requirements, "
            "the one that better serves the goal wins. Do not reopen points that are "
            "already settled by appealing to it."
        )
    return (
        f"{header}\n{goal.statement}\n"
        "Your more specific instructions take precedence now. Use the goal as a "
        "sanity check only: nothing you produce should contradict it."
    )
