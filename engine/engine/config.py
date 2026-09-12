"""Provider registry and runtime settings.

Adding a provider is a row in PROVIDERS plus a key in `.env`. No new code path, no new
dependency. That is the "config change, not a code change" claim, and it is what makes
each extra sponsor integration cheap enough to be worth doing at 8am.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ProviderConfig:
    """One provider.

    `kind` picks the client implementation:
      "anthropic"        -> the Anthropic SDK (native Messages API)
      "openai_compatible"-> the OpenAI SDK, pointed at `base_url`

    Every provider below other than Anthropic speaks an OpenAI-compatible chat
    completions API, so they all share one client. If a provider ever stops being
    compatible, it gets its own `kind` and nothing else changes.
    """

    key: str
    agent_id: str
    label: str
    kind: str
    api_key_env: str
    model_env: str
    default_model: str
    base_url: str | None = None
    prize: str = ""


PROVIDERS: dict[str, ProviderConfig] = {
    p.key: p
    for p in [
        ProviderConfig(
            key="anthropic",
            agent_id="claude",
            label="Claude",
            kind="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            model_env="ANTHROPIC_MODEL",
            default_model="claude-opus-5",
        ),
        ProviderConfig(
            key="openai",
            agent_id="gpt",
            label="GPT",
            kind="openai_compatible",
            api_key_env="OPENAI_API_KEY",
            model_env="OPENAI_MODEL",
            default_model="gpt-5",
            base_url=None,  # the SDK default
        ),
        ProviderConfig(
            key="google",
            agent_id="gemini",
            label="Gemini",
            kind="openai_compatible",
            api_key_env="GOOGLE_API_KEY",
            model_env="GOOGLE_MODEL",
            default_model="gemini-2.5-pro",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            prize="MLH Gemini",
        ),
        ProviderConfig(
            key="ifm",
            agent_id="k2",
            label="Kimi K2",
            kind="openai_compatible",
            api_key_env="IFM_API_KEY",
            model_env="IFM_MODEL",
            default_model="moonshotai/Kimi-K2-Instruct",
            # CONFIRM at runtime — IFM's gateway host is the one thing here most
            # likely to differ from what you expect. `engine.cli models ifm` checks it.
            base_url="https://api.ifm.ai/v1",
            prize="IFM",
        ),
        ProviderConfig(
            key="xai",
            agent_id="grok",
            label="Grok",
            kind="openai_compatible",
            api_key_env="XAI_API_KEY",
            model_env="XAI_MODEL",
            default_model="grok-4",
            base_url="https://api.x.ai/v1",
            prize="xAI",
        ),
    ]
}

AGENT_ID_TO_PROVIDER: dict[str, str] = {p.agent_id: p.key for p in PROVIDERS.values()}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    enabled_agents: list[str] = field(default_factory=list)
    moderator_provider: str = "anthropic"
    max_rounds: int = 3
    timeout_seconds: float = 90.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.getenv("ENABLED_AGENTS", "anthropic,openai")
        enabled = [k.strip() for k in raw.split(",") if k.strip()]
        unknown = [k for k in enabled if k not in PROVIDERS]
        if unknown:
            raise ValueError(
                f"ENABLED_AGENTS names unknown providers: {unknown}. "
                f"Known: {sorted(PROVIDERS)}"
            )
        return cls(
            enabled_agents=enabled,
            moderator_provider=os.getenv("MODERATOR_PROVIDER", "anthropic").strip(),
            max_rounds=_env_int("MAX_ROUNDS", 3),
            timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 90.0),
            max_retries=_env_int("LLM_MAX_RETRIES", 2),
        )


def api_key_for(provider_key: str) -> str | None:
    cfg = PROVIDERS[provider_key]
    return os.getenv(cfg.api_key_env) or None


def model_for(provider_key: str) -> str:
    cfg = PROVIDERS[provider_key]
    return os.getenv(cfg.model_env, "").strip() or cfg.default_model


def available_providers() -> list[str]:
    """Providers that have a key set. Everything else is skipped, not errored.

    A missing key should degrade the demo to fewer agents, never crash it.
    """
    return [k for k in PROVIDERS if api_key_for(k)]
