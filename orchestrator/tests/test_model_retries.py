"""CLAUDE.md: "Every model call: timeout, one retry, and a JSON-repair
fallback." What the retry has to carry, and what a double failure must
look like to the loop.

Before: K2's retry sent only "Round N. Reply with compact valid JSON" — no
plans, no tasks — so a "successful" retry was K2 diffing nothing, and a
double failure looked exactly like "no differences". The advocates had no
retry at all: one malformed reply froze that side's plan for the round.
"""

from __future__ import annotations

import json

import orchestrator.agents as agents_module
import orchestrator.k2 as k2_module
from orchestrator.agents import JSON_RETRY_NOTE, revise_poa
from orchestrator.k2 import PROBE_RULE, RETRY_NOTE, analyze

POA1 = {"poa_id": "p1", "agent_id": "a1", "user_id": "u1", "summary": "cookies", "steps": [], "assumptions": []}
POA2 = {"poa_id": "p2", "agent_id": "a2", "user_id": "u2", "summary": "bearer JWT", "steps": [], "assumptions": []}
GOOD = {
    "similarities": [],
    "differences": [
        {"issue_id": "d1", "topic": "auth", "positions": {"a1": "cookies", "a2": "JWT"}, "severity": "blocking"}
    ],
    "converged": False,
}


class Replies:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.users: list[str] = []

    def __call__(self, *, system, user, provider="ifm", max_tokens=2048, api_key=None) -> str:
        self.users.append(user)
        return self.replies.pop(0)


def run_analyze(monkeypatch, *replies: str, **kw) -> tuple[dict, Replies]:
    chat = Replies(*replies)
    monkeypatch.setattr(k2_module, "chat", chat)
    result = analyze(context={}, tasks={}, poa1=POA1, poa2=POA2, round_index=2, **kw)
    return result, chat


def test_k2s_retry_resends_the_plans(monkeypatch):
    result, chat = run_analyze(monkeypatch, "Sure! Here's my analysis: d1 is", json.dumps(GOOD))

    assert len(chat.users) == 2
    assert '"summary": "bearer JWT"' in chat.users[1], "the retry must still show K2 both plans"
    assert chat.users[1].endswith(RETRY_NOTE)
    assert [d["issue_id"] for d in result["differences"]] == ["d1"]
    assert "moderator_failed" not in result


def test_two_unreadable_replies_are_marked_not_mistaken_for_agreement(monkeypatch):
    result, _ = run_analyze(monkeypatch, "no json", "still no json")

    assert result["moderator_failed"] is True
    assert result["differences"] == [] and result["converged"] is False


def test_k2_sees_last_rounds_differences_and_the_probe_rule(monkeypatch):
    _, chat = run_analyze(
        monkeypatch, json.dumps(GOOD), previous_differences=GOOD["differences"], probe=True
    )

    assert "Last round's differences" in chat.users[0] and '"issue_id": "d1"' in chat.users[0]
    assert PROBE_RULE in chat.users[0]


def test_an_ordinary_round_has_neither(monkeypatch):
    _, chat = run_analyze(monkeypatch, json.dumps(GOOD))

    assert "Last round's differences" not in chat.users[0]
    assert "PROBE ROUND" not in chat.users[0]


def test_k2_is_told_to_keep_open_issue_ids():
    assert "keeps its" in k2_module.SYSTEM and "issue_id" in k2_module.SYSTEM


# --- advocates ----------------------------------------------------------------------


REVISED = {
    "content": "d1: CONCEDE — JWT it is.",
    "addresses_issues": ["d1"],
    "moves": {"d1": "CONCEDE"},
    "poa": {**POA1, "summary": "bearer JWT after all"},
}


def run_revise(monkeypatch, *replies: str) -> tuple[dict, Replies]:
    chat = Replies(*replies)
    monkeypatch.setattr(agents_module, "chat", chat)
    reply = revise_poa(poa=POA1, tasks={}, analysis=GOOD, round_index=1, provider="ifm")
    return reply, chat


def test_an_advocate_gets_one_retry_on_a_malformed_reply(monkeypatch):
    reply, chat = run_revise(monkeypatch, "I concede d1 but here's no JSON", json.dumps(REVISED))

    assert len(chat.users) == 2 and chat.users[1].endswith(JSON_RETRY_NOTE)
    assert reply["poa"]["summary"] == "bearer JWT after all"
    assert reply["moves"] == {"d1": "CONCEDE"}


def test_two_malformed_replies_keep_the_last_plan(monkeypatch):
    reply, chat = run_revise(monkeypatch, "nope", "nope again")

    assert len(chat.users) == 2
    assert reply["poa"]["summary"] == "cookies"
    assert reply["moves"] == {} and "could not revise" in reply["content"]


def test_the_advocate_prompt_asks_for_named_moves():
    assert '"moves"' in agents_module.ADVOCATE_SYSTEM
