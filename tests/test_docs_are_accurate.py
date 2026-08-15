"""Published counts must match reality.

The test count in the README and on the landing page drifted four times in a
single afternoon — 169, 221, 228, 233 — because it is maintained by hand and
nothing checks it. The grader count was wrong for three releases: the docs said
13 because someone counted rows in a table where four rows cover families like
contains_any/contains_all/not_contains/matches, when there are 17 kinds.

Numbers a stranger uses to size up the project should not be able to rot
silently, so they are asserted here instead. When one of these fails, the
number in the docs is wrong — not the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from halligan.config import load_suites
from halligan.graders import available_graders

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
INDEX = ROOT / "site" / "public" / "index.html"
START = ROOT / "site" / "public" / "start.html"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


@pytest.fixture(scope="module")
def truth() -> dict[str, int]:
    suites = load_suites([str(ROOT / "suites")])
    return {
        "cases": sum(len(s.cases) for s in suites),
        "suites": len(suites),
        "graders": len(available_graders()),
    }


class TestPublishedCounts:
    def test_readme_case_and_suite_counts(self, truth):
        text = _text(README)
        assert f"**{truth['cases']} cases total.**" in text
        assert f"{truth['suites']} suite(s), {truth['cases']} case(s)" in text

    def test_landing_page_stat_strip(self, truth):
        """The four numbers under the report preview."""
        stats = {
            label.strip(): int(n)
            for n, label in re.findall(r"<b>(\d+)</b>\s*([a-z ]+)", _text(INDEX))
        }
        assert stats.get("cases") == truth["cases"]
        assert stats.get("suites") == truth["suites"]
        assert stats.get("grader kinds") == truth["graders"]

    def test_grader_count_claim_in_prose(self, truth):
        """The landing page spells the grader count out in words too."""
        words = {13: "Thirteen", 17: "Seventeen", 18: "Eighteen", 19: "Nineteen"}
        expected = words.get(truth["graders"])
        if expected is None:
            pytest.skip(f"no spelling for {truth['graders']}")
        assert f"{expected} graders in all" in _text(INDEX)

    def test_start_page_case_count(self, truth):
        assert f"{truth['cases']} cases" in _text(START)


class TestNoStaleTestCount:
    """The test count is deliberately not published as an exact figure.

    It changes on almost every commit, so quoting it is a promise nobody can
    keep. The docs say what the suite guarantees — no network — instead.
    """

    @pytest.mark.parametrize("path", [README, INDEX, START])
    def test_no_hardcoded_test_count(self, path):
        text = _text(path)
        stale = re.findall(r"(\d{2,4})\s*(?:</b>\s*)?unit tests", text)
        assert not stale, (
            f"{path.name} quotes an exact test count {stale}. That number goes stale "
            f"on the next commit — say what the suite guarantees instead."
        )
