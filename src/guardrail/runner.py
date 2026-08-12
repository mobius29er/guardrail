"""The run loop: drive each case's conversation, then grade it."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timezone

from guardrail.config import TargetConfig
from guardrail.graders import REGISTRY, GradeContext
from guardrail.models import (
    Case,
    CaseResult,
    CheckResult,
    Message,
    Outcome,
    RunResult,
    Suite,
)
from guardrail.providers import build_provider
from guardrail.providers.base import Provider, ProviderError

ProgressFn = Callable[[CaseResult], None]


async def run_case(
    case: Case,
    provider: Provider,
    ctx: GradeContext,
    default_system: str | None = None,
) -> CaseResult:
    """Play one case's conversation to completion, then apply its checks.

    Every user turn is appended to the running conversation, so a multi-turn
    case exercises the model's memory of its own earlier answers — which is the
    only way position drift becomes visible.
    """
    started = time.monotonic()
    system = case.system if case.system is not None else default_system
    transcript: list[Message] = []
    if system:
        transcript.append(Message(role="system", content=system))

    for user_turn in case.turns:
        transcript.append(Message(role="user", content=user_turn))
        try:
            reply = await provider.complete(transcript)
        except ProviderError as exc:
            return CaseResult(
                case=case,
                transcript=transcript,
                checks=[],
                error=str(exc),
                latency_s=round(time.monotonic() - started, 2),
            )
        transcript.append(Message(role="assistant", content=reply))

    assistant_turns = [m.content for m in transcript if m.role == "assistant"]
    check_results: list[CheckResult] = []

    for check in case.checks:
        fn = REGISTRY.get(check.kind)
        if fn is None:
            check_results.append(
                CheckResult(
                    kind=check.kind,
                    outcome=Outcome.ERROR,
                    reason=(
                        f"unknown grader {check.kind!r}; available: {', '.join(sorted(REGISTRY))}"
                    ),
                    description=check.description,
                )
            )
            continue

        try:
            target = assistant_turns[check.turn]
        except IndexError:
            check_results.append(
                CheckResult(
                    kind=check.kind,
                    outcome=Outcome.ERROR,
                    reason=(
                        f"check targets turn {check.turn} but the case produced "
                        f"{len(assistant_turns)} assistant turn(s)"
                    ),
                    description=check.description,
                )
            )
            continue

        try:
            ok, reason = await fn(target, transcript, check.params, ctx)
        except Exception as exc:  # a grader bug must not abort the run
            ok, reason = False, f"grader raised {type(exc).__name__}: {exc}"

        check_results.append(
            CheckResult(
                kind=check.kind,
                outcome=Outcome.PASS if ok else Outcome.FAIL,
                reason=reason,
                description=check.description,
            )
        )

    return CaseResult(
        case=case,
        transcript=transcript,
        checks=check_results,
        latency_s=round(time.monotonic() - started, 2),
    )


async def run_suites(
    target: TargetConfig,
    suites: list[Suite],
    *,
    on_progress: ProgressFn | None = None,
    filter_family: str | None = None,
    filter_id: str | None = None,
) -> RunResult:
    """Run every case in ``suites`` against ``target``, bounded by concurrency."""
    cases: list[Case] = []
    for suite in suites:
        for case in suite.cases:
            if filter_family and case.family != filter_family:
                continue
            if filter_id and filter_id not in case.id:
                continue
            cases.append(case)

    provider = build_provider(target.provider)
    judge = build_provider(target.judge) if target.judge else None
    ctx = GradeContext(judge=judge)

    semaphore = asyncio.Semaphore(target.concurrency)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    wall_start = time.monotonic()

    async def guarded(case: Case) -> CaseResult:
        async with semaphore:
            result = await run_case(case, provider, ctx, target.system)
            if on_progress:
                on_progress(result)
            return result

    try:
        results = await asyncio.gather(*(guarded(c) for c in cases))
    finally:
        await provider.aclose()
        if judge is not None:
            await judge.aclose()

    return RunResult(
        target_name=target.name,
        model=target.model,
        results=list(results),
        started_at=started_at,
        duration_s=round(time.monotonic() - wall_start, 2),
    )
