"""One chat() for every vendor. Adding a provider is a row here plus env vars.

  anthropic  -> Claude (native Messages API)
  openai     -> GPT (OpenAI chat completions)
  ifm        -> K2 (OpenAI-compatible, IFM gateway)

If the requested provider has no key, we fall back to IFM so the loop still runs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    key: str
    kind: str  # anthropic | openai_compatible
    api_key_env: str
    model_env: str
    default_model: str
    base_url_env: str | None = None
    default_base_url: str | None = None


PROVIDERS: dict[str, Provider] = {
    "ifm": Provider(
        key="ifm",
        kind="openai_compatible",
        api_key_env="IFM_API_KEY",
        model_env="IFM_MODEL",
        default_model="IFM/K2-Horizon-375B-A23B",
        base_url_env="IFM_BASE_URL",
        default_base_url="https://api.ifm.ai/v1",
    ),
    "anthropic": Provider(
        key="anthropic",
        kind="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        default_model="claude-sonnet-4-5",
    ),
    "openai": Provider(
        key="openai",
        kind="openai_compatible",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_MODEL",
        default_model="gpt-4.1",
        base_url_env="OPENAI_BASE_URL",
    ),
    "google": Provider(
        key="google",
        kind="openai_compatible",
        api_key_env="GOOGLE_API_KEY",
        model_env="GOOGLE_MODEL",
        default_model="gemini-2.5-flash",
        base_url_env="GOOGLE_BASE_URL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
}


def api_key_for(provider_key: str) -> str:
    return os.getenv(PROVIDERS[provider_key].api_key_env, "").strip()


def model_for(provider_key: str) -> str:
    cfg = PROVIDERS[provider_key]
    return os.getenv(cfg.model_env, "").strip() or cfg.default_model


def base_url_for(provider_key: str) -> str | None:
    cfg = PROVIDERS[provider_key]
    if cfg.base_url_env:
        return os.getenv(cfg.base_url_env, "").strip() or cfg.default_base_url
    return cfg.default_base_url


def has_key(provider_key: str) -> bool:
    return bool(api_key_for(provider_key))


def moderator_provider() -> str:
    return os.getenv("MODERATOR_PROVIDER", "ifm").strip() or "ifm"


def agent_provider(agent_id: str) -> str:
    defaults = {"a1": "google", "a2": "openai"}
    env_names = {"a1": "AGENT_A1_PROVIDER", "a2": "AGENT_A2_PROVIDER"}
    fallback = defaults.get(agent_id, "ifm")
    env = env_names.get(agent_id)
    if not env:
        return fallback
    raw = os.getenv(env, fallback).strip() or fallback
    if raw == "gemini":
        return "google"
    return raw


def resolve(provider_key: str) -> str:
    """Return a provider we can actually call. Missing keys fall back to IFM."""
    aliases = {"gemini": "google", "gpt": "openai", "claude": "anthropic", "k2": "ifm"}
    wanted = aliases.get(provider_key, provider_key)
    wanted = wanted if wanted in PROVIDERS else "ifm"
    if has_key(wanted):
        return wanted
    if wanted != "ifm" and has_key("ifm"):
        log.warning(
            "%s has no %s — falling back to IFM/K2 for this call",
            wanted,
            PROVIDERS[wanted].api_key_env,
        )
        return "ifm"
    raise RuntimeError(
        f"no API key for {wanted} (set {PROVIDERS[wanted].api_key_env}) "
        "and IFM_API_KEY is also empty"
    )


def provider_status() -> dict[str, object]:
    return {
        key: {
            "key_set": has_key(key),
            "model": model_for(key),
        }
        for key in PROVIDERS
    }


def _openai_complete(cfg: Provider, *, system: str, user: str, max_tokens: int) -> str:
    client = OpenAI(
        api_key=api_key_for(cfg.key),
        base_url=base_url_for(cfg.key),
        timeout=90.0,
        max_retries=1,
    )
    kwargs: dict = {
        "model": model_for(cfg.key),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        resp = client.chat.completions.create(**kwargs, max_tokens=max_tokens)
    except Exception as exc:
        if "max_token" not in str(exc).lower():
            raise
        resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def _anthropic_complete(cfg: Provider, *, system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key_for(cfg.key), timeout=90.0)
    resp = client.messages.create(
        model=model_for(cfg.key),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _complete(cfg: Provider, *, system: str, user: str, max_tokens: int) -> str:
    if cfg.kind == "anthropic":
        return _anthropic_complete(cfg, system=system, user=user, max_tokens=max_tokens)
    return _openai_complete(cfg, system=system, user=user, max_tokens=max_tokens)


def chat(*, system: str, user: str, provider: str = "ifm", max_tokens: int = 2048) -> str:
    """One completion. Pass provider='google' | 'openai' | 'anthropic' | 'ifm'.

    Auth/quota failures on Claude/GPT/Gemini retry once on IFM so a round still
    produces a revised plan instead of echoing the old one.
    """
    key = resolve(provider)
    cfg = PROVIDERS[key]
    log.info("chat provider=%s model=%s", key, model_for(key))
    try:
        return _complete(cfg, system=system, user=user, max_tokens=max_tokens)
    except Exception as exc:
        if key == "ifm":
            raise
        log.warning("%s call failed (%s); retrying on IFM/K2", key, exc)
        ifm = PROVIDERS["ifm"]
        return _complete(ifm, system=system, user=user, max_tokens=max_tokens)
