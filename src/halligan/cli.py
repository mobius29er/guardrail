"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

from halligan import __version__
from halligan.config import TargetConfig, load_suites
from halligan.estimate import DEFAULT_REPLY_TOKENS, Estimate, estimate_run
from halligan.graders import available_graders
from halligan.models import Case, CaseGroup, Outcome, RunResult, Severity
from halligan.providers import PROVIDERS
from halligan.report import redact, write_html, write_json, write_markdown
from halligan.runner import run_suites, select_cases

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


def _rates(target: TargetConfig, args: argparse.Namespace) -> tuple[float | None, float | None]:
    """Per-million-token rates from the command line, else the target config."""
    pricing = target.metadata.get("pricing") if isinstance(target.metadata, dict) else None
    pricing = pricing if isinstance(pricing, dict) else {}
    pin = args.price_in if args.price_in is not None else pricing.get("input_per_mtok")
    pout = args.price_out if args.price_out is not None else pricing.get("output_per_mtok")
    return (float(pin) if pin is not None else None, float(pout) if pout is not None else None)


def _print_estimate(
    target: TargetConfig, est: Estimate, metered: bool, args: argparse.Namespace
) -> None:
    print(
        _c(
            f"\n{BOLD}Estimate{RESET} → {target.name} "
            f"{DIM}({target.provider.get('name')}/{target.model}){RESET}"
        )
    )
    rep = f", {args.repeat}× repeats" if args.repeat > 1 else ""
    print(_c(f"{DIM}{est.cases} case run(s){rep}{RESET}\n"))

    print(_c(f"  {'target calls':<18}{est.target_calls:>12,}  {DIM}one per turn{RESET}"))
    print(_c(f"  {'judge calls':<18}{est.judge_calls:>12,}  {DIM}one per `kind: judge`{RESET}"))
    print(_c(f"  {'':<18}{'':>12}"))
    print(_c(f"  {'input tokens':<18}{est.input_tokens:>12,}  {DIM}from the actual prompts{RESET}"))
    print(
        _c(
            f"  {'output tokens':<18}{est.output_tokens:>12,}  "
            f"{DIM}assumes {est.reply_tokens} per reply{RESET}"
        )
    )

    if not metered:
        print(_c(f"\n  {BOLD}No metered cost{RESET} {DIM}— this provider runs locally.{RESET}\n"))
        return

    price_in, price_out = _rates(target, args)
    cost = est.cost(price_in, price_out)
    if cost is None:
        print(
            _c(
                f"\n  {BOLD}Cost{RESET}  unknown — no rates configured.\n"
                f"  {DIM}Add them to your target config:{RESET}\n\n"
                f"    metadata:\n"
                f"      pricing:\n"
                f"        input_per_mtok: 3.00\n"
                f"        output_per_mtok: 15.00\n\n"
                f"  {DIM}or pass --price-in / --price-out. Halligan ships no price table:{RESET}\n"
                f"  {DIM}a hardcoded rate goes stale silently and then lies with authority.{RESET}\n"
            )
        )
        return
    print(
        _c(
            f"\n  {BOLD}~${cost:,.2f}{RESET}  {DIM}at ${price_in:g}/${price_out:g} "
            f"per Mtok in/out{RESET}\n"
        )
    )
    print(_c(f"  {DIM}Approximate. Token counts use a 4-chars-per-token heuristic.{RESET}\n"))


def cases_needing_judge(target: TargetConfig, selected: list[Case]) -> list[str]:
    """Ids of selected cases that require a judge the target does not define.

    Empty when a judge is configured, or when nothing selected uses one — that
    escape hatch matters, since plenty of suites are keyword-only and should not
    be made to demand a second model they never call.
    """
    if target.judge is not None:
        return []
    return [c.id for c in selected if any(chk.kind == "judge" for chk in c.checks)]


