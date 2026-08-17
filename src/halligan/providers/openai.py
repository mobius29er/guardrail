"""OpenAI Chat Completions, and any API that speaks the same dialect.

Set ``base_url`` in the provider block to point at Together, Groq, vLLM,
LM Studio, OpenRouter, or another compatible gateway.
"""

from __future__ import annotations

import os
from typing import Any

from halligan.models import Message
from halligan.providers.base import Provider, ProviderError


class OpenAIProvider(Provider):
    env_var = "OPENAI_API_KEY"

    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        extra = dict(self.extra)
        base = str(
            extra.pop("base_url", None)
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )

        # `max_completion_tokens` is OpenAI's newer name and the only one their
        # reasoning models accept. Compatible servers largely have not followed:
        # LM Studio accepts the field, silently ignores it, and falls back to a
        # small default — which on a reasoning model is consumed entirely by
        # thinking, returning an empty string with finish_reason "length".
        #
        # Halligan then scored that empty response as a failed refusal. Hours of
        # "the model returned nothing" traced to a parameter name.
        #
        # So: the canonical name for OpenAI, the universally supported one for
        # everyone else. Override with `token_param` if a gateway disagrees.
        token_param = str(
            extra.pop(
                "token_param",
                "max_completion_tokens" if "api.openai.com" in base else "max_tokens",
            )
        )

        body: dict[str, Any] = {
            "model": self.model,
            token_param: self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        body.update(extra)

        headers = {
            "authorization": f"Bearer {self._require_key()}",
            "content-type": "application/json",
        }
        return f"{base.rstrip('/')}/chat/completions", headers, body

    def _parse_response(self, data: dict[str, Any]) -> str:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if content:
            return content

        # Empty content is almost never the model declining — it is the budget
        # running out before it wrote anything. Reasoning models spend tokens
        # thinking first, and that thinking is billed against the same limit:
        # observed here at 4095 reasoning tokens of a 4096 budget, content "".
        #
        # Halligan scored that as `does_not_refuse: empty response` — a failed
        # check. It is a configuration problem, and saying so plainly costs a
        # user hours less than a silent wrong verdict does.
        if choice.get("finish_reason") == "length":
            usage = (data.get("usage") or {}).get("completion_tokens_details") or {}
            reasoning = usage.get("reasoning_tokens")
            detail = (
                f" The model spent {reasoning} tokens reasoning before writing any"
                " answer, which counts against the same limit."
                if reasoning
                else ""
            )
            raise ProviderError(
                f"response truncated before any content was produced (finish_reason="
                f"'length', max_tokens={self.max_tokens})."
                f"{detail} Raise max_tokens, or use a non-reasoning model."
            )
        return ""
