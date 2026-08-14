"""Shared test doubles.

This lives in its own module rather than being imported across test files.
``from tests.test_runner import ...`` only works when the repo root happens to
be on ``sys.path`` — true under ``python -m pytest``, false under the bare
``pytest`` console script that CI runs. pytest puts *this* directory on the
path for rootdir-relative test packages, so ``from helpers import ...`` works
under both.
"""

from __future__ import annotations

from halligan.models import Case, CaseResult, CheckResult, Message, Outcome
from halligan.providers.base import Provider, ProviderError


class FakeProvider(Provider):
    """Returns canned replies in order, and records what it was sent."""

    def __init__(self, replies: list[str], *, fail_on: int | None = None):
        super().__init__(model="fake")
        self.replies = replies
        self.calls: list[list[Message]] = []
        self.fail_on = fail_on

    def _build_request(self, messages):  # pragma: no cover - unused
        raise NotImplementedError

    def _parse_response(self, data):  # pragma: no cover - unused
        raise NotImplementedError

    async def complete(self, messages: list[Message]) -> str:
        self.calls.append(list(messages))
        if self.fail_on is not None and len(self.calls) - 1 == self.fail_on:
            raise ProviderError("simulated provider failure")
        idx = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[idx]

    async def aclose(self) -> None:
        return None


class Scripted(FakeProvider):
    """Returns one canned reply per turn, indexed by call order."""

    async def complete(self, messages: list[Message]) -> str:
        self.calls.append(list(messages))
        return self.replies[len(self.calls) - 1]


def make_run(case: Case, outcome: Outcome, index: int = 0) -> CaseResult:
    """A synthetic single run with the given outcome."""
    if outcome is Outcome.ERROR:
        return CaseResult(case=case, transcript=[], checks=[], error="boom", run_index=index)
    check = CheckResult(kind="refuses", outcome=outcome, reason="")
    return CaseResult(case=case, transcript=[], checks=[check], run_index=index)
