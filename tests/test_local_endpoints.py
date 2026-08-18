"""Pointing at a local model must not demand a cloud credential.

The Getting Started page tells people to run a model in LM Studio and set
`base_url: http://127.0.0.1:1234/v1`. The provider then required
OPENAI_API_KEY regardless, so following our own documentation produced an
error about a credential the local server neither wants nor checks. It was not
a soft failure either: every case in the run errored, the score came back 0.0,
and the run finished in under a second looking superficially like it had done
something.

The line to hold is narrow. Together, Groq, OpenRouter and friends all speak
the same dialect through the same provider and all genuinely need a key — they
must keep failing loudly. Only an address that is unmistakably your own
machine or LAN is exempt.
"""

from __future__ import annotations

import pytest

from halligan.models import Message
from halligan.providers.base import MissingCredentialError
from halligan.providers.openai import OpenAIProvider, _is_self_hosted

MESSAGES = [Message(role="user", content="hi")]

SELF_HOSTED = [
    "http://127.0.0.1:1234/v1",
    "http://localhost:1234/v1",
    "http://0.0.0.0:8000/v1",
    "http://192.168.1.9:1234/v1",
    "http://10.0.0.4:8000/v1",
    "http://workstation.local:1234/v1",
]

NEEDS_A_KEY = [
    "https://api.openai.com/v1",
    "https://api.together.xyz/v1",
    "https://api.groq.com/openai/v1",
    "https://openrouter.ai/api/v1",
]


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """These tests are about the absence of a key, so remove any real one."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


@pytest.mark.parametrize("base", SELF_HOSTED)
def test_local_endpoint_needs_no_key(base):
    provider = OpenAIProvider("some-local-model", extra={"base_url": base})
    url, headers, _ = provider._build_request(MESSAGES)
    assert url.startswith(base.rstrip("/"))
    assert headers["authorization"].startswith("Bearer "), (
        "send the header anyway — some local servers parse it even though they do not validate it"
    )


@pytest.mark.parametrize("base", NEEDS_A_KEY)
def test_hosted_gateway_still_demands_a_key(base):
    provider = OpenAIProvider("some-model", extra={"base_url": base})
    with pytest.raises(MissingCredentialError):
        provider._build_request(MESSAGES)


def test_default_base_url_still_demands_a_key():
    """No base_url at all means api.openai.com, which is not self-hosted."""
    with pytest.raises(MissingCredentialError):
        OpenAIProvider("gpt-4o", extra={})._build_request(MESSAGES)


def test_a_real_key_is_used_for_local_gateways_too(monkeypatch):
    """Some self-hosted gateways do check a key; honour one when it is set."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-secret")
    provider = OpenAIProvider("m", extra={"base_url": "http://127.0.0.1:1234/v1"})
    _, headers, _ = provider._build_request(MESSAGES)
    assert headers["authorization"] == "Bearer sk-local-secret"


@pytest.mark.parametrize("base", SELF_HOSTED)
def test_classifier_accepts_local(base):
    assert _is_self_hosted(base)


@pytest.mark.parametrize("base", NEEDS_A_KEY)
def test_classifier_rejects_hosted(base):
    assert not _is_self_hosted(base)
