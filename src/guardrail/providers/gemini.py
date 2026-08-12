"""Google Gemini generateContent API."""

from __future__ import annotations

from typing import Any

from guardrail.models import Message
from guardrail.providers.base import Provider, split_system

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(Provider):
    env_var = "GEMINI_API_KEY"

    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        system, rest = split_system(messages)

        body: dict[str, Any] = {
            "contents": [
                {
                    # Gemini calls the assistant role "model".
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [{"text": m.content}],
                }
                for m in rest
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        body.update(self.extra)

        headers = {
            "x-goog-api-key": self._require_key(),
            "content-type": "application/json",
        }
        return f"{BASE}/models/{self.model}:generateContent", headers, body

    def _parse_response(self, data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            # A safety block returns no candidates but does explain why.
            feedback = data.get("promptFeedback", {})
            blocked = feedback.get("blockReason")
            if blocked:
                return f"[BLOCKED_BY_PROVIDER: {blocked}]"
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)
