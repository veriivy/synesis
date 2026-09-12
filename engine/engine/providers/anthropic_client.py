"""Anthropic provider, via the official SDK's Messages API."""

from __future__ import annotations

import asyncio
import logging
import random

import anthropic

from .base import Completion, LLMClient, Message, ProviderError, ToolCall, ToolSpec

log = logging.getLogger(__name__)


class AnthropicClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 90.0,
        max_retries: int = 2,
        provider_key: str = "anthropic",
    ) -> None:
        self.provider_key = provider_key
        self.model = model
        self.max_retries = max_retries
        # The SDK retries 429/5xx/connection errors itself; our loop on top of it
        # covers the malformed-response and timeout cases the SDK re-raises.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    # --- translation ---------------------------------------------------------

    @staticmethod
    def _to_wire(messages: list[Message]) -> list[dict]:
        wire: list[dict] = []
        for m in messages:
            if m.tool_results:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": r.content,
                                **({"is_error": True} if r.is_error else {}),
                            }
                            for r in m.tool_results
                        ],
                    }
                )
                continue

            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for call in m.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            if blocks:
                wire.append({"role": m.role, "content": blocks})
        return wire

    @staticmethod
    def _tools_to_wire(tools: list[ToolSpec]) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]

    # --- interface -----------------------------------------------------------

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> Completion:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self._to_wire(messages),
        }
        if tools:
            kwargs["tools"] = self._tools_to_wire(tools)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.messages.create(**kwargs)
            except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
                # A bad model id or malformed request will never succeed on retry.
                raise ProviderError(self.provider_key, str(exc)) from exc
            except anthropic.AuthenticationError as exc:
                raise ProviderError(self.provider_key, "bad or missing API key") from exc
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                last_error = exc
                log.warning("%s attempt %d failed: %s", self.provider_key, attempt + 1, exc)
                await asyncio.sleep(min(2**attempt + random.uniform(0, 0.5), 10))
                continue

            text = "".join(b.text for b in response.content if b.type == "text")
            calls = [
                ToolCall(call_id=b.id, name=b.name, arguments=dict(b.input))
                for b in response.content
                if b.type == "tool_use"
            ]
            return Completion(
                text=text,
                tool_calls=calls,
                stop_reason=response.stop_reason or "",
                model=response.model,
                raw=response,
            )

        raise ProviderError(self.provider_key, f"failed after retries: {last_error}")

    async def list_models(self) -> list[str]:
        page = await self._client.models.list()
        return [m.id for m in page.data]
