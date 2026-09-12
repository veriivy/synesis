"""Env-driven config and provider factories.

CLAUDE.md: "Keys: BYO with server fallback... if absent, the orchestrator
uses a server-side env key." This module is that fallback chain for K2:
real IFM if IFM_API_KEY is set, otherwise the deterministic template
provider (see app/providers/templating.py's module docstring for why a
template, not a stub, is the no-key default here).

Per-user agent providers (Claude/Gemini/GPT) are not implemented — real
clients are out of scope for this slice. get_agent_provider() always
returns the template provider; a teammate adding a real client wires it in
here, one function, without touching k2/poa.py or k2/reply.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .jsonx import ChatProvider

# Same .env the root k2mod.py reads — repo_root/.env, two levels up from
# this file (orchestrator/app/config.py -> orchestrator/ -> repo root).
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from .providers.templating import (
    TemplateAnalysisProvider,
    TemplatePoAProvider,
    TemplateReplyProvider,
    TemplateTicketingProvider,
)


@dataclass(frozen=True)
class Settings:
    ifm_api_key: str = ""
    ifm_base_url: str = ""
    ifm_model: str = ""
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    @property
    def has_real_k2(self) -> bool:
        return bool(self.ifm_api_key and self.ifm_base_url and self.ifm_model)


@lru_cache
def get_settings() -> Settings:
    origins = os.environ.get("ORCHESTRATOR_CORS_ORIGINS", "http://localhost:3000")
    return Settings(
        ifm_api_key=os.environ.get("IFM_API_KEY", "").strip(),
        ifm_base_url=os.environ.get("IFM_BASE_URL", "").strip(),
        ifm_model=os.environ.get("IFM_MODEL", "").strip(),
        cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
    )


@lru_cache
def _real_k2_provider() -> ChatProvider:
    from .providers.ifm import IFMChatProvider  # local import: openai pkg optional at call time

    s = get_settings()
    return IFMChatProvider(base_url=s.ifm_base_url, default_api_key=s.ifm_api_key)


def get_k2_analysis_provider() -> tuple[ChatProvider, str]:
    s = get_settings()
    if s.has_real_k2:
        return _real_k2_provider(), s.ifm_model
    return TemplateAnalysisProvider(), "template-k2-analysis"


def get_k2_ticketing_provider() -> tuple[ChatProvider, str]:
    s = get_settings()
    if s.has_real_k2:
        return _real_k2_provider(), s.ifm_model
    return TemplateTicketingProvider(), "template-k2-ticketing"


def get_agent_poa_provider(agent_id: str) -> tuple[ChatProvider, str]:
    return TemplatePoAProvider(), f"template-poa-{agent_id}"


def get_agent_reply_provider(agent_id: str) -> tuple[ChatProvider, str]:
    return TemplateReplyProvider(), f"template-reply-{agent_id}"
