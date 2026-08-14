"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from halligan import __version__
from halligan.config import TargetConfig, load_suites
from halligan.graders import available_graders
from halligan.models import CaseGroup, Outcome, RunResult, Severity
from halligan.providers import PROVIDERS
from halligan.report import redact, write_html, write_json, write_markdown
from halligan.runner import run_suites

# Exit codes
EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_CRITICAL = 2
EXIT_CONFIG = 3

ICON = {
    Outcome.PASS: "\033[32m✓\033[0m",
    Outcome.FAIL: "\033[31m✗\033[0m",
    Outcome.ERROR: "\033[33m!\033[0m",
    Outcome.SKIP: "\033[90m-\033[0m",
}
# A case that both passed and failed across repeats gets its own marker — it is
# neither of the other two, and conflating it with either hides the finding.
FLAKY_ICON = "\033[35m~\033[0m"


def _temperature_of(target: TargetConfig) -> float | None:
    """The configured sampling temperature, if the target sets one."""
    raw = target.provider.get("temperature")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


DIM = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str) -> str:
    """Strip ANSI codes when the terminal (or a pipe) won't render them."""
    if _supports_color():
        return text
    import re

    return re.sub(r"\033\[[0-9;]*m", "", text)


def _load_dotenv() -> None:
    """Load .env if python-dotenv is installed. Optional by design."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=False)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    _load_dotenv()

    try:
        target = TargetConfig.load(args.target)
        suites = load_suites(args.suite)
    except (FileNotFoundError, ValueError) as exc:
        print(_c(f"\033[31mConfiguration error:\033[0m {exc}"), file=sys.stderr)
        return EXIT_CONFIG

    authored = [case for s in suites for case in s.cases]
    sweepable = [c for c in authored if c.sweep_turn is not None]
    total = sum(len(c.expand_sweep()) for c in authored) if args.sweep else len(authored)
    repeat = max(1, int(args.repeat))
    print(
        _c(
            f"{BOLD}Halligan{RESET} → {target.name} "
            f"{DIM}({target.provider.get('name')}/{target.model}){RESET}"
        )
    )
    detail = f"{total} case(s) across {len(suites)} suite(s), concurrency {target.concurrency}"
    if args.sweep and sweepable:
        detail += f", position sweep on {len(sweepable)} case(s)"
    if repeat > 1:
        detail += f", {repeat}× repeats ({total * repeat} runs)"
    print(_c(f"{DIM}{detail}{RESET}\n"))

    # Silence here would read as "no sweeps exist", when the truth is that
    # sweeps exist and were skipped to save money.
    if sweepable and not args.sweep:
        print(
            _c(
                f"  {DIM}note: {len(sweepable)} case(s) declare a position sweep, "
                f"running in authored order only. Pass --sweep to expand them "
                f"(costs ~{sum(len(c.expand_sweep()) for c in sweepable)} cases "
                f"instead of {len(sweepable)}).{RESET}\n"
            )
        )

    if repeat > 1 and _temperature_of(target) == 0:
        print(
            _c(
                f"  {DIM}note: temperature is 0, so repeats measure only provider-side "
                f"nondeterminism. Set it to your production value to measure what users "
                f"actually hit.{RESET}\n"
            )
        )

    done = 0

    def on_progress(group: CaseGroup) -> None:
        nonlocal done
        done += 1
        icon = FLAKY_ICON if group.is_flaky else ICON[group.outcome]
        sev = group.case.severity.value
        meta = f"{sev}, {group.latency_s}s"
        if len(group.runs) > 1:
            meta = f"{sev}, {len(group.failures)}/{len(group.graded)} failed, {group.latency_s}s"
        print(_c(f"  {icon} [{done}/{total}] {group.case.id} {DIM}({meta}){RESET}"))

        result = group.representative
        if result is None:
            return
        if group.outcome is Outcome.FAIL:
            for check in result.failed_checks:
                print(
                    _c(f"      {DIM}└─{RESET} \033[31m{check.kind}\033[0m: {redact(check.reason)}")
                )
        elif group.outcome is Outcome.ERROR and result.error:
            print(_c(f"      {DIM}└─{RESET} \033[33m{redact(result.error)}\033[0m"))

    try:
        run: RunResult = asyncio.run(
            run_suites(
                target,
                suites,
                on_progress=on_progress,
                filter_family=args.family,
                filter_id=args.case,
                repeat=repeat,
                flake_threshold=args.flake_threshold,
                sweep=args.sweep,
            )
        )
    except ValueError as exc:
        print(_c(f"\033[31mConfiguration error:\033[0m {exc}"), file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print(_c("\n\033[33mInterrupted.\033[0m"), file=sys.stderr)
        return EXIT_CONFIG

    _print_summary(run)

    if args.report:
        write_html(run, args.report)
        print(_c(f"{DIM}HTML report → {args.report}{RESET}"))
    if args.json:
        write_json(run, args.json, include_transcripts=not args.no_transcripts)
        print(_c(f"{DIM}JSON results → {args.json}{RESET}"))
    if args.markdown:
        write_markdown(run, args.markdown)
        print(_c(f"{DIM}Markdown report → {args.markdown}{RESET}"))

    if run.has_critical_failure():
        return EXIT_CRITICAL
    if run.failed or run.errored:
        return EXIT_FAILURES
    if args.fail_under is not None and run.score < args.fail_under:
        print(
            _c(
                f"\033[31mScore {run.score}% is below the --fail-under "
                f"threshold of {args.fail_under}%.\033[0m"
            )
        )
        return EXIT_FAILURES
    return EXIT_OK


def _print_summary(run: RunResult) -> None:
    score_color = "\033[32m" if run.score >= 90 else "\033[33m" if run.score >= 70 else "\033[31m"
    print()
    print(_c(f"{BOLD}{'─' * 58}{RESET}"))
    summary = (
        f"  {BOLD}Score {score_color}{run.score}%{RESET}   "
        f"\033[32m{len(run.passed)} passed\033[0m  "
        f"\033[31m{len(run.failed)} failed\033[0m  "
        f"\033[33m{len(run.errored)} errored\033[0m  "
    )
    if run.repeat > 1:
        summary += f"\033[35m{len(run.flaky)} flaky\033[0m  "
    print(_c(summary + f"{DIM}{run.duration_s}s{RESET}"))

    if run.flaky:
        print(
            _c(
                f"  \033[45m\033[97m FLAKY \033[0m {len(run.flaky)} case(s) both passed "
                f"and failed across {run.repeat} runs:"
            )
        )
        for group in sorted(run.flaky, key=lambda g: -g.flake_rate):
            print(
                _c(
                    f"      • {group.case.id} {DIM}"
                    f"{len(group.failures)}/{len(group.graded)} failed "
                    f"({group.flake_rate:.0%}){RESET}"
                )
            )

    critical = [
        r
        for r in run.results
        if r.case.severity is Severity.CRITICAL and r.outcome is not Outcome.PASS
    ]
    if critical:
        print(
            _c(
                f"  \033[41m\033[97m CRITICAL \033[0m {len(critical)} critical case(s) did not pass:"
            )
        )
        for group in critical:
            print(_c(f"      • {group.case.id}"))
    print(_c(f"{BOLD}{'─' * 58}{RESET}"))


# ---------------------------------------------------------------------------
# other commands
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    try:
        suites = load_suites(args.suite)
    except (FileNotFoundError, ValueError) as exc:
        print(_c(f"\033[31mError:\033[0m {exc}"), file=sys.stderr)
        return EXIT_CONFIG

    for suite in suites:
        print(_c(f"\n{BOLD}{suite.name}{RESET} {DIM}({suite.path}){RESET}"))
        if suite.description:
            print(_c(f"  {DIM}{suite.description.strip().splitlines()[0]}{RESET}"))
        for case in suite.cases:
            turns = f"{len(case.turns)} turn{'s' if len(case.turns) != 1 else ''}"
            sweep = ""
            if case.sweep_turn is not None:
                sweep = f", sweep@{case.sweep_turn} → {len(case.turns)} positions"
            print(
                _c(
                    f"  • {case.id} {DIM}[{case.family}/{case.severity.value}, "
                    f"{turns}, {len(case.checks)} check(s){sweep}]{RESET}"
                )
            )
    print()
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        suites = load_suites(args.suite)
    except (FileNotFoundError, ValueError) as exc:
        print(_c(f"\033[31mInvalid:\033[0m {exc}"), file=sys.stderr)
        return EXIT_CONFIG

    known = set(available_graders())
    problems: list[str] = []
    for suite in suites:
        for case in suite.cases:
            for check in case.checks:
                if check.kind not in known:
                    problems.append(
                        f"{suite.path}: case {case.id!r} uses unknown grader {check.kind!r}"
                    )
                if check.follows_swept_turn:
                    if case.sweep_turn is None:
                        problems.append(
                            f"{suite.path}: case {case.id!r} uses 'turn: swept' but "
                            f"declares no sweep_turn — the check would silently fall "
                            f"back to the last turn"
                        )
                    continue
                if check.turn >= len(case.turns):
                    problems.append(
                        f"{suite.path}: case {case.id!r} has a check targeting turn "
                        f"{check.turn} but only {len(case.turns)} turn(s) are defined"
                    )

    if problems:
        for problem in problems:
            print(_c(f"\033[31m✗\033[0m {problem}"), file=sys.stderr)
        return EXIT_CONFIG

    total = sum(len(s.cases) for s in suites)
    print(_c(f"\033[32m✓\033[0m {len(suites)} suite(s), {total} case(s) — all valid."))
    return EXIT_OK


def cmd_graders(args: argparse.Namespace) -> int:
    from halligan import graders as g

    print(_c(f"\n{BOLD}Available graders{RESET}\n"))
    for kind in available_graders():
        doc = (g.REGISTRY[kind].__doc__ or "").strip().split("\n")[0]
        print(_c(f"  {BOLD}{kind}{RESET}\n    {DIM}{doc}{RESET}"))
    print()
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report which credentials are present — never their values."""
    _load_dotenv()

    print(_c(f"\n{BOLD}Halligan environment{RESET}\n"))
    print(_c(f"  version      {__version__}"))
    print(_c(f"  python       {sys.version.split()[0]}"))

    env_file = Path(".env")
    print(
        _c(
            f"  .env         {'found' if env_file.is_file() else 'not found'} "
            f"{DIM}(optional){RESET}"
        )
    )

    print(_c(f"\n{BOLD}Provider credentials{RESET}\n"))
    for name, cls in sorted(PROVIDERS.items()):
        var = cls.env_var
        if var is None:
            print(_c(f"  \033[32m✓\033[0m {name:<10} {DIM}no credential required{RESET}"))
            continue
        value = os.environ.get(var, "")
        if value:
            # Length only — never the value, not even a prefix.
            print(
                _c(f"  \033[32m✓\033[0m {name:<10} {var} is set {DIM}({len(value)} chars){RESET}")
            )
        else:
            print(_c(f"  \033[90m·\033[0m {name:<10} {var} {DIM}not set{RESET}"))

    print(_c(f"\n{DIM}Values are never printed. See SECURITY.md.{RESET}\n"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="halligan",
        description="Automated guardrail testing for AI assistants.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  all cases passed\n"
            "  1  one or more non-critical failures\n"
            "  2  a critical-severity case failed\n"
            "  3  configuration error\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"halligan {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run suites against a target")
    p_run.add_argument("--target", "-t", required=True, help="target config YAML")
    p_run.add_argument(
        "--suite",
        "-s",
        action="append",
        required=True,
        help="suite file or directory (repeatable)",
    )
    p_run.add_argument("--report", help="write an HTML report here")
    p_run.add_argument("--json", help="write raw JSON results here")
    p_run.add_argument("--markdown", help="write a Markdown report here")
    p_run.add_argument(
        "--no-transcripts",
        action="store_true",
        help="omit full transcripts from the JSON output",
    )
    p_run.add_argument("--family", help="only run cases in this family")
    p_run.add_argument("--case", help="only run cases whose id contains this substring")
    p_run.add_argument(
        "--repeat",
        "-n",
        type=int,
        default=1,
        metavar="N",
        help=(
            "run every case N times and grade across the whole set. A case fails "
            "if ANY run fails, and cases that both pass and fail are reported as "
            "flaky. Use this to tell a real guardrail from a coin flip."
        ),
    )
    p_run.add_argument(
        "--sweep",
        action="store_true",
        help=(
            "expand cases declaring 'sweep_turn' into one variant per position, "
            "isolating whether WHERE a prompt lands changes the outcome. OFF by "
            "default: this multiplies cost by the number of positions, and "
            "without it a sweep case runs once in its authored order."
        ),
    )
    p_run.add_argument(
        "--flake-threshold",
        type=float,
        default=0.0,
        metavar="RATE",
        help=(
            "fraction of failing runs tolerated before a case is marked failed "
            "(0.0–1.0, default 0.0 = any failure fails). --flake-threshold 0.1 "
            "accepts up to one failure in ten."
        ),
    )
    p_run.add_argument(
        "--fail-under",
        type=float,
        metavar="PCT",
        help="exit non-zero if the weighted score falls below PCT",
    )
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="list suites and cases")
    p_list.add_argument("--suite", "-s", action="append", required=True)
    p_list.set_defaults(func=cmd_list)

    p_val = sub.add_parser("validate", help="check suite files without calling any API")
    p_val.add_argument("--suite", "-s", action="append", required=True)
    p_val.set_defaults(func=cmd_validate)

    p_gr = sub.add_parser("graders", help="list available graders")
    p_gr.set_defaults(func=cmd_graders)

    p_doc = sub.add_parser("doctor", help="show which credentials are configured")
    p_doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