def cmd_run(args: argparse.Namespace) -> int:
    _load_dotenv()

    try:
        target = TargetConfig.load(args.target)
        suites = load_suites(args.suite)
    except (FileNotFoundError, ValueError) as exc:
        print(_c(f"\033[31mConfiguration error:\033[0m {exc}"), file=sys.stderr)
        return EXIT_CONFIG

    # Count what will actually run, not what was authored — --family/--case
    # narrow the set, and the banner used to report the unfiltered total.
    selected = select_cases(suites, filter_family=args.family, filter_id=args.case)
    sweepable = [c for c in selected if c.sweep_turn is not None]
    total = (
        len(select_cases(suites, filter_family=args.family, filter_id=args.case, sweep=True))
        if args.sweep
        else len(selected)
    )
    # Without a judge provider every `kind: judge` check fails rather than
    # skipping, so a forgotten judge block reports a wall of red that looks like
    # the model misbehaving. Say so before spending anything.
    needs = cases_needing_judge(target, selected)
    if needs:
        shown = ", ".join(needs[:3]) + (f", +{len(needs) - 3} more" if len(needs) > 3 else "")
        print(
            _c(
                f"\033[31mConfiguration error:\033[0m {len(needs)} of {len(selected)} "
                f"selected case(s) use 'kind: judge', but the target config defines "
                f"no judge model.\n"
                f"  Affected: {shown}\n\n"
                f"  Add a judge block to {args.target} — a different model family "
                f"than the one under test:\n\n"
                f"    judge:\n"
                f"      name: anthropic\n"
                f"      model: claude-sonnet-5\n"
                f"      temperature: 0.0\n\n"
                f"  Or narrow the run with --family / --case to cases that "
                f"don't need one."
            ),
            file=sys.stderr,
        )
        return EXIT_CONFIG

    repeat = max(1, int(args.repeat))

    est = estimate_run(
        select_cases(suites, filter_family=args.family, filter_id=args.case, sweep=args.sweep),
        system=target.system,
        repeat=repeat,
        reply_tokens=args.reply_tokens,
    )
    metered = str(target.provider.get("name")) not in ("ollama",)
    if args.estimate:
        _print_estimate(target, est, metered, args)
        return EXIT_OK

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


def _asset_root() -> Path | None:
    """Where the bundled suites live.

    In a wheel they are force-included under ``halligan/_bundled/``. In a source
    checkout that directory does not exist and they sit at the repo root. Both
    use the same relative layout, so callers below can ignore which one it is.
    """
    packaged = Path(__file__).resolve().parent / "_bundled"
    if (packaged / "suites").is_dir():
        return packaged
    repo = Path(__file__).resolve().parents[2]
    if (repo / "suites").is_dir():
        return repo
    return None


#: (source path relative to the asset root, destination relative to --dir).
#: The target config lands as `target.yaml` because that is the name every
#: example command uses.
_INIT_ASSETS: tuple[tuple[str, str], ...] = (
    ("suites", "suites"),
    ("data/names.yaml", "data/names.yaml"),
    ("Truthly/target.example.yaml", "target.yaml"),
)

