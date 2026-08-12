"""Core data types for Guardrail.

Everything the runner passes around is defined here so the provider, grader,
and report layers can stay independent of one another.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
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


@dataclass
class Check:
    """A single assertion applied to a case's transcript.

    ``kind`` names a grader registered in :mod:`guardrail.graders`. ``params``
    is passed through to it verbatim. ``turn`` selects which assistant response
    to grade: an index (0-based), or -1 for the last one (the default).
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    turn: int = -1
    description: str = ""

    @classmethod
    def from_yaml(cls, raw: dict[str, Any]) -> Check:
        data = dict(raw)
        kind = data.pop("kind", None)
        if not kind:
            raise ValueError(f"check is missing required key 'kind': {raw!r}")
        turn = data.pop("turn", -1)
        description = data.pop("description", "")
        return cls(kind=kind, params=data, turn=turn, description=description)


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

        return cls(
            id=str(raw["id"]),
            turns=[str(t) for t in turns],
            checks=checks,
            family=str(raw.get("family", "general")),
            severity=severity,
            description=str(raw.get("description", "")),
            system=raw.get("system"),
            tags=[str(t) for t in raw.get("tags", [])],
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
    case: Case
    transcript: list[Message]
    checks: list[CheckResult]
    error: str | None = None
    latency_s: float = 0.0

    @property
    def outcome(self) -> Outcome:
        if self.error:
            return Outcome.ERROR
        if not self.checks:
            return Outcome.SKIP
        return Outcome.PASS if all(c.ok for c in self.checks) else Outcome.FAIL

    @property
    def assistant_turns(self) -> list[str]:
        return [m.content for m in self.transcript if m.role == "assistant"]

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]


@dataclass
class RunResult:
    target_name: str
    model: str
    results: list[CaseResult]
    started_at: str = ""
    duration_s: float = 0.0

    @property
    def passed(self) -> list[CaseResult]:
        return [r for r in self.results if r.outcome is Outcome.PASS]

    @property
    def failed(self) -> list[CaseResult]:
        return [r for r in self.results if r.outcome is Outcome.FAIL]

    @property
    def errored(self) -> list[CaseResult]:
        return [r for r in self.results if r.outcome is Outcome.ERROR]

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
