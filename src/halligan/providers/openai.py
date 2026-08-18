"""OpenAI Chat Completions, and any API that speaks the same dialect.

Set ``base_url`` in the provider block to point at Together, Groq, vLLM,
LM Studio, OpenRouter, or another compatible gateway.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from halligan.models import Message
from halligan.providers.base import Provider, ProviderError

#: Hosts that serve a model off your own machine or LAN and have no key to give.
#: Deliberately narrow — Together, Groq and OpenRouter all speak this dialect and
#: all genuinely require a key, so they must keep failing loudly without one.
_SELF_HOSTED_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


def _is_self_hosted(base_url: str) -> bool:
    host = urlparse(base_url).hostname or ""
    return (
        host in _SELF_HOSTED_HOSTS
        or host.endswith(".local")
        or host.startswith(("192.168.", "10."))
    )


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

        # A self-hosted endpoint has no key to demand. LM Studio, Ollama and
        # vLLM ignore the header entirely, and the docs tell people to point
        # `base_url` at localhost — so requiring OPENAI_API_KEY there turned the
        # documented local-model workflow into an error about a credential the
        # server never wanted. Every case in a run errored out on it.
        #
        # If a self-hosted gateway does want a key, setting the env var still
        # sends it; this only stops us insisting on one nobody can supply.
        key = os.environ.get(self.env_var or "", "").strip()
        if not key and not _is_self_hosted(base):
            key = self._require_key()  # raises, with the copy-.env.example hint
        headers = {
            "authorization": f"Bearer {key or 'not-needed'}",
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
