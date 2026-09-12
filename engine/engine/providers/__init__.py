"""Provider factory. The only place that maps a config row to a concrete client."""

from __future__ import annotations

from ..config import PROVIDERS, Settings, api_key_for, model_for
from .anthropic_client import AnthropicClient
from .base import (
    Completion,
    LLMClient,
    Message,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from .openai_compatible import OpenAICompatibleClient

__all__ = [
    "Completion",
    "LLMClient",
    "Message",
    "ProviderError",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "build_client",
]


def build_client(provider_key: str, settings: Settings | None = None) -> LLMClient:
    """Construct the client for a provider key. Raises if the key is missing."""
    settings = settings or Settings.from_env()

    try:
        cfg = PROVIDERS[provider_key]
    except KeyError:
        raise ValueError(
            f"unknown provider {provider_key!r}; known: {sorted(PROVIDERS)}"
        ) from None

    api_key = api_key_for(provider_key)
    if not api_key:
        raise ProviderError(
            provider_key, f"{cfg.api_key_env} is not set — this agent cannot run"
        )

    common = {
        "api_key": api_key,
        "model": model_for(provider_key),
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }

    if cfg.kind == "anthropic":
        return AnthropicClient(provider_key=provider_key, **common)
    if cfg.kind == "openai_compatible":
        return OpenAICompatibleClient(
            provider_key=provider_key, base_url=cfg.base_url, **common
        )
    raise ValueError(f"provider {provider_key!r} has unknown kind {cfg.kind!r}")
