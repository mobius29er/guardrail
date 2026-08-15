"""CLI behaviour that has burned us before. No network, no model calls."""

from __future__ import annotations

import textwrap

from halligan.cli import EXIT_CONFIG, EXIT_OK, _asset_root, cases_needing_judge, main
from halligan.config import TargetConfig
from halligan.models import Case, Check

TARGET_NO_JUDGE = """\
name: t
provider:
  name: ollama
  model: mistral:latest
"""

TARGET_WITH_JUDGE = TARGET_NO_JUDGE + """\
judge:
  name: ollama
  model: codellama:13b-instruct
"""

SUITE_WITH_JUDGE = """\
name: needs_judge
cases:
  - id: judged-case
    prompt: "hello"
    checks:
      - kind: judge
        rubric: "must be warm"
"""

SUITE_NO_JUDGE = """\
name: keyword_only
cases:
  - id: plain-case
    prompt: "hello"
    checks:
      - kind: refuses
"""


def _write(tmp_path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


class TestBundledAssets:
    """`pip install halligan` shipped a CLI and nothing to point it at.

    The suites are force-included into the wheel and resolved from the repo
    root in a source checkout; both layouts must satisfy the same resolver.
    """

    def test_asset_root_resolves(self):
        root = _asset_root()
        assert root is not None
        assert (root / "suites").is_dir()
        assert (root / "Truthly/target.example.yaml").is_file()

    def test_init_writes_a_runnable_directory(self, tmp_path):
        assert main(["init", "--dir", str(tmp_path)]) == EXIT_OK
        assert (tmp_path / "target.yaml").is_file()
        assert (tmp_path / "data/names.yaml").is_file()
        assert len(list((tmp_path / "suites").glob("*.yaml"))) == 9
        # The whole point: the directory it produces actually validates.
        assert main(["validate", "--suite", str(tmp_path / "suites")]) == EXIT_OK

    def test_init_does_not_clobber(self, tmp_path):
        main(["init", "--dir", str(tmp_path)])
        (tmp_path / "target.yaml").write_text("mine", encoding="utf-8")
        assert main(["init", "--dir", str(tmp_path)]) == EXIT_OK
        assert (tmp_path / "target.yaml").read_text(encoding="utf-8") == "mine"

    def test_init_force_overwrites(self, tmp_path):
        main(["init", "--dir", str(tmp_path)])
        (tmp_path / "target.yaml").write_text("mine", encoding="utf-8")
        assert main(["init", "--dir", str(tmp_path), "--force"]) == EXIT_OK
        assert (tmp_path / "target.yaml").read_text(encoding="utf-8") != "mine"


class TestJudgePreflight:
    """A missing judge block used to fail every judge check rather than skip.

    Most cases carry one, so the run reported a wall of red that read as the
    model misbehaving. It is a configuration error and must be caught before
    any request is sent.
    """

    def test_missing_judge_is_a_config_error(self, tmp_path, capsys):
        target = _write(tmp_path, "t.yaml", TARGET_NO_JUDGE)
        suite = _write(tmp_path, "s.yaml", SUITE_WITH_JUDGE)
        assert main(["run", "-t", target, "-s", suite]) == EXIT_CONFIG
        err = capsys.readouterr().err
        assert "no judge model" in err
        assert "judged-case" in err

    # The cases below assert on the predicate rather than driving `main`, which
    # would proceed past the preflight and make real model calls. This suite
    # runs with no network.

    @staticmethod
    def _case(cid: str, kinds: list[str]) -> Case:
        return Case(id=cid, turns=["x"], checks=[Check(kind=k) for k in kinds])

    def _target(self, judge: dict | None) -> TargetConfig:
        return TargetConfig(name="t", provider={"name": "ollama", "model": "m"}, judge=judge)

    def test_judge_free_selection_needs_nothing(self):
        """The escape hatch must stay open — don't demand a judge nobody calls."""
        selected = [self._case("plain", ["refuses"])]
        assert cases_needing_judge(self._target(None), selected) == []

    def test_configured_judge_satisfies_everything(self):
        selected = [self._case("judged", ["judge"])]
        assert cases_needing_judge(self._target({"name": "ollama", "model": "m"}), selected) == []

    def test_only_selected_cases_count(self):
        """--case/--family narrow the run, so they narrow the requirement too."""
        judged = self._case("judged", ["judge"])
        plain = self._case("plain", ["refuses"])
        assert cases_needing_judge(self._target(None), [judged, plain]) == ["judged"]
        assert cases_needing_judge(self._target(None), [plain]) == []
