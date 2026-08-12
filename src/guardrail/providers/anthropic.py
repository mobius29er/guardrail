"""Anthropic Messages API."""

from __future__ import annotations

from typing import Any

from guardrail.models import Message
from guardrail.providers.base import Provider, split_system

API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    env_var = "ANTHROPIC_API_KEY"

    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        system, rest = split_system(messages)
        extra = dict(self.extra)
        base = str(extra.pop("base_url", None) or "https://api.anthropic.com")

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in rest],
        }
        if system:
            body["system"] = system
        body.update(extra)

        headers = {
            "x-api-key": self._require_key(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        return f"{base.rstrip('/')}/v1/messages", headers, body

    def _parse_response(self, data: dict[str, Any]) -> str:
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
