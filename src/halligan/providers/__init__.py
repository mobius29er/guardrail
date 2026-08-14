"""Built-in providers and the factory that constructs them."""

from __future__ import annotations

from typing import Any

from halligan.providers.anthropic import AnthropicProvider
from halligan.providers.base import (
    MissingCredentialError,
    Provider,
    ProviderError,
)
from halligan.providers.gemini import GeminiProvider
from halligan.providers.http import HTTPProvider
from halligan.providers.ollama import OllamaProvider
from halligan.providers.openai import OpenAIProvider

PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "http": HTTPProvider,
}

__all__ = [
    "PROVIDERS",
    "AnthropicProvider",
    "GeminiProvider",
    "HTTPProvider",
    "MissingCredentialError",
    "OllamaProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "build_provider",
]


def build_provider(spec: dict[str, Any]) -> Provider:
    """Construct a provider from a target-config ``provider`` block.

    ``spec`` must contain ``name`` and ``model``; everything else is forwarded
    to the provider constructor. Unknown keys land in ``extra``.
    """
    spec = dict(spec)
    name = str(spec.pop("name", "")).lower()
    if not name:
        raise ValueError("target config must set provider.name")
    if name not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider {name!r} (available: {available})")

    model = spec.pop("model", None)
    if not model:
        raise ValueError(f"target config must set provider.model for {name!r}")

    known = ("temperature", "max_tokens", "timeout", "max_retries")
    kwargs = {k: spec.pop(k) for k in known if k in spec}

    return PROVIDERS[name](model=str(model), extra=spec, **kwargs)
