"""BYO key threading — CLAUDE.md: "Keys: BYO with server fallback."

providers.py's alias/resolve logic is tested directly (pure, no network).
The room-level wiring (_agent_provider_and_key, and that draft_poa/
revise_poa actually receive what it resolves) is tested through main.py
with the LLM-calling functions faked, same pattern as test_end_to_end.py.
"""

from __future__ import annotations

import pytest

from orchestrator import k2 as k2_module
from orchestrator import main as main_module
from orchestrator.providers import provider_key_for, resolve
from orchestrator.rooms import Room
from orchestrator.schemas import Participant, Task


def test_provider_key_for_maps_self_reported_names():
    assert provider_key_for("claude") == "anthropic"
    assert provider_key_for("gemini") == "google"
    assert provider_key_for("gpt") == "openai"


def test_provider_key_for_unknown_name_falls_back_to_ifm():
    assert provider_key_for("some-unknown-thing") == "ifm"


def test_resolve_trusts_a_byo_key_without_checking_env(monkeypatch):
    # No ANTHROPIC_API_KEY in the environment at all, but a caller-supplied
    # key should still resolve to anthropic, not silently fall back to ifm.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert resolve("claude", api_key="sk-user-pasted-key") == "anthropic"


def test_resolve_without_a_key_and_without_env_falls_back_or_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("IFM_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        resolve("claude")


def test_agent_provider_and_key_returns_none_without_a_mapped_participant():
    room = Room(room_id="r1")
    assert main_module._agent_provider_and_key(room, "a1") == (None, None)


def test_agent_provider_and_key_resolves_the_owning_participants_choice():
    room = Room(room_id="r1")
    room.agent_users = {"a1": "u1"}
    room.participants["u1"] = Participant(
        user_id="u1",
        display_name="Avery",
        provider="claude",
        model="claude-sonnet-4-5",
        api_key="sk-avery-key",
    )

    provider, api_key = main_module._agent_provider_and_key(room, "a1")

    assert provider == "anthropic"
    assert api_key == "sk-avery-key"


def test_agent_provider_and_key_without_a_pasted_key_still_honors_the_provider_choice():
    room = Room(room_id="r1")
    room.agent_users = {"a2": "u2"}
    room.participants["u2"] = Participant(
        user_id="u2", display_name="Blair", provider="gpt", model="gpt-4.1"
    )

    provider, api_key = main_module._agent_provider_and_key(room, "a2")

    assert provider == "openai"
    assert api_key is None


@pytest.mark.asyncio
async def test_negotiate_threads_the_participants_byo_key_into_draft_poa(
    monkeypatch,
):
    calls: list[dict] = []

    def recording_draft_poa(*, agent_id, user_id, tasks, provider=None, api_key=None):
        calls.append({"agent_id": agent_id, "provider": provider, "api_key": api_key})
        return {
            "poa_id": f"poa_{agent_id}",
            "agent_id": agent_id,
            "user_id": user_id,
            "summary": "s",
            "steps": [],
            "assumptions": [],
        }

    def fake_analyze(**kwargs):
        return {"round": kwargs["round_index"], "similarities": [], "differences": [], "converged": True}

    monkeypatch.setattr(main_module, "draft_poa", recording_draft_poa)
    monkeypatch.setattr(main_module, "analyze", fake_analyze)
    # Converges immediately (fake_analyze always returns converged=True),
    # so the loop breaks before ever calling the real (un-faked) revise_poa.
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", True)

    room = main_module.rooms.create()
    room.participants["u1"] = Participant(
        user_id="u1", display_name="Avery", provider="claude", model="m", api_key="sk-avery"
    )
    room.participants["u2"] = Participant(user_id="u2", display_name="Blair", provider="gpt", model="m")
    from orchestrator.schemas import Task

    room.tasks_by_user["u1"] = [Task(text="x", priority="must")]
    room.tasks_by_user["u2"] = [Task(text="y", priority="want")]

    await main_module._run_loop(room)

    by_agent = {c["agent_id"]: c for c in calls}
    assert by_agent["a1"] == {"agent_id": "a1", "provider": "anthropic", "api_key": "sk-avery"}
    assert by_agent["a2"] == {"agent_id": "a2", "provider": "openai", "api_key": None}


@pytest.mark.asyncio
async def test_a_users_byo_key_never_reaches_the_moderator(monkeypatch):
    """K2 is the seat WE provide. A participant's pasted key pays for their own
    advocate and their own coder, and must never fund a moderator call.

    That is structural rather than defensive — k2.analyze and
    ticketing.decompose_tickets take no api_key parameter at all, so there is
    nothing to pass. This pins it, because adding one later would be an easy,
    quiet mistake: it would silently bill one user for moderating their own
    negotiation.
    """
    moderator_calls: list[str | None] = []
    agent_calls: list[str | None] = []

    def recording_moderator_chat(*, system, user, provider="ifm", max_tokens=2048, api_key=None):
        moderator_calls.append(api_key)
        return '{"round":1,"similarities":[],"differences":[],"converged":true}'

    def recording_draft_poa(*, agent_id, user_id, tasks, provider=None, api_key=None):
        agent_calls.append(api_key)
        return {
            "poa_id": f"poa_{agent_id}",
            "agent_id": agent_id,
            "user_id": user_id,
            "summary": "s",
            "steps": [],
            "assumptions": [],
        }

    # k2.analyze resolves `chat` as a module-level name, so this reaches it.
    monkeypatch.setattr(k2_module, "chat", recording_moderator_chat)
    monkeypatch.setattr(main_module, "draft_poa", recording_draft_poa)
    monkeypatch.setattr(main_module, "STOP_ON_CONVERGE", True)

    room = main_module.rooms.create()
    room.participants["u1"] = Participant(
        user_id="u1", display_name="Avery", provider="claude", model="m", api_key="sk-avery"
    )
    room.participants["u2"] = Participant(
        user_id="u2", display_name="Blair", provider="gpt", model="m", api_key="sk-blair"
    )
    room.tasks_by_user["u1"] = [Task(text="x", priority="must")]
    room.tasks_by_user["u2"] = [Task(text="y", priority="must")]

    await main_module._run_loop(room)

    assert moderator_calls, "expected at least one moderator call"
    assert all(key is None for key in moderator_calls), (
        f"a user key reached the moderator: {moderator_calls}"
    )
    # Meanwhile the advocates did spend their own owners' keys.
    assert sorted(k for k in agent_calls if k) == ["sk-avery", "sk-blair"]
