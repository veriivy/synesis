"""One client for every provider that speaks OpenAI's chat completions API.

That is OpenAI itself, xAI/Grok, IFM/K2, and Gemini's compatibility endpoint — four of
our five providers, differing only by `base_url` and key. This is the file that makes
"adding a provider is a config change" true rather than aspirational.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import openai

from .base import Completion, LLMClient, Message, ProviderError, ToolCall, ToolSpec

log = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        self.provider_key = provider_key
        self.model = model
        self.max_retries = max_retries
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    # --- translation ---------------------------------------------------------

    @staticmethod
    def _to_wire(system: str, messages: list[Message]) -> list[dict]:
        wire: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.tool_results:
                # OpenAI wants one message per tool result, not one bundle.
                for r in m.tool_results:
                    wire.append(
                        {"role": "tool", "tool_call_id": r.call_id, "content": r.content}
                    )
                continue

            if m.tool_calls:
                wire.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": c.call_id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": json.dumps(c.arguments),
                                },
                            }
                            for c in m.tool_calls
                        ],
                    }
                )
                continue

            wire.append({"role": m.role, "content": m.content})
        return wire

    @staticmethod
    def _tools_to_wire(tools: list[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
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
            "max_completion_tokens": max_tokens,
            "messages": self._to_wire(system, messages),
        }
        if tools:
            kwargs["tools"] = self._tools_to_wire(tools)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.chat.completions.create(**kwargs)
            except openai.BadRequestError as exc:
                # Some gateways reject `max_completion_tokens` and want the older
                # `max_tokens`. Swap once and retry rather than losing the agent.
                if "max_completion_tokens" in str(exc) and "max_tokens" not in kwargs:
                    kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                    continue
                raise ProviderError(self.provider_key, str(exc)) from exc
            except openai.AuthenticationError as exc:
                raise ProviderError(self.provider_key, "bad or missing API key") from exc
            except openai.NotFoundError as exc:
                raise ProviderError(
                    self.provider_key, f"model {self.model!r} not reachable: {exc}"
                ) from exc
            except (openai.APIStatusError, openai.APIConnectionError) as exc:
                last_error = exc
                log.warning("%s attempt %d failed: %s", self.provider_key, attempt + 1, exc)
                await asyncio.sleep(min(2**attempt + random.uniform(0, 0.5), 10))
                continue

            choice = response.choices[0]
            calls: list[ToolCall] = []
            for raw_call in choice.message.tool_calls or []:
                # Model output is untrusted input. A tool call with unparseable
                # arguments becomes an empty-argument call, which the sandbox will
                # reject and report — it must not take the negotiation down.
                try:
                    args = json.loads(raw_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    log.warning(
                        "%s emitted unparseable tool arguments: %r",
                        self.provider_key,
                        raw_call.function.arguments,
                    )
                    args = {}
                calls.append(
                    ToolCall(
                        call_id=raw_call.id,
                        name=raw_call.function.name,
                        arguments=args if isinstance(args, dict) else {},
                    )
                )

            return Completion(
                text=choice.message.content or "",
                tool_calls=calls,
                stop_reason=choice.finish_reason or "",
                model=response.model,
                raw=response,
            )

        raise ProviderError(self.provider_key, f"failed after retries: {last_error}")

    async def list_models(self) -> list[str]:
        page = await self._client.models.list()
        return [m.id for m in page.data]
