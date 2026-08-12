"""Suite/target loading, and validation of the suites shipped in this repo."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guardrail.config import TargetConfig, load_suites
from guardrail.graders import available_graders
from guardrail.models import Severity, Suite

REPO = Path(__file__).resolve().parent.parent
SUITES = REPO / "suites"


class TestShippedSuites:
    """The suites in this repo must always be loadable and internally valid."""

    def test_suites_load(self):
        suites = load_suites([SUITES])
        assert len(suites) >= 5

    def test_every_check_uses_a_known_grader(self):
        known = set(available_graders())
        for suite in load_suites([SUITES]):
            for case in suite.cases:
                for check in case.checks:
                    assert check.kind in known, (
                        f"{suite.path}: case {case.id!r} uses unknown grader {check.kind!r}"
                    )

    def test_check_turn_indices_are_in_range(self):
        for suite in load_suites([SUITES]):
            for case in suite.cases:
                for check in case.checks:
                    assert check.turn < len(case.turns), (
                        f"{suite.path}: case {case.id!r} check targets turn "
                        f"{check.turn} but only {len(case.turns)} turns exist"
                    )

    def test_case_ids_are_globally_unique(self):
        seen: dict[str, str] = {}
        for suite in load_suites([SUITES]):
            for case in suite.cases:
                assert case.id not in seen, (
                    f"case id {case.id!r} appears in both {seen[case.id]} and {suite.path}"
                )
                seen[case.id] = suite.path

    def test_pastoral_suite_has_critical_cases(self):
        """The sacramental-substitution checks must be marked critical."""
        suites = {s.name: s for s in load_suites([SUITES])}
        pastoral = suites["pastoral_safety"]
        critical = [c for c in pastoral.cases if c.severity is Severity.CRITICAL]
        assert len(critical) >= 3
        for case in critical:
            kinds = {c.kind for c in case.checks}
            assert "no_authority_claim" in kinds or "offers_human_referral" in kinds

    def test_consistency_suite_uses_the_drift_detector(self):
        suites = {s.name: s for s in load_suites([SUITES])}
        cases = suites["consistency"].cases
        assert any(any(chk.kind == "consistent_with" for chk in case.checks) for case in cases)
        # The drift detector is meaningless single-turn.
        for case in cases:
            if any(chk.kind == "consistent_with" for chk in case.checks):
                assert len(case.turns) > 1


class TestSuiteParsing:
    def test_prompt_shorthand_becomes_one_turn(self):
        suite = Suite.from_yaml(
            {
                "name": "s",
                "cases": [{"id": "a", "prompt": "hello", "checks": [{"kind": "refuses"}]}],
            }
        )
        assert suite.cases[0].turns == ["hello"]

    def test_suite_system_cascades_to_cases(self):
        suite = Suite.from_yaml(
            {
                "name": "s",
                "system": "you are a test",
                "cases": [
                    {"id": "a", "prompt": "x", "checks": [{"kind": "refuses"}]},
                    {
                        "id": "b",
                        "prompt": "y",
                        "system": "own prompt",
                        "checks": [{"kind": "refuses"}],
                    },
                ],
            }
        )
        assert suite.cases[0].system == "you are a test"
        assert suite.cases[1].system == "own prompt"

    def test_missing_checks_rejected(self):
        with pytest.raises(ValueError, match="no checks"):
            Suite.from_yaml({"name": "s", "cases": [{"id": "a", "prompt": "x"}]})

    def test_missing_turns_and_prompt_rejected(self):
        with pytest.raises(ValueError, match="turns.*prompt"):
            Suite.from_yaml({"name": "s", "cases": [{"id": "a", "checks": [{"kind": "refuses"}]}]})

    def test_duplicate_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate case id"):
            Suite.from_yaml(
                {
                    "name": "s",
                    "cases": [
                        {"id": "a", "prompt": "x", "checks": [{"kind": "refuses"}]},
                        {"id": "a", "prompt": "y", "checks": [{"kind": "refuses"}]},
                    ],
                }
            )

    def test_unknown_severity_rejected_with_helpful_message(self):
        with pytest.raises(ValueError, match="unknown severity"):
            Suite.from_yaml(
                {
                    "name": "s",
                    "cases": [
                        {
                            "id": "a",
                            "prompt": "x",
                            "severity": "catastrophic",
                            "checks": [{"kind": "refuses"}],
                        }
                    ],
                }
            )

    def test_check_without_kind_rejected(self):
        with pytest.raises(ValueError, match="missing required key 'kind'"):
            Suite.from_yaml(
                {"name": "s", "cases": [{"id": "a", "prompt": "x", "checks": [{"terms": ["z"]}]}]}
            )


class TestTargetConfig:
    def test_loads_example_target(self):
        target = TargetConfig.load(REPO / "Truthly" / "target.example.yaml")
        assert target.provider["name"] == "anthropic"
        assert target.system and "Catholic" in target.system
        assert target.judge is not None

    def test_example_target_contains_no_literal_key(self, tmp_path):
        """Guards against someone pasting a real key into the committed example."""
        raw = (REPO / "Truthly" / "target.example.yaml").read_text()
        from guardrail.report import redact

        assert redact(raw) == raw, "the example target config appears to contain a secret"

    def test_missing_provider_rejected(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump({"name": "x"}))
        with pytest.raises(ValueError, match="provider"):
            TargetConfig.load(p)

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            TargetConfig.load(tmp_path / "nope.yaml")

    def test_zero_concurrency_rejected(self, tmp_path):
        p = tmp_path / "t.yaml"
        p.write_text(
            yaml.safe_dump({"provider": {"name": "openai", "model": "m"}, "concurrency": 0})
        )
        with pytest.raises(ValueError, match="concurrency"):
            TargetConfig.load(p)
