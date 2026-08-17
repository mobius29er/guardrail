"""Core data types for Halligan.

Everything the runner passes around is defined here so the provider, grader,
and report layers can stay independent of one another.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any


class Severity(enum.Enum):
    """How much a failure of a given case matters.

    ``CRITICAL`` failures are the ones that cause real-world harm — an
    assistant claiming to absolve sin, for example. ``LOW`` failures are
    stylistic.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {"critical": 8, "high": 4, "medium": 2, "low": 1}[self.value]


class Outcome(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


# Sentinel for `turn: swept` — grade the response to the swept turn, wherever
# that turn landed in this positional variant. Without it, a check targeting a
# fixed index would grade a different prompt's response at every position, which
# is exactly the silent-garbage failure a sweep is supposed to avoid.
SWEPT = -1000


@dataclass
class Check:
    """A single assertion applied to a case's transcript.

    ``kind`` names a grader registered in :mod:`halligan.graders`. ``params``
    is passed through to it verbatim. ``turn`` selects which assistant response
    to grade: an index (0-based), -1 for the last one (the default), or the
    literal ``swept`` to follow the swept turn in a position sweep.
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    turn: int = -1
    description: str = ""

    @property
    def follows_swept_turn(self) -> bool:
        return self.turn == SWEPT

    @classmethod
    def from_yaml(cls, raw: dict[str, Any]) -> Check:
        data = dict(raw)
        kind = data.pop("kind", None)
        if not kind:
            raise ValueError(f"check is missing required key 'kind': {raw!r}")
        turn = data.pop("turn", -1)
        if isinstance(turn, str):
            if turn.strip().lower() != "swept":
                raise ValueError(
                    f"check 'turn' must be an integer or the literal 'swept', got {turn!r}"
                )
            turn = SWEPT
        description = data.pop("description", "")
        return cls(kind=kind, params=data, turn=int(turn), description=description)


@dataclass
class Case:
    """One test: a conversation to run, plus the checks to apply to it.

    ``turns`` is a list of user messages sent in sequence, each appended to the
    growing conversation. A single-turn probe is just a one-element list.
    """

    id: str
    turns: list[str]
    checks: list[Check]
    family: str = "general"
    severity: Severity = Severity.MEDIUM
    description: str = ""
    system: str | None = None
    tags: list[str] = field(default_factory=list)

    #: Index of the turn whose position is under test. Set by `sweep_turn:` in
    #: YAML. Only expanded when the run opts in with --sweep.
    sweep_turn: int | None = None
    #: On an expanded variant: where the swept turn landed (0-based).
    sweep_position: int | None = None
    #: On an expanded variant: how many positions the sweep covers.
    sweep_total: int | None = None
    #: On an expanded variant: the id of the case it was expanded from.
    sweep_base: str | None = None

    @property
    def is_sweep_variant(self) -> bool:
        return self.sweep_position is not None

    def expand_sweep(self) -> list[Case]:
        """Slide the swept turn through every position, holding the rest fixed.

        This is the designed alternative to random shuffling. Randomizing order
        confounds position with neighborhood — a turn at index 3 has different
        predecessors on every draw, so "position 3 is dangerous" cannot be
        separated from "that particular preceding turn is dangerous". Moving one
        turn through a fixed sequence isolates position cleanly, and N positions
        beats sampling N! permutations.

        Returns ``[self]`` unchanged when no sweep is declared.
        """
        if self.sweep_turn is None:
            return [self]

        swept = self.turns[self.sweep_turn]
        others = [t for i, t in enumerate(self.turns) if i != self.sweep_turn]
        total = len(self.turns)

        return [
            replace(
                self,
                id=f"{self.id}@pos{pos + 1}",
                turns=others[:pos] + [swept] + others[pos:],
                sweep_turn=None,  # expanded variants must not re-expand
                sweep_position=pos,
                sweep_total=total,
                sweep_base=self.id,
            )
            for pos in range(total)
        ]

    @classmethod
    def from_yaml(cls, raw: dict[str, Any]) -> Case:
        if "id" not in raw:
            raise ValueError(f"case is missing required key 'id': {raw!r}")

        turns = raw.get("turns")
        if turns is None:
            prompt = raw.get("prompt")
            if prompt is None:
                raise ValueError(f"case {raw['id']!r} must define either 'turns' or 'prompt'")
            turns = [prompt]
        if isinstance(turns, str):
            turns = [turns]
        if not turns:
            raise ValueError(f"case {raw['id']!r} has no turns")

        checks = [Check.from_yaml(c) for c in raw.get("checks", [])]
        if not checks:
            raise ValueError(f"case {raw['id']!r} has no checks")

        severity_raw = str(raw.get("severity", "medium")).lower()
        try:
            severity = Severity(severity_raw)
        except ValueError:
            valid = ", ".join(s.value for s in Severity)
            raise ValueError(
                f"case {raw['id']!r} has unknown severity {severity_raw!r} "
                f"(expected one of: {valid})"
            ) from None

        sweep_turn = raw.get("sweep_turn")
        if sweep_turn is not None:
            sweep_turn = int(sweep_turn)
            if not 0 <= sweep_turn < len(turns):
                raise ValueError(
                    f"case {raw['id']!r} sets sweep_turn={sweep_turn} but has {len(turns)} turn(s)"
                )
            if len(turns) < 2:
                raise ValueError(
                    f"case {raw['id']!r} declares a sweep but has only one turn — "
                    "there is nothing to sweep across"
                )
            # `consistent_with` compares against a fixed baseline index. Under a
            # sweep that index holds a different prompt at every position, so the
            # grader would keep reporting DRIFT/HELD against a moving target — no
            # error, just meaningless numbers. Reject it rather than average it.
            offenders = [c.kind for c in checks if c.kind == "consistent_with"]
            if offenders:
                raise ValueError(
                    f"case {raw['id']!r} cannot combine sweep_turn with "
                    f"'consistent_with': the baseline turn moves between positions, "
                    f"so the comparison would be meaningless. Use 'turn: swept' with "
                    f"a positional grader instead, or split the sweep into its own case."
                )

        return cls(
            id=str(raw["id"]),
            turns=[str(t) for t in turns],
            checks=checks,
            family=str(raw.get("family", "general")),
            severity=severity,
            description=str(raw.get("description", "")),
            system=raw.get("system"),
            tags=[str(t) for t in raw.get("tags", [])],
            sweep_turn=sweep_turn,
        )


@dataclass
class Suite:
    name: str
    cases: list[Case]
    description: str = ""
    system: str | None = None
    path: str = ""

    @classmethod
    def from_yaml(cls, raw: dict[str, Any], path: str = "") -> Suite:
        cases = [Case.from_yaml(c) for c in raw.get("cases", [])]

        seen: set[str] = set()
        for case in cases:
            if case.id in seen:
                raise ValueError(
                    f"duplicate case id {case.id!r} in suite {path or raw.get('name')}"
                )
            seen.add(case.id)

        suite_system = raw.get("system")
        if suite_system:
            for case in cases:
                if case.system is None:
                    case.system = suite_system

        return cls(
            name=str(raw.get("name", "unnamed")),
            cases=cases,
            description=str(raw.get("description", "")),
            system=suite_system,
            path=path,
        )


@dataclass
class CheckResult:
    kind: str
    outcome: Outcome
    reason: str
    description: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass
class CaseResult:
    """The result of executing a case exactly once."""

    case: Case
    transcript: list[Message]
    checks: list[CheckResult]
    error: str | None = None
    latency_s: float = 0.0
    run_index: int = 0

    @property
    def outcome(self) -> Outcome:
        if self.error:
            return Outcome.ERROR
        if not self.checks:
            return Outcome.SKIP
        # A check that could not run has produced no verdict. Treating "not
        # graded" as "failed" is how a judge outage becomes a safety finding,
        # so an ungradable check errors the whole run rather than failing it —
        # the same rule already applied to a provider outage on the target.
        if any(c.outcome is Outcome.ERROR for c in self.checks):
            return Outcome.ERROR
        return Outcome.PASS if all(c.ok for c in self.checks) else Outcome.FAIL

    @property
    def assistant_turns(self) -> list[str]:
        return [m.content for m in self.transcript if m.role == "assistant"]

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]


@dataclass
class CaseGroup:
    """Every execution of one case, graded as a set.

    With ``--repeat 1`` this holds a single run and behaves like it. With
    ``--repeat N`` it holds N, and the case is graded across the whole set —
    because a guardrail that holds nine times and moves once has moved. That
    is the entire point of repeating: a single run reports a coin flip as
    though it were a measurement.

    ``flake_threshold`` is the fraction of failing runs tolerated before the
    case is marked failed. The default of 0.0 means any failure fails.
    """

    case: Case
    runs: list[CaseResult]
    flake_threshold: float = 0.0

    @property
    def graded(self) -> list[CaseResult]:
        """Runs that produced a real verdict — infrastructure errors excluded."""
        return [r for r in self.runs if r.outcome in (Outcome.PASS, Outcome.FAIL)]

    @property
    def failures(self) -> list[CaseResult]:
        return [r for r in self.runs if r.outcome is Outcome.FAIL]

    @property
    def errors(self) -> list[CaseResult]:
        return [r for r in self.runs if r.outcome is Outcome.ERROR]

    @property
    def outcome(self) -> Outcome:
        """A network blip is not model behavior.

        Errored runs only decide the outcome when nothing gradable survived;
        otherwise the case is judged on the runs that actually completed.
        """
        if not self.graded:
            if self.errors:
                return Outcome.ERROR
            return Outcome.SKIP
        return Outcome.FAIL if self.flake_rate > self.flake_threshold else Outcome.PASS

    @property
    def flake_rate(self) -> float:
        """Fraction of gradable runs that failed, 0.0–1.0."""
        if not self.graded:
            return 0.0
        return len(self.failures) / len(self.graded)

    @property
    def is_flaky(self) -> bool:
        """True when the case both passed and failed across its repeats.

        This is the interesting state: not "the guardrail holds" or "the
        guardrail fails", but "the guardrail is a coin flip" — which a
        single run can never distinguish from either.
        """
        return 0 < len(self.failures) < len(self.graded)

    @property
    def representative(self) -> CaseResult | None:
        """The run worth showing in a report — a failure if one exists."""
        if self.failures:
            return self.failures[0]
        if self.errors and not self.graded:
            return self.errors[0]
        return self.runs[0] if self.runs else None

    @property
    def latency_s(self) -> float:
        """Mean latency across runs."""
        if not self.runs:
            return 0.0
        return round(sum(r.latency_s for r in self.runs) / len(self.runs), 2)


@dataclass
class RunResult:
    target_name: str
    model: str
    results: list[CaseGroup]
    started_at: str = ""
    duration_s: float = 0.0
    repeat: int = 1
    sweep: bool = False

    @property
    def sweeps(self) -> dict[str, list[CaseGroup]]:
        """Sweep variants grouped by the case they were expanded from.

        Each list is ordered by position, so the flake rate across it reads
        directly as a function of where the swept turn landed.
        """
        grouped: dict[str, list[CaseGroup]] = {}
        for group in self.results:
            base = group.case.sweep_base
            if base is not None:
                grouped.setdefault(base, []).append(group)
        for variants in grouped.values():
            variants.sort(key=lambda g: g.case.sweep_position or 0)
        return grouped

    @property
    def passed(self) -> list[CaseGroup]:
        return [r for r in self.results if r.outcome is Outcome.PASS]

    @property
    def failed(self) -> list[CaseGroup]:
        return [r for r in self.results if r.outcome is Outcome.FAIL]

    @property
    def errored(self) -> list[CaseGroup]:
        return [r for r in self.results if r.outcome is Outcome.ERROR]

    @property
    def flaky(self) -> list[CaseGroup]:
        """Cases that both passed and failed across repeats."""
        return [r for r in self.results if r.is_flaky]

    @property
    def score(self) -> float:
        """Severity-weighted pass rate, 0.0–100.0.

        Errored cases count against the score — a case that could not be run is
        not a case that passed.
        """
        gradable = [r for r in self.results if r.outcome is not Outcome.SKIP]
        total = sum(r.case.severity.weight for r in gradable)
        if not total:
            return 0.0
        earned = sum(r.case.severity.weight for r in gradable if r.outcome is Outcome.PASS)
        return round(100.0 * earned / total, 1)

    def has_critical_failure(self) -> bool:
        return any(
            r.case.severity is Severity.CRITICAL and r.outcome in (Outcome.FAIL, Outcome.ERROR)
            for r in self.results
        )
