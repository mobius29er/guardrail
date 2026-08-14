"""Position sweep: expansion, `turn: swept` resolution, and the opt-in gate."""

from __future__ import annotations

import pytest

from halligan.graders import GradeContext
from halligan.models import SWEPT, Case, CaseGroup, Check, Outcome, RunResult, Suite
from halligan.runner import run_case
from helpers import FakeProvider, Scripted, make_run

CTX = GradeContext()
REFUSAL = "The role-play cannot be done. That answer will not change."


def _case(**kw) -> Case:
    base = {
        "id": "c",
        "turns": ["t1", "t2", "PROBE", "t4"],
        "checks": [Check(kind="refuses")],
        "sweep_turn": 2,
    }
    base.update(kw)
    return Case(**base)


class TestExpansion:
    def test_expands_to_one_variant_per_position(self):
        variants = _case().expand_sweep()
        assert len(variants) == 4
        assert [v.id for v in variants] == ["c@pos1", "c@pos2", "c@pos3", "c@pos4"]

    def test_probe_lands_at_each_position(self):
        for i, variant in enumerate(_case().expand_sweep()):
            assert variant.turns[i] == "PROBE", f"variant {i} misplaced the probe"
            assert variant.sweep_position == i

    def test_other_turns_keep_relative_order(self):
        """Neighborhood is controlled — only the probe moves."""
        for variant in _case().expand_sweep():
            others = [t for t in variant.turns if t != "PROBE"]
            assert others == ["t1", "t2", "t4"]

    def test_every_variant_has_the_same_length(self):
        for variant in _case().expand_sweep():
            assert len(variant.turns) == 4

    def test_variants_do_not_re_expand(self):
        """A variant must be terminal, or expansion would recurse."""
        for variant in _case().expand_sweep():
            assert variant.sweep_turn is None
            assert variant.expand_sweep() == [variant]

    def test_variants_carry_provenance(self):
        for variant in _case().expand_sweep():
            assert variant.sweep_base == "c"
            assert variant.sweep_total == 4
            assert variant.is_sweep_variant

    def test_case_without_sweep_is_returned_unchanged(self):
        plain = _case(sweep_turn=None)
        assert plain.expand_sweep() == [plain]
        assert not plain.is_sweep_variant

    def test_severity_and_family_survive_expansion(self):
        for variant in _case(family="consistency").expand_sweep():
            assert variant.family == "consistency"
            assert variant.severity == _case().severity


class TestSweepParsing:
    def _suite(self, case: dict):
        return Suite.from_yaml({"name": "s", "cases": [case]})

    def test_sweep_turn_parsed(self):
        suite = self._suite(
            {
                "id": "a",
                "turns": ["x", "y"],
                "sweep_turn": 1,
                "checks": [{"kind": "refuses"}],
            }
        )
        assert suite.cases[0].sweep_turn == 1

    def test_out_of_range_sweep_turn_rejected(self):
        with pytest.raises(ValueError, match="sweep_turn=5"):
            self._suite(
                {
                    "id": "a",
                    "turns": ["x", "y"],
                    "sweep_turn": 5,
                    "checks": [{"kind": "refuses"}],
                }
            )

    def test_single_turn_sweep_rejected(self):
        with pytest.raises(ValueError, match="nothing to sweep"):
            self._suite(
                {
                    "id": "a",
                    "turns": ["x"],
                    "sweep_turn": 0,
                    "checks": [{"kind": "refuses"}],
                }
            )

    def test_sweep_with_consistent_with_rejected(self):
        """The baseline index moves under a sweep — reject rather than average."""
        with pytest.raises(ValueError, match="consistent_with"):
            self._suite(
                {
                    "id": "a",
                    "turns": ["x", "y", "z"],
                    "sweep_turn": 2,
                    "checks": [{"kind": "consistent_with", "baseline_turn": 0}],
                }
            )

    def test_turn_swept_parsed(self):
        suite = self._suite(
            {
                "id": "a",
                "turns": ["x", "y"],
                "sweep_turn": 1,
                "checks": [{"kind": "refuses", "turn": "swept"}],
            }
        )
        check = suite.cases[0].checks[0]
        assert check.turn == SWEPT
        assert check.follows_swept_turn

    def test_bad_turn_string_rejected(self):
        with pytest.raises(ValueError, match="integer or the literal 'swept'"):
            self._suite(
                {
                    "id": "a",
                    "turns": ["x", "y"],
                    "checks": [{"kind": "refuses", "turn": "last"}],
                }
            )


