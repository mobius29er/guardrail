# Contributing

Thanks for your interest. New probe families, providers, and graders are all
welcome — particularly probes for domains beyond this one.

## Setup

```bash
git clone https://github.com/mobius29er/halligan
cd halligan
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install          # catches secrets before they reach the remote
pytest -q                   # 86 tests, no network or API key required
```

## Before you open a PR

```bash
ruff check src tests && ruff format src tests
pytest -q
halligan validate --suite suites/
```

CI runs the same three on Python 3.10–3.13, plus a full-history gitleaks scan.

## Never commit a credential

This is the one hard rule.

- Keys come from the environment, never from a config file or CLI argument.
- `.env` is gitignored; `.env.example` holds empty assignments only.
- Anything written to a report goes through `halligan.report.redact` first —
  if you add a new output path, route it through that function.
- If you leak one: **revoke it at the provider first**, then scrub history.
  See [`SECURITY.md`](SECURITY.md).

## Adding a probe

Suites live in [`suites/`](suites/) as YAML. A good probe is:

- **Specific.** It fails for one identifiable reason.
- **Justified.** The `description` says what real failure it guards against.
  Probes derived from an observed session are the most valuable kind — say
  which turn it came from.
- **Correctly severity-tagged.** `critical` means real-world harm (an
  assistant claiming to absolve sin, missing a crisis signal), not "important
  to me."
- **Paired with its inverse where over-refusal is possible.** If you add a
  probe asserting a model refuses something, consider adding one asserting it
  *doesn't* refuse the legitimate neighbouring case. Over-blocking is a real
  failure — see `steelman-permitted` in `jailbreak_roleplay.yaml`.

Multi-turn cases are the point of this project. If a property can only degrade
over several turns, write it as several turns and assert with
`consistent_with`.

Run `halligan validate --suite suites/` when you're done; `tests/test_config.py`
also enforces globally unique case ids and in-range turn indices.

## Adding a grader

Add an async function to [`src/halligan/graders.py`](src/halligan/graders.py)
decorated with `@grader("your_kind")`:

```python
@grader("your_kind")
async def your_kind(response, transcript, params, ctx) -> tuple[bool, str]:
    """One-line summary — this shows up in `halligan graders`."""
    ...
    return ok, "why it passed or failed"
```

The reason string is read by a human staring at a CI failure, so make it say
what actually happened — quote the matched phrase, not just "check failed."

Keyword graders should be **narrow**. The first version of
`does_not_advocate` matched the bare phrase "as a Satanist", which fired on
the *refusal* text ("Roleplaying as a Satanist is not something that can be
done here") and silently disabled the drift detector downstream. A false pass
on a critical case is much worse than a false fail. Add a test with a real
refusal excerpt to prove your pattern doesn't fire on it.

Anything requiring judgement rather than matching belongs in a `judge` rubric
instead.

## Adding a provider

Subclass `Provider` in `src/halligan/providers/`, implement `_build_request`
and `_parse_response`, and register it in `PROVIDERS`. Retries, backoff,
`Retry-After` handling, and timeouts are handled by the base class.

Read credentials via `self._require_key()` so a missing key produces a helpful
message instead of a 401.

## Scope

In scope: probes, providers, graders, reporting, docs.

Out of scope: anything whose purpose is to make a model *produce* harmful
content rather than to test whether it refuses to. Probes are adversarial by
necessity; the goal is always measurement.

## Code of Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
