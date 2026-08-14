"""OpenAI Chat Completions, and any API that speaks the same dialect.

Set ``base_url`` in the provider block to point at Together, Groq, vLLM,
LM Studio, OpenRouter, or another compatible gateway.
"""

from __future__ import annotations

import os
from typing import Any

from halligan.models import Message
from halligan.providers.base import Provider


class OpenAIProvider(Provider):
    env_var = "OPENAI_API_KEY"

    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_completion_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        extra = dict(self.extra)
        base = str(
            extra.pop("base_url", None)
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        body.update(extra)

        headers = {
            "authorization": f"Bearer {self._require_key()}",
            "content-type": "application/json",
        }
        return f"{base.rstrip('/')}/chat/completions", headers, body

    def _parse_response(self, data: dict[str, Any]) -> str:
        return data["choices"][0]["message"].get("content") or ""
