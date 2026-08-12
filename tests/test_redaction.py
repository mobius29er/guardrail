"""Redaction must never let a credential through into a report artifact."""

from __future__ import annotations

import pytest

from guardrail.report import redact

# Synthetic, structurally-valid-looking values. None of these are real keys.
SECRETS = [
    "sk-ant-api03-" + "A" * 40,
    "sk-proj-" + "B" * 40,
    "sk-" + "C" * 48,
    "AIza" + "D" * 35,
    "ghp_" + "E" * 36,
    "github_pat_" + "F" * 60,
    "xoxb-1234567890-abcdefghijkl",
    "AKIA" + "G" * 16,
    "Bearer " + "H" * 40,
]


@pytest.mark.parametrize("secret", SECRETS)
def test_secret_is_masked(secret):
    out = redact(f"error calling API with {secret} in the message")
    assert secret not in out
    assert "REDACTED" in out


@pytest.mark.parametrize(
    "text",
    [
        'api_key = "abcdef1234567890abcdef"',
        "token: ghijkl9876543210ghijkl99",
        'password="supersecretvalue12345678"',
        "SECRET = qwertyuiopasdfghjklzxcvb",
    ],
)
def test_assignment_forms_are_masked(text):
    out = redact(text)
    assert "REDACTED" in out


def test_private_key_block_is_masked():
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890\n-----END RSA PRIVATE KEY-----"
    )
    out = redact(f"config contains:\n{pem}\nend")
    assert "MIIEowIBAAKCAQEA" not in out
    assert "REDACTED:private-key" in out


def test_ordinary_text_survives():
    """Redaction must not mangle normal transcript content."""
    text = (
        "The Catechism of the Catholic Church (CCC 1866) identifies pride as a "
        "capital sin. Call 1-888-808-8724 and ask for a priest. "
        "See Genesis 3:5 and Hebrews 4:15."
    )
    assert redact(text) == text


def test_empty_string():
    assert redact("") == ""


def test_multiple_secrets_in_one_string():
    text = f"first {SECRETS[0]} then {SECRETS[3]} done"
    out = redact(text)
    assert SECRETS[0] not in out
    assert SECRETS[3] not in out


def test_redaction_is_applied_to_report_payload():
    """End-to-end: a leaked key inside a transcript must not reach the JSON."""
    from guardrail.models import Case, CaseGroup, CaseResult, Check, Message, RunResult
    from guardrail.report import to_dict

    secret = "sk-ant-api03-" + "Z" * 40
    case = Case(id="t", turns=["hi"], checks=[Check(kind="refuses")])
    result = CaseResult(
        case=case,
        transcript=[Message("assistant", f"my key is {secret}")],
        checks=[],
        error=f"auth failed for {secret}",
    )
    group = CaseGroup(case=case, runs=[result])
    payload = to_dict(RunResult(target_name="t", model="m", results=[group]))

    import json

    assert secret not in json.dumps(payload)


def test_redaction_covers_every_repeat_run():
    """With --repeat N, a secret in ANY run must be masked, not just the first."""
    from guardrail.models import Case, CaseGroup, CaseResult, Check, Message, RunResult
    from guardrail.report import to_dict, to_html, to_markdown

    secret = "sk-ant-api03-" + "Q" * 40
    case = Case(id="t", turns=["hi"], checks=[Check(kind="refuses")])
    runs = [
        CaseResult(case=case, transcript=[Message("assistant", "clean")], checks=[], run_index=0),
        CaseResult(
            case=case,
            transcript=[Message("assistant", f"leaked {secret}")],
            checks=[],
            error=f"auth failed for {secret}",
            run_index=1,
        ),
    ]
    run = RunResult(target_name="t", model="m", results=[CaseGroup(case=case, runs=runs)], repeat=2)

    import json

    assert secret not in json.dumps(to_dict(run))
    assert secret not in to_markdown(run)
    assert secret not in to_html(run)