class TestSweptTurnResolution:
    async def test_swept_check_follows_the_probe(self):
        """The graded response must be the one answering the probe, not a fixed index."""
        case = _case(checks=[Check(kind="contains_any", params={"terms": ["hit"]}, turn=SWEPT)])

        for variant in case.expand_sweep():
            pos = variant.sweep_position
            # Only the reply at the probe's position carries the marker, so the
            # check passes if and only if it graded the right turn.
            replies = ["miss"] * 4
            replies[pos] = "hit"

            result = await run_case(variant, Scripted(replies), CTX)
            assert result.outcome is Outcome.PASS, (
                f"position {pos} graded the wrong response: {result.failed_checks}"
            )

    async def test_swept_check_on_plain_case_uses_last_turn(self):
        """Graceful degradation — no crash if the sentinel reaches a non-sweep case."""
        case = Case(
            id="c",
            turns=["a", "b"],
            checks=[Check(kind="contains_any", params={"terms": ["final"]}, turn=SWEPT)],
        )
        result = await run_case(case, FakeProvider(["first", "final"]), CTX)
        assert result.outcome is Outcome.PASS


class TestSweepReporting:
    def _group(self, base: str, pos: int, total: int, outcome: Outcome) -> CaseGroup:
        case = Case(
            id=f"{base}@pos{pos + 1}",
            turns=["x"],
            checks=[Check(kind="refuses")],
            sweep_position=pos,
            sweep_total=total,
            sweep_base=base,
        )
        return CaseGroup(case=case, runs=[make_run(case, outcome)])

    def test_sweeps_grouped_and_ordered_by_position(self):
        run = RunResult(
            "t",
            "m",
            [
                self._group("probe", 2, 3, Outcome.FAIL),
                self._group("probe", 0, 3, Outcome.PASS),
                self._group("probe", 1, 3, Outcome.PASS),
            ],
            sweep=True,
        )
        assert list(run.sweeps) == ["probe"]
        assert [g.case.sweep_position for g in run.sweeps["probe"]] == [0, 1, 2]

    def test_non_sweep_cases_excluded(self):
        case = Case(id="plain", turns=["x"], checks=[Check(kind="refuses")])
        run = RunResult("t", "m", [CaseGroup(case=case, runs=[make_run(case, Outcome.PASS)])])
        assert run.sweeps == {}

    def test_position_appears_in_json_and_reports(self):
        from halligan.report import to_dict, to_html, to_markdown

        run = RunResult(
            "t",
            "m",
            [self._group("probe", 0, 2, Outcome.PASS), self._group("probe", 1, 2, Outcome.FAIL)],
            sweep=True,
        )
        payload = to_dict(run)
        assert payload["sweep"] is True
        assert payload["cases"][0]["sweep"] == {"base": "probe", "position": 1, "of": 2}

        assert "Position sweep" in to_markdown(run)
        assert "Position sweep" in to_html(run)


class TestOptInGate:
    """The sweep must stay off unless asked — it multiplies cost by N."""

    def test_shipped_sweep_case_is_declared(self):
        from pathlib import Path

        from halligan.config import load_suites

        suites = {s.name: s for s in load_suites([Path(__file__).parent.parent / "suites"])}
        sweeps = [c for c in suites["consistency"].cases if c.sweep_turn is not None]
        assert sweeps, "the consistency suite should ship a sweep case"

        for case in sweeps:
            assert len(case.expand_sweep()) == len(case.turns)
            # Its checks must follow the swept turn, or the sweep grades noise.
            assert any(c.follows_swept_turn for c in case.checks), (
                f"{case.id} sweeps but no check uses 'turn: swept'"
            )
