"""Matched experimental arms must stay matched.

A controlled arm is only worth running if the arms differ in exactly one
variable. `sympathy-pressure-only` and `neutral-pressure-only` open with a
byte-identical request and escalate over the same number of turns; the only
thing that changes is whether the pressure is emotional or a stated
preference. That is what makes "sympathy moved the guardrail" falsifiable
rather than a story told after the fact.

Nothing enforced that until this file existed. Someone rewording one arm — or
adding a turn to it, or attaching a grader to one side only — silently turns a
controlled comparison into two unrelated tests that still look like a pair in
the report. The failure is invisible: both arms keep passing, the numbers stay
plausible, and the conclusion drawn from them is confounded.

The description on the control arm already states the invariant in prose.
These tests make the prose load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from halligan.config import load_suites

SUITES = Path(__file__).resolve().parents[1] / "suites"

# (treatment, control). Add pairs here as new arms are authored.
ARM_PAIRS = [("sympathy-pressure-only", "neutral-pressure-only")]

# Word count is a proxy for token count — close enough to catch a turn being
# doubled in length, loose enough that ordinary rewording does not fail CI.
# The shipped arms sit at 61 and 53 words (0.87); 0.70 leaves honest headroom.
LENGTH_RATIO = 0.70


def _cases() -> dict:
    found = {}
    for suite in load_suites([SUITES]):
        for case in suite.cases:
            found[case.id] = case
    return found


@pytest.fixture(scope="module")
def cases() -> dict:
    return _cases()


def _turn_text(turn) -> str:
    """Turns are strings or mappings depending on how they were authored."""
    if isinstance(turn, str):
        return turn
    for key in ("user", "content", "text"):
        value = getattr(turn, key, None) or (turn.get(key) if isinstance(turn, dict) else None)
        if value:
            return str(value)
    return ""


@pytest.mark.parametrize("treatment,control", ARM_PAIRS)
class TestArmsRemainComparable:
    def test_both_arms_exist(self, cases, treatment, control):
        missing = [i for i in (treatment, control) if i not in cases]
        assert not missing, (
            f"{missing} not found. An arm was renamed or deleted without "
            f"updating ARM_PAIRS, so the comparison it belongs to is no "
            f"longer being run."
        )

    def test_opening_turn_is_identical(self, cases, treatment, control):
        """The variable under test starts at turn 2, so turn 1 must not vary."""
        a = _turn_text(cases[treatment].turns[0]).strip()
        b = _turn_text(cases[control].turns[0]).strip()
        assert a == b, (
            "the arms must open with the same request, or a difference at "
            f"turn 1 confounds everything after it.\n"
            f"  {treatment}: {a!r}\n  {control}: {b!r}"
        )

    def test_same_number_of_turns(self, cases, treatment, control):
        a, b = len(cases[treatment].turns), len(cases[control].turns)
        assert a == b, (
            f"{treatment} has {a} turns and {control} has {b}. Unequal "
            f"conversation length reintroduces context fatigue as a second "
            f"variable, which is the exact thing this pair exists to rule out."
        )

    def test_comparable_length(self, cases, treatment, control):
        a = sum(len(_turn_text(t).split()) for t in cases[treatment].turns)
        b = sum(len(_turn_text(t).split()) for t in cases[control].turns)
        ratio = min(a, b) / max(a, b)
        assert ratio >= LENGTH_RATIO, (
            f"{treatment} is {a} words and {control} is {b} ({ratio:.2f}). "
            f"Once one arm is substantially longer the comparison measures "
            f"prompt length as much as it measures tone. Trim the longer arm "
            f"rather than lowering LENGTH_RATIO."
        )

    def test_graded_identically(self, cases, treatment, control):
        """Same checks on both sides, or the arms are scored on different rules."""

        def kinds(case):
            return sorted(
                getattr(c, "kind", None).value
                if hasattr(getattr(c, "kind", None), "value")
                else str(getattr(c, "kind", c))
                for c in case.checks
            )

        a, b = kinds(cases[treatment]), kinds(cases[control])
        assert a == b, (
            f"the arms are graded differently, so a difference in outcome may "
            f"just be a difference in grading.\n  {treatment}: {a}\n"
            f"  {control}: {b}"
        )

    def test_same_severity(self, cases, treatment, control):
        a, b = cases[treatment].severity, cases[control].severity
        assert a == b, (
            f"{treatment} is {a} and {control} is {b}; differing severity "
            f"means one arm can fail the build while its control cannot."
        )
