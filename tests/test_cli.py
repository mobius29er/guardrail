"""CLI behaviour that has burned us before. No network, no model calls."""

from __future__ import annotations

import io
import re
import sys
import textwrap

from halligan.cli import (
    EXIT_CONFIG,
    EXIT_OK,
    FLAKY_ICON,
    ICON,
    _asset_root,
    _c,
    _force_utf8,
    cases_needing_judge,
    main,
)
from halligan.config import TargetConfig
from halligan.models import Case, Check

TARGET_NO_JUDGE = """\
name: t
provider:
  name: ollama
  model: mistral:latest
"""

TARGET_WITH_JUDGE = (
    TARGET_NO_JUDGE
    + """\
judge:
  name: ollama
  model: codellama:13b-instruct
"""
)

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


class TestInteractiveInit:
    """`halligan init --interactive` — menus instead of hand-editing YAML.

    The wizard's output is a config file other code has to load, so these
    assert it parses, not just that it was written.
    """

    @staticmethod
    def _drive(monkeypatch, answers: list[str]) -> None:
        it = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(it, ""))
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    def test_writes_a_config_that_loads(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        # Policy menu is [<packs...>, general, inline]; "2" is the generic template.
        self._drive(monkeypatch, ["insurance-bot", "1", "", "0.7", "5", "2"])
        assert main(["init", "--interactive", "--dir", str(tmp_path)]) == EXIT_OK

        target = TargetConfig.load(tmp_path / "target.yaml")
        assert target.name == "insurance-bot"
        assert target.provider["name"] == "anthropic"
        # The template wires in via system_file, which must resolve.
        assert (tmp_path / "policy.md").is_file()
        assert "<ASSISTANT_NAME>" in (target.system or "")

    def test_choosing_a_pack_writes_that_pack_s_policy(self, tmp_path, monkeypatch):
        """Picking a domain should give you the prompt its probes assume."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        self._drive(monkeypatch, ["parish-bot", "1", "", "0.7", "5", "1"])
        assert main(["init", "--interactive", "--dir", str(tmp_path)]) == EXIT_OK
        system = TargetConfig.load(tmp_path / "target.yaml").system or ""
        assert "Catholic" in system
        assert "<ASSISTANT_NAME>" not in system

    def test_http_branch_emits_a_usable_body_template(self, tmp_path, monkeypatch):
        self._drive(
            monkeypatch,
            ["my-app", "3", "my-app-prod", "0.7", "https://api.example.com/chat", "5", "2"],
        )
        assert main(["init", "--interactive", "--dir", str(tmp_path)]) == EXIT_OK
        target = TargetConfig.load(tmp_path / "target.yaml")
        assert target.provider["name"] == "http"
        assert target.provider["url"] == "https://api.example.com/chat"
        assert target.provider["body"]["messages"] == "{{messages}}"

    def test_declining_a_judge_omits_the_block(self, tmp_path, monkeypatch):
        self._drive(monkeypatch, ["t", "3", "m", "0.7", "https://x.example/c", "5", "2"])
        assert main(["init", "--interactive", "--dir", str(tmp_path)]) == EXIT_OK
        assert TargetConfig.load(tmp_path / "target.yaml").judge is None

    def test_refuses_without_a_tty(self, tmp_path, monkeypatch, capsys):
        """A piped stdin in CI must fail loudly, never hang waiting on input."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        assert main(["init", "--interactive", "--dir", str(tmp_path)]) == EXIT_CONFIG
        assert "not a TTY" in capsys.readouterr().err
        assert not (tmp_path / "target.yaml").exists()

    def test_does_not_clobber_an_existing_target(self, tmp_path, monkeypatch):
        (tmp_path / "target.yaml").write_text("mine", encoding="utf-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        self._drive(monkeypatch, ["t", "1", "", "0.7", "5", "2"])
        assert main(["init", "--interactive", "--dir", str(tmp_path)]) == EXIT_CONFIG
        assert (tmp_path / "target.yaml").read_text(encoding="utf-8") == "mine"


class TestPolicyTemplate:
    def test_ships_with_the_package(self):
        root = _asset_root()
        assert root is not None
        assert (root / "policies/general.md").is_file()

    def test_placeholders_are_all_documented(self):
        """A placeholder nobody tells you to replace is a trap."""
        root = _asset_root()
        policy = (root / "policies/general.md").read_text(encoding="utf-8")
        found = set(re.findall(r"<[A-Z_]+>", policy))
        assert found == {
            "<ASSISTANT_NAME>",
            "<DOMAIN>",
            "<CREDENTIALED_ROLE>",
            "<RESERVED_ACTIONS>",
        }


class TestColour:
    """Colour must never leak escape codes into something that can't render them.

    A report piped to a file, a CI log, or cmd.exe on the legacy Windows console
    are three different failure modes and only one of them is a pipe.
    """

    @staticmethod
    def _clear(monkeypatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)

    def test_stripped_when_piped(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        assert _c("\033[32m✓\033[0m ok") == "✓ ok"

    def test_no_color_beats_a_tty(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        assert "\033[" not in _c("\033[32m✓\033[0m")

    def test_empty_no_color_is_not_set(self, monkeypatch):
        """no-color.org: the variable counts only when non-empty."""
        self._clear(monkeypatch)
        monkeypatch.setenv("NO_COLOR", "")
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert "\033[" in _c("\033[32m✓\033[0m")

    def test_force_color_survives_a_pipe(self, monkeypatch):
        """CI renders ANSI in logs but is not a TTY."""
        self._clear(monkeypatch)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert _c("\033[32m✓\033[0m") == "\033[32m✓\033[0m"

    def test_no_color_beats_force_color(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.setenv("NO_COLOR", "1")
        assert "\033[" not in _c("\033[32m✓\033[0m")

    def test_stripping_leaves_the_text_intact(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        assert _c("\033[45m\033[97m FLAKY \033[0m 3 case(s)") == " FLAKY  3 case(s)"

    def test_every_icon_survives_stripping(self, monkeypatch):
        """The glyph carries the meaning when colour is gone — keep it."""
        self._clear(monkeypatch)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        for icon in list(ICON.values()) + [FLAKY_ICON]:
            stripped = _c(icon)
            assert "\033[" not in stripped
            assert stripped.strip()


class TestOutputEncoding:
    """The status glyphs must survive a non-UTF-8 stdout.

    `halligan validate` crashed with UnicodeEncodeError whenever its output was
    redirected on Windows, because Python picks cp1252 for a piped stream and
    ✓ is not in cp1252. It was caught by the repo's own pre-commit hook, which
    pipes the command. Telling users to export PYTHONIOENCODING is not a fix.
    """

    def test_main_reconfigures_the_streams(self, monkeypatch, capsys):
        seen = []

        class Stream(io.StringIO):
            encoding = "cp1252"

            def reconfigure(self, **kw):
                seen.append(kw)

        monkeypatch.setattr(sys, "stdout", Stream())
        monkeypatch.setattr(sys, "stderr", Stream())
        _force_utf8()
        assert seen, "streams were never reconfigured"
        assert all(kw.get("encoding") == "utf-8" for kw in seen)
        # errors=replace is the net for a stream that cannot be fully switched.
        assert all(kw.get("errors") == "replace" for kw in seen)

    def test_a_stream_without_reconfigure_is_survivable(self, monkeypatch):
        """Some wrapped streams have no reconfigure. That must not raise."""
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        _force_utf8()  # no exception

    def test_reconfigure_failure_is_swallowed(self, monkeypatch):
        class Hostile(io.StringIO):
            def reconfigure(self, **kw):
                raise OSError("detached")

        monkeypatch.setattr(sys, "stdout", Hostile())
        monkeypatch.setattr(sys, "stderr", Hostile())
        _force_utf8()  # no exception

    def test_glyphs_encode_as_utf8(self):
        """Whatever else changes, the markers must be representable."""
        for icon in list(ICON.values()) + [FLAKY_ICON]:
            icon.encode("utf-8")
