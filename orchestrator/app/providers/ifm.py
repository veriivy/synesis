"""Real K2 calls through IFM's OpenAI-compatible chat API.

Same pattern as the root k2mod.py proof-of-concept, wrapped to satisfy
app.jsonx.ChatProvider so it's a drop-in for the template providers once
IFM_API_KEY / IFM_BASE_URL / IFM_MODEL are set (see app/config.py). Only
constructed when a key is present — see config.get_k2_provider(). Real
Claude/Gemini/GPT clients for the per-user agents are out of scope here
(CLAUDE.md's provider abstraction is the seam for whoever adds those).
"""

from __future__ import annotations

from openai import AsyncOpenAI


class IFMChatProvider:
    def __init__(self, *, base_url: str, default_api_key: str, timeout: float = 90.0):
        self._base_url = base_url
        self._default_api_key = default_api_key
        self._timeout = timeout

    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        system: str,
        model: str,
        api_key: str | None = None,
    ) -> str:
        client = AsyncOpenAI(
            api_key=api_key or self._default_api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,  # app.jsonx already retries once at a higher level
        )
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
        )
        return resp.choices[0].message.content or ""