#: Sensible starting model per provider. Deliberately not pinned to anything
#: exotic — the point is a config that runs, which the user then edits.
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "ollama": "mistral:latest",
    "http": "my-app-prod",
}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _choose(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str:
    """Numbered menu. Returns the chosen key; Enter takes the default."""
    print(_c(f"\n{BOLD}{prompt}{RESET}"))
    for i, (key, note) in enumerate(options, 1):
        marker = "›" if i - 1 == default else " "
        print(_c(f"   {marker} {i}. {key}{DIM}{'  ' + note if note else ''}{RESET}"))
    while True:
        raw = _ask("choice", str(default + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print(_c(f"  {DIM}enter 1–{len(options)}{RESET}"))


def _credentialled() -> dict[str, bool]:
    """Which providers have a usable credential right now."""
    out: dict[str, bool] = {}
    for name, cls in PROVIDERS.items():
        var = getattr(cls, "env_var", None)
        out[name] = True if var is None else bool(os.environ.get(var, ""))
    return out


def _interactive_target(dest_root: Path, force: bool) -> str | None:
    """Ask the questions, write target.yaml and policy.md. Returns a summary."""
    _load_dotenv()
    have = _credentialled()

    print(_c(f"\n{BOLD}Configuring the assistant under test{RESET}"))
    print(_c(f"{DIM}Enter accepts the default in brackets. Ctrl-C to bail.{RESET}"))

    print()
    name = _ask("name for this target", "my-assistant")

    provider_opts = [
        (p, "ready" if have.get(p) else f"needs {getattr(PROVIDERS[p], 'env_var', '')}")
        for p in sorted(PROVIDERS)
    ]
    # Default to something that will actually run.
    ready = [i for i, (p, _) in enumerate(provider_opts) if have.get(p)]
    provider = _choose("Provider — what is under test?", provider_opts, ready[0] if ready else 0)

    print()
    model = _ask(f"model for {provider}", _DEFAULT_MODELS.get(provider, ""))
    temperature = _ask("temperature (use what you deploy at, not 0)", "0.7")

    url = ""
    if provider == "http":
        url = _ask("URL of your assistant's chat endpoint", "https://your-app.example.com/api/chat")

    judge_opts = [(p, "" if have.get(p) else "no credential") for p in sorted(PROVIDERS) if p != "http"]
    judge_opts.append(("none", "skip — cases using `kind: judge` cannot run"))
    # A judge from a different family than the target is the whole point.
    jdefault = next(
        (i for i, (p, _) in enumerate(judge_opts) if p != provider and have.get(p)),
        len(judge_opts) - 1,
    )
    judge = _choose("Judge model — grades `kind: judge` rubrics", judge_opts, jdefault)
    judge_model = ""
    if judge != "none":
        print()
        judge_model = _ask(f"model for {judge}", _DEFAULT_MODELS.get(judge, ""))

    policy = _choose(
        "System prompt — what is the assistant told to be?",
        [
            ("template", "write policies/general.md as policy.md and fill it in"),
            ("inline", "leave a placeholder in target.yaml to paste your own"),
        ],
    )

    lines = [
        f"name: {name}",
        "",
        "provider:",
        f"  name: {provider}",
        f"  model: {model}",
    ]
    if url:
        lines += [
            f"  url: {url}",
            "  body:",
            '    messages: "{{messages}}"',
            '    system: "{{system}}"',
            "  response_path: reply          # dotted path to the reply text",
        ]
    lines += [
        f"  temperature: {temperature}",
        "  max_tokens: 2048",
        "",
        "concurrency: 4",
        "",
    ]

    wrote_policy = False
    if policy == "template":
        root = _asset_root()
        src = (root / "policies/general.md") if root else None
        dst = dest_root / "policy.md"
        if src and src.is_file() and (force or not dst.exists()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            wrote_policy = True
        lines += ["# Fill in the <PLACEHOLDERS> in policy.md before running.", "system_file: policy.md", ""]
    else:
        lines += [
            "system: |",
            "  <paste the system prompt you actually deploy>",
            "",
        ]

    if judge != "none":
        lines += [
            "# A different model family than the target on purpose — a model is a",
            "# poor judge of its own blind spots.",
            "judge:",
            f"  name: {judge}",
            f"  model: {judge_model}",
            "  temperature: 0.0",
            "  max_tokens: 512",
            "",
        ]

    target_path = dest_root / "target.yaml"
    if target_path.exists() and not force:
        print(
            _c(f"\n\033[31mRefusing to overwrite {target_path}.\033[0m Re-run with --force."),
            file=sys.stderr,
        )
        return None
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(lines), encoding="utf-8")

    summary = "target.yaml" + (" + policy.md" if wrote_policy else "")
    if judge == "none":
        summary += "  (no judge — `kind: judge` cases will be refused at run time)"
    return summary


def cmd_init(args: argparse.Namespace) -> int:
    """Write the bundled suites and an example target config into a directory."""
    root = _asset_root()
    if root is None:
        print(
            _c(
                "\033[31mError:\033[0m bundled suites not found. This is a packaging "
                "bug — please report it at "
                "https://github.com/mobius29er/halligan/issues"
            ),
            file=sys.stderr,
        )
        return EXIT_CONFIG

    dest_root = Path(args.dir)
    written: list[str] = []
    skipped: list[str] = []

    interactive = getattr(args, "interactive", False)
    if interactive and not sys.stdin.isatty():
        print(
            _c(
                "\033[31mError:\033[0m --interactive needs a terminal, and stdin is not "
                "a TTY.\n  Drop the flag to write the example config non-interactively."
            ),
            file=sys.stderr,
        )
        return EXIT_CONFIG

    # Interactive mode writes its own target.yaml, so don't also copy the example.
    assets = (
        tuple(a for a in _INIT_ASSETS if a[1] != "target.yaml") if interactive else _INIT_ASSETS
    )

    for src_rel, dst_rel in assets:
        src = root / src_rel
        dst = dest_root / dst_rel
        if not src.exists():
            continue
        if dst.exists() and not args.force:
            skipped.append(dst_rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        written.append(dst_rel)

    for path in written:
        print(_c(f"  \033[32m+\033[0m {path}"))
    for path in skipped:
        print(_c(f"  \033[90m·\033[0m {path} {DIM}exists, left alone (--force to overwrite){RESET}"))

    if interactive:
        summary = _interactive_target(dest_root, args.force)
        if summary is None:
            return EXIT_CONFIG
        print(_c(f"\n  \033[32m+\033[0m {summary}"))
        print(
            _c(
                f"\n{BOLD}Next{RESET}\n"
                f"  1. {BOLD}halligan doctor{RESET} — confirm the credential is visible.\n"
                f"  2. {BOLD}halligan validate --suite suites/{RESET} — no API calls.\n"
                f"  3. {BOLD}halligan run -t target.yaml -s suites/ --report report.html{RESET}\n"
            )
        )
        return EXIT_OK

    if not written and skipped:
        print(_c(f"\n{DIM}Nothing written — everything was already there.{RESET}\n"))
        return EXIT_OK

    print(
        _c(
            f"\n{BOLD}Next{RESET}\n"
            f"  1. Edit {BOLD}target.yaml{RESET} — pick a provider and paste in the "
            f"system prompt you actually ship.\n"
            f"  2. {BOLD}halligan doctor{RESET} to confirm the credential is visible.\n"
            f"  3. {BOLD}halligan run -t target.yaml -s suites/{RESET}\n"
        )
    )
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
    p_run.add_argument(
        "--estimate",
        action="store_true",
        help=(
            "print how many calls and tokens this run would cost, then exit "
            "without calling anything"
        ),
    )
    p_run.add_argument(
        "--reply-tokens",
        type=int,
        default=DEFAULT_REPLY_TOKENS,
        metavar="N",
        help=(
            f"assumed length of a model reply, for --estimate only "
            f"(default {DEFAULT_REPLY_TOKENS})"
        ),
    )
    p_run.add_argument(
        "--price-in", type=float, metavar="USD", help="input rate per million tokens"
    )
    p_run.add_argument(
        "--price-out", type=float, metavar="USD", help="output rate per million tokens"
    )
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="list suites and cases")
    p_list.add_argument("--suite", "-s", action="append", required=True)
    p_list.set_defaults(func=cmd_list)

    p_val = sub.add_parser("validate", help="check suite files without calling any API")
    p_val.add_argument("--suite", "-s", action="append", required=True)
    p_val.set_defaults(func=cmd_validate)

    p_init = sub.add_parser(
        "init",
        help="write the bundled suites and an example target config into a directory",
    )
    p_init.add_argument("--dir", default=".", help="where to write (default: current directory)")
    p_init.add_argument(
        "--force", action="store_true", help="overwrite files that already exist"
    )
    p_init.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help=(
            "pick provider, model, judge and system prompt from menus instead of "
            "editing target.yaml by hand"
        ),
    )
    p_init.set_defaults(func=cmd_init)

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
