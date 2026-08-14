"""Ollama — local models, usually with no credential at all."""

from __future__ import annotations

import os
from typing import Any

from halligan.models import Message
from halligan.providers.base import Provider


class OllamaProvider(Provider):
    env_var = None  # local by default

    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        extra = dict(self.extra)
        host = str(
            extra.pop("host", None) or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        )

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
                **extra.pop("options", {}),
            },
        }
        body.update(extra)

        return f"{host.rstrip('/')}/api/chat", {"content-type": "application/json"}, body

    def _parse_response(self, data: dict[str, Any]) -> str:
        return data.get("message", {}).get("content", "")
