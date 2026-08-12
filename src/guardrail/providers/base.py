"""Provider abstraction.

A provider turns a list of :class:`~guardrail.models.Message` into an assistant
reply. Adding support for a new backend means subclassing :class:`Provider` and
registering it in :func:`guardrail.providers.build_provider`.

Providers read credentials from the environment **only** — never from the run
config, and never from the command line. See ``SECURITY.md``.
"""

from __future__ import annotations

import abc
import asyncio
import os
import random
from typing import Any

import httpx

from guardrail.models import Message


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a response."""


class MissingCredentialError(ProviderError):
    def __init__(self, env_var: str, provider: str) -> None:
        super().__init__(
            f"{provider} requires the {env_var} environment variable. "
            f"Copy .env.example to .env and set {env_var}, or export it in your shell."
        )
        self.env_var = env_var


# Status codes worth retrying: rate limits, and transient server-side faults.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class Provider(abc.ABC):
    """Base class for chat backends."""

    #: Environment variable holding this provider's key, if it needs one.
    env_var: str | None = None

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 120.0,
        max_retries: int = 4,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra = extra or {}
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return type(self).__name__.removesuffix("Provider").lower()

    def _require_key(self) -> str:
        assert self.env_var is not None
        key = os.environ.get(self.env_var, "").strip()
        if not key:
            raise MissingCredentialError(self.env_var, type(self).__name__)
        return key

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Provider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- subclass API ------------------------------------------------------

    @abc.abstractmethod
    def _build_request(self, messages: list[Message]) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return ``(url, headers, json_body)`` for this turn."""

    @abc.abstractmethod
    def _parse_response(self, data: dict[str, Any]) -> str:
        """Pull the assistant text out of a successful response body."""

    # -- driver ------------------------------------------------------------

    async def complete(self, messages: list[Message]) -> str:
        """Send ``messages`` and return the assistant's reply.

        Retries retryable HTTP statuses and transport errors with exponential
        backoff plus jitter, honouring ``Retry-After`` when the server sends it.
        """
        url, headers, body = self._build_request(messages)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(url, headers=headers, json=body)
            except httpx.TransportError as exc:  # network blip, DNS, timeout
                last_error = exc
            else:
                if response.status_code < 400:
                    try:
                        return self._parse_response(response.json())
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise ProviderError(
                            f"{self.name}: could not parse response: {exc}"
                        ) from exc

                if response.status_code not in RETRYABLE_STATUS:
                    raise ProviderError(
                        f"{self.name}: HTTP {response.status_code}: {response.text[:400]}"
                    )
                last_error = ProviderError(
                    f"{self.name}: HTTP {response.status_code}: {response.text[:200]}"
                )
                retry_after = _parse_retry_after(response)
                if retry_after is not None and attempt < self.max_retries:
                    await asyncio.sleep(min(retry_after, 60.0))
                    continue

            if attempt < self.max_retries:
                backoff = min(2.0**attempt, 30.0) * (0.5 + random.random())
                await asyncio.sleep(backoff)

        raise ProviderError(
            f"{self.name}: giving up after {self.max_retries + 1} attempts: {last_error}"
        )


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form; fall back to exponential backoff


def split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
    """Split a leading system message out, for APIs that take it separately."""
    system_parts = [m.content for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]
    return ("\n\n".join(system_parts) or None), rest
