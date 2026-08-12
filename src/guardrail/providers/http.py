"""Generic HTTP provider — for testing your own deployed assistant.

Use this when the thing under test is your own app rather than a raw model API.
Configure the request and response shape declaratively in the target config::

    provider:
      name: http
      model: truthly-prod            # label only; sent as {{model}}
      url: https://your-app/api/chat
      # Where to put the conversation in the request body. The literal strings
      # {{messages}}, {{system}}, {{model}}, and {{last_user}} are substituted.
      body:
        conversation: "{{messages}}"
        system_prompt: "{{system}}"
      # Dotted path to the reply text in the response JSON.
      response_path: data.reply
      # Optional: extra headers. Use {{token}} for GUARDRAIL_HTTP_TOKEN.
      headers:
        authorization: "Bearer {{token}}"

Credentials come from ``GUARDRAIL_HTTP_TOKEN``; never put a literal token in
the config file.
"""

from __future__ import annotations

import os
from typing import Any

from guardrail.models import Message
from guardrail.providers.base import Provider, ProviderError, split_system

DEFAULT_BODY: dict[str, Any] = {
    "model": "{{model}}",
    "messages": "{{messages}}",
}


class HTTPProvider(Provider):
    env_var = None  # token is optional

    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        extra = dict(self.extra)

        url = extra.pop("url", None) or os.environ.get("GUARDRAIL_HTTP_URL")
        if not url:
            raise ProviderError(
                "http provider requires 'url' in the provider block, "
                "or the GUARDRAIL_HTTP_URL environment variable"
            )

        system, rest = split_system(messages)
        token = os.environ.get("GUARDRAIL_HTTP_TOKEN", "")

        subs: dict[str, Any] = {
            "{{messages}}": [{"role": m.role, "content": m.content} for m in rest],
            "{{system}}": system or "",
            "{{model}}": self.model,
            "{{last_user}}": next((m.content for m in reversed(rest) if m.role == "user"), ""),
            "{{token}}": token,
        }

        template = extra.pop("body", None) or DEFAULT_BODY
        body = _substitute(template, subs)
        if not isinstance(body, dict):
            raise ProviderError("http provider: 'body' must be a mapping")

        headers = {"content-type": "application/json"}
        for key, value in (extra.pop("headers", None) or {}).items():
            headers[str(key).lower()] = str(_substitute(value, subs))
        if token and "authorization" not in headers:
            headers["authorization"] = f"Bearer {token}"

        self._response_path = str(extra.pop("response_path", "") or "")
        return str(url), headers, body

    def _parse_response(self, data: dict[str, Any]) -> str:
        path = getattr(self, "_response_path", "")
        if path:
            node: Any = data
            for part in path.split("."):
                # A numeric segment indexes a list, e.g. "choices.0.message".
                node = node[int(part)] if isinstance(node, list) else node[part]
            return str(node)

        # No path configured — try the common shapes before giving up.
        for guess in ("reply", "response", "content", "text", "message", "output"):
            value = data.get(guess)
            if isinstance(value, str):
                return value
            if isinstance(value, dict) and isinstance(value.get("content"), str):
                return value["content"]

        try:  # OpenAI-compatible
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            pass

        raise ProviderError(
            "http provider: could not locate reply text in the response. "
            "Set 'response_path' in the provider block (e.g. response_path: data.reply). "
            f"Top-level keys were: {sorted(data)[:10]}"
        )


def _substitute(node: Any, subs: dict[str, Any]) -> Any:
    """Recursively replace ``{{placeholder}}`` markers in a config template.

    A string that is *exactly* a placeholder is replaced by the substitution's
    real type (so ``"{{messages}}"`` becomes a list, not its repr); a
    placeholder embedded in a longer string is interpolated as text.
    """
    if isinstance(node, str):
        if node in subs:
            return subs[node]
        for key, value in subs.items():
            if key in node and isinstance(value, str):
                node = node.replace(key, value)
        return node
    if isinstance(node, dict):
        return {k: _substitute(v, subs) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, subs) for v in node]
    return node
