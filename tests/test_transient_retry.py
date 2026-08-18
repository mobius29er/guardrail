"""A local server evicting its model must not end the run.

LM Studio answers ``400 {"error":"Model unloaded."}`` when it reclaims memory
or an idle TTL expires, then reloads the model on the next request. 400 is
normally a client error and retrying one is wrong, so the driver refused —
and a transient two-second gap became a permanent case failure.

It was not cheap. Across three multi-hour experiment runs this produced 10
errored runs of 24 in one arm and 12 of 24 in another, wasting roughly two
hours of GPU time and leaving two of three arms unusable for comparison.

The exemption is deliberately narrow: only a 400 whose *body* says the model
is missing. A malformed request still fails immediately, which is what a 400
almost always means.
"""

from __future__ import annotations

import pytest

from halligan.providers.base import RETRYABLE_STATUS, _is_retryable

# Verbatim from the failed runs, plus the neighbouring phrasings.
TRANSIENT_BODIES = [
    '{"error":"Model unloaded."}',
    '{"error": "Model not loaded"}',
    '{"error":"No model loaded"}',
    '{"error":"The model is loading, please wait"}',
    '{"error":"loading the model"}',
]

REAL_CLIENT_ERRORS = [
    '{"error":{"message":"Invalid value for temperature","type":"invalid_request_error"}}',
    '{"error":{"message":"model `nope` does not exist","type":"invalid_request_error"}}',
    '{"error":{"message":"Unsupported parameter: max_completion_tokens"}}',
    '{"error":"context length exceeded"}',
]


@pytest.mark.parametrize("body", TRANSIENT_BODIES)
def test_evicted_model_is_retryable(body):
    assert _is_retryable(400, body), (
        f"{body} is the server having dropped the model, not a bad request. "
        f"Retrying reloads it; refusing wastes the whole run."
    )


@pytest.mark.parametrize("body", REAL_CLIENT_ERRORS)
def test_genuine_client_errors_still_fail_fast(body):
    assert not _is_retryable(400, body), (
        f"{body} will fail identically on every attempt. Retrying it just "
        f"multiplies the delay before the user sees the real problem."
    )


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
def test_existing_retryable_statuses_are_untouched(status):
    assert _is_retryable(status, "anything")


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_other_client_errors_never_retry(status):
    assert not _is_retryable(status, '{"error":"Model unloaded."}'), (
        "the body exemption applies to 400 only — a 401 with any body is a "
        "credential problem that retrying cannot fix"
    )
