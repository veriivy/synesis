"""Human interjections: what people type into the room mid-negotiation
(POST /messages), and why they rejected a plan (POST /plan/approve's
`notes`), as input to the next round. CLAUDE.md step 3a: "Drain the
user-message queue; include any interjections as input to this round."

Before this existed, messages were published to the chat and then cleared
at the end of the round without any model ever reading them.

Who sees what:
  - K2 gets every interjection by name and nothing about influence. It is
    the neutral seat; a human's words are input to its diff, not a vote.
  - Each advocate gets every interjection, labelled by whose user said it.
    Its OWN user's words are instructions, and an explicit one is the only
    thing that lets it concede a `must` (CLAUDE.md's anti-mush rule, which
    had no way to be satisfied until now). The OTHER user's words are input,
    weighed by that person's influence share when the room has weights.

Only people who joined the room (and so hold a participant_token) are
queued — a message from an unverified user_id still shows in the chat, but
it never steers an agent.

A round's batch is also capped as a whole (MAX_BATCH_CHARS), split evenly
between the people who spoke: a burst of messages used to go into K2's
prompt in full, where enough of them overflow the context and leave every
round's analysis unreadable — and one person's flood could bury the other
person's single instruction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

#: Per-message cap, enforced on POST /messages. One interjection must not be
#: able to crowd the actual plans out of a prompt.
MAX_INTERJECTION_CHARS = 4000

#: Per-round cap on everything a batch adds to one prompt.
MAX_BATCH_CHARS = 8000

Source = Literal["message", "rejection"]


@dataclass(frozen=True)
class Interjection:
    user_id: str
    display_name: str
    content: str
    source: Source = "message"


def _said(i: Interjection) -> str:
    verb = "rejected the last plan" if i.source == "rejection" else "said"
    return f'{verb}: "{i.content.strip()}"'


def budgeted(batch: Sequence[Interjection]) -> tuple[list[Interjection], int]:
    """(what fits, how many were left out). Each speaker gets an equal
    share of MAX_BATCH_CHARS, spent on their rejection notes first, then
    their newest messages; everyone keeps at least one. Order is kept."""
    if sum(len(i.content) for i in batch) <= MAX_BATCH_CHARS:
        return list(batch), 0
    speakers = list(dict.fromkeys(i.user_id for i in batch))
    share = MAX_BATCH_CHARS // len(speakers)
    keep: set[int] = set()
    for user_id in speakers:
        theirs = [k for k, i in enumerate(batch) if i.user_id == user_id]
        used = 0
        for k in sorted(theirs, key=lambda k: (batch[k].source != "rejection", -k)):
            size = len(batch[k].content)
            if used and used + size > share:
                continue
            keep.add(k)
            used += size
    return [batch[k] for k in sorted(keep)], len(batch) - len(keep)


def _lines(batch: Sequence[Interjection], render: Callable[[Interjection], str]) -> str:
    kept, omitted = budgeted(batch)
    note = f"\n({omitted} earlier interjection(s) left out for length.)" if omitted else ""
    return "\n".join(render(i) for i in kept) + note


def for_moderator(batch: Sequence[Interjection]) -> str:
    if not batch:
        return ""
    lines = _lines(batch, lambda i: f"- {i.display_name} ({i.user_id}) {_said(i)}")
    return (
        "HUMAN INTERJECTIONS since the last analysis. Input to this round, not votes. "
        "Diff the PoAs as they now stand; if a human has settled a difference but the "
        "plans don't reflect it yet, it is still a difference.\n" + lines
    )


def for_advocate(
    batch: Sequence[Interjection],
    *,
    my_user_id: str,
    user_shares: Mapping[str, float] | None = None,
) -> str:
    """`user_shares` is passed only when the room assigned weights; without
    it, nobody's say is labelled and the other side's input is unweighted."""
    if not batch:
        return ""

    def label(i: Interjection) -> str:
        who = "YOUR USER" if i.user_id == my_user_id else "other side"
        say = f" · {round(user_shares.get(i.user_id, 0.0) * 100)}% say" if user_shares else ""
        return f"- [{who} · {i.display_name}{say}] {_said(i)}"

    weighing = (
        " Accommodate them in proportion to that person's say." if user_shares else ""
    )
    return (
        "HUMAN INTERJECTIONS since your last turn. These come from the people in the "
        "room and outrank K2 and the other agent.\n"
        "From YOUR USER: instructions. Follow them. An explicit instruction from your own "
        "user is the only thing that lets you concede a must requirement.\n"
        "From the other side: input, not orders." + weighing + " They never license "
        "dropping your own user's must.\n" + _lines(batch, label)
    )


def for_transcript(batch: Sequence[Interjection], round_index: int) -> str:
    if not batch:
        return ""
    lines = _lines(batch, lambda i: f"{i.display_name} ({i.user_id}) {_said(i)}")
    return f"--- round {round_index} · human interjections ---\n{lines}"
