"""The run loop: drive each case's conversation, then grade it."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timezone

from halligan.config import TargetConfig
from halligan.graders import REGISTRY, GradeContext
from halligan.models import (
    Case,
    CaseGroup,
    CaseResult,
    CheckResult,
    Message,
    Outcome,
    RunResult,
    Suite,
)
from halligan.providers import build_provider
from halligan.providers.base import Provider, ProviderError

ProgressFn = Callable[[CaseGroup], None]


async def run_case(
    case: Case,
    provider: Provider,
    ctx: GradeContext,
    default_system: str | None = None,
    run_index: int = 0,
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
                run_index=run_index,
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

        # `turn: swept` follows the swept turn to wherever it landed in this
        # positional variant. On a non-sweep case it degrades to the last turn.
        turn_index = check.turn
        if check.follows_swept_turn:
            turn_index = case.sweep_position if case.sweep_position is not None else -1

        try:
            target = assistant_turns[turn_index]
        except IndexError:
            check_results.append(
                CheckResult(
                    kind=check.kind,
                    outcome=Outcome.ERROR,
                    reason=(
                        f"check targets turn {turn_index} but the case produced "
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
        run_index=run_index,
    )


def select_cases(
    suites: list[Suite],
    *,
    filter_family: str | None = None,
    filter_id: str | None = None,
    sweep: bool = False,
) -> list[Case]:
    """The cases a run will actually execute, after filtering and sweep expansion.

    Shared with the CLI so the banner and the progress counter cannot disagree
    with the runner. They did: both were computed from the unfiltered suites, so
    ``--case fiction-wrapper --repeat 8`` announced "6 case(s) ... (48 runs)"
    and then printed ``[1/6]`` while running exactly one case eight times.
    """
    cases: list[Case] = []
    for suite in suites:
        for case in suite.cases:
            if filter_family and case.family != filter_family:
                continue
            if filter_id and filter_id not in case.id:
                continue
            cases.extend(case.expand_sweep() if sweep else [case])
    return cases


async def run_suites(
    target: TargetConfig,
    suites: list[Suite],
    *,
    on_progress: ProgressFn | None = None,
    filter_family: str | None = None,
    filter_id: str | None = None,
    repeat: int = 1,
    flake_threshold: float = 0.0,
    sweep: bool = False,
) -> RunResult:
    """Run every case in ``suites`` against ``target``, bounded by concurrency.

    ``repeat`` executes each case that many times and grades it across the
    whole set. Repeats of the same case run concurrently alongside everything
    else — the semaphore bounds total in-flight requests, not cases.

    ``sweep`` expands cases declaring ``sweep_turn`` into one variant per
    position. It is off by default because it multiplies API cost by the number
    of positions; without it a sweep case runs once in its authored order.
    """
    if repeat < 1:
        raise ValueError("repeat must be >= 1")
    if not 0.0 <= flake_threshold <= 1.0:
        raise ValueError("flake_threshold must be between 0.0 and 1.0")

    cases = select_cases(suites, filter_family=filter_family, filter_id=filter_id, sweep=sweep)

    provider = build_provider(target.provider)
    judge = build_provider(target.judge) if target.judge else None
    ctx = GradeContext(judge=judge)

    semaphore = asyncio.Semaphore(target.concurrency)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    wall_start = time.monotonic()

    async def one_run(case: Case, index: int) -> CaseResult:
        async with semaphore:
            return await run_case(case, provider, ctx, target.system, run_index=index)

    async def group_for(case: Case) -> CaseGroup:
        runs = await asyncio.gather(*(one_run(case, i) for i in range(repeat)))
        group = CaseGroup(case=case, runs=list(runs), flake_threshold=flake_threshold)
        if on_progress:
            on_progress(group)
        return group

    try:
        results = await asyncio.gather(*(group_for(c) for c in cases))
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
        repeat=repeat,
        sweep=sweep,
    )
