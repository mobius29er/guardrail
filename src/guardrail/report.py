"""Rendering run results — terminal, JSON, Markdown, HTML.

Everything written or printed passes through :func:`redact` first. Run
artifacts contain full prompts and full model responses, and a model can echo
back anything that was in its context, so treating output as untrusted is
cheaper than being surprised.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from guardrail.models import Outcome, RunResult, Severity

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    # Provider-specific formats first, so they win over the generic rules.
    (r"sk-ant-[A-Za-z0-9\-_]{20,}", "[REDACTED:anthropic-key]"),
    (r"sk-proj-[A-Za-z0-9\-_]{20,}", "[REDACTED:openai-key]"),
    (r"sk-[A-Za-z0-9]{32,}", "[REDACTED:openai-key]"),
    (r"AIza[A-Za-z0-9\-_]{35}", "[REDACTED:google-key]"),
    (r"ghp_[A-Za-z0-9]{36}", "[REDACTED:github-token]"),
    (r"github_pat_[A-Za-z0-9_]{50,}", "[REDACTED:github-token]"),
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "[REDACTED:slack-token]"),
    (r"AKIA[0-9A-Z]{16}", "[REDACTED:aws-key-id]"),
    (r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*", "[REDACTED:bearer-token]"),
    # Key-shaped assignments: api_key = "...", "token": "...", etc.
    (
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9\-._~+/]{16,}=*[\"']?",
        r"\1=[REDACTED]",
    ),
    # PEM blocks.
    (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED:private-key]",
    ),
)

_COMPILED = tuple(
    (re.compile(pattern, re.DOTALL), replacement) for pattern, replacement in _SECRET_PATTERNS
)


def redact(text: str) -> str:
    """Mask anything that looks like a credential.

    Conservative by design: a false positive costs a mangled report line, a
    false negative costs a leaked key.
    """
    if not text:
        return text
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text


def _redact_tree(node: Any) -> Any:
    if isinstance(node, str):
        return redact(node)
    if isinstance(node, dict):
        return {k: _redact_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_tree(v) for v in node]
    return node


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def to_dict(run: RunResult, *, include_transcripts: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target": run.target_name,
        "model": run.model,
        "started_at": run.started_at,
        "duration_s": run.duration_s,
        "score": run.score,
        "repeat": run.repeat,
        "summary": {
            "total": len(run.results),
            "passed": len(run.passed),
            "failed": len(run.failed),
            "errored": len(run.errored),
            "flaky": len(run.flaky),
            "critical_failure": run.has_critical_failure(),
        },
        "cases": [],
    }

    for group in run.results:
        entry: dict[str, Any] = {
            "id": group.case.id,
            "family": group.case.family,
            "severity": group.case.severity.value,
            "description": group.case.description,
            "outcome": group.outcome.value,
            "latency_s": group.latency_s,
            "repeat": len(group.runs),
            "flake_rate": round(group.flake_rate, 3),
            "is_flaky": group.is_flaky,
            "runs_failed": len(group.failures),
            "runs_graded": len(group.graded),
            "runs_errored": len(group.errors),
            "runs": [],
        }

        for result in group.runs:
            run_entry: dict[str, Any] = {
                "run_index": result.run_index,
                "outcome": result.outcome.value,
                "latency_s": result.latency_s,
                "checks": [
                    {
                        "kind": c.kind,
                        "outcome": c.outcome.value,
                        "reason": c.reason,
                        "description": c.description,
                    }
                    for c in result.checks
                ],
            }
            if result.error:
                run_entry["error"] = result.error
            if include_transcripts:
                run_entry["transcript"] = [
                    {"role": m.role, "content": m.content} for m in result.transcript
                ]
            entry["runs"].append(run_entry)

        payload["cases"].append(entry)

    return _redact_tree(payload)


def write_json(run: RunResult, path: str | Path, *, include_transcripts: bool = True) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(to_dict(run, include_transcripts=include_transcripts), indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_ICON = {
    Outcome.PASS: "✅",
    Outcome.FAIL: "❌",
    Outcome.ERROR: "⚠️",
    Outcome.SKIP: "⏭️",
}


def to_markdown(run: RunResult) -> str:
    lines: list[str] = [
        f"# Guardrail Report — {run.target_name}",
        "",
        f"- **Model:** `{run.model}`",
        f"- **Run:** {run.started_at} ({run.duration_s}s)",
        f"- **Score:** **{run.score}%** (severity-weighted)",
        f"- **Results:** {len(run.passed)} passed / {len(run.failed)} failed"
        f" / {len(run.errored)} errored",
        "",
    ]

    if run.repeat > 1:
        lines[-1:] = [
            f"- **Repeats:** {run.repeat}× per case — a case fails if *any* run fails",
            "",
        ]

    if run.has_critical_failure():
        lines += [
            "> ⛔ **CRITICAL FAILURE** — at least one critical-severity case did not pass.",
            "",
        ]

    if run.flaky:
        lines += [
            f"> 🎲 **{len(run.flaky)} FLAKY** — these cases both passed and failed "
            "across repeats. An intermittent guardrail is not a guardrail.",
            "",
        ]

    by_family: dict[str, list[Any]] = {}
    for group in run.results:
        by_family.setdefault(group.case.family, []).append(group)

    lines += [
        "## Summary by family",
        "",
        "| Family | Passed | Failed | Errored | Flaky |",
        "|---|---:|---:|---:|---:|",
    ]
    for family in sorted(by_family):
        rows = by_family[family]
        lines.append(
            f"| `{family}` "
            f"| {sum(1 for r in rows if r.outcome is Outcome.PASS)} "
            f"| {sum(1 for r in rows if r.outcome is Outcome.FAIL)} "
            f"| {sum(1 for r in rows if r.outcome is Outcome.ERROR)} "
            f"| {sum(1 for r in rows if r.is_flaky)} |"
        )
    lines.append("")

    if run.flaky:
        lines += [
            "## Flaky cases",
            "",
            "Ranked by how often the guardrail moved. These are the most important "
            "rows in the report: a case that fails 3 times in 10 looks like a clean "
            "pass or a clean fail depending on which single run you happened to see.",
            "",
            "| Case | Severity | Failed | Rate |",
            "|---|---|---:|---:|",
        ]
        for group in sorted(run.flaky, key=lambda g: -g.flake_rate):
            lines.append(
                f"| `{group.case.id}` | {group.case.severity.value} "
                f"| {len(group.failures)}/{len(group.graded)} "
                f"| {group.flake_rate:.0%} |"
            )
        lines.append("")

    failures = run.failed + run.errored
    if failures:
        lines += ["## Failures", ""]
        for group in sorted(failures, key=lambda g: -g.case.severity.weight):
            heading = f"### {_ICON[group.outcome]} `{group.case.id}` ({group.case.severity.value})"
            if len(group.runs) > 1:
                heading += f" — failed {len(group.failures)}/{len(group.graded)} runs"
            lines += [heading, ""]

            if group.case.description:
                lines += [redact(group.case.description), ""]

            result = group.representative
            if result is None:
                continue
            if result.error:
                lines += [f"**Error:** {redact(result.error)}", ""]
            for check in result.failed_checks:
                lines.append(f"- **`{check.kind}`** — {redact(check.reason)}")
            lines.append("")

            final = result.assistant_turns[-1] if result.assistant_turns else ""
            if final:
                excerpt = redact(final[:1200])
                label = "Final response"
                if len(group.runs) > 1:
                    label += f" (run {result.run_index + 1})"
                lines += [
                    f"<details><summary>{label}</summary>",
                    "",
                    "```text",
                    excerpt + ("\n... (truncated)" if len(final) > 1200 else ""),
                    "```",
                    "",
                    "</details>",
                    "",
                ]
    else:
        lines += ["## Failures", "", "None — every case passed. 🎉", ""]

    return "\n".join(lines)


def write_markdown(run: RunResult, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_markdown(run), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #666; --line: #e2e2e2;
  --card: #fafafa; --pass: #1a7f37; --fail: #cf222e; --warn: #9a6700;
  --accent: #0b5fff; --flake: #8250df;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #30363d;
    --card: #161b22; --pass: #3fb950; --fail: #f85149; --warn: #d29922;
    --accent: #58a6ff; --flake: #a371f7;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1rem; background: var(--bg); color: var(--fg);
  font: 16px/1.6 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 .75rem; padding-bottom: .3rem;
     border-bottom: 1px solid var(--line); }
.sub { color: var(--muted); font-size: .9rem; margin-bottom: 1.5rem; }
.score { font-size: 3rem; font-weight: 700; line-height: 1; }
.tiles { display: flex; flex-wrap: wrap; gap: .75rem; margin: 1.25rem 0; }
.tile { flex: 1 1 7rem; background: var(--card); border: 1px solid var(--line);
        border-radius: .5rem; padding: .85rem 1rem; }
.tile .n { font-size: 1.6rem; font-weight: 650; }
.tile .l { color: var(--muted); font-size: .78rem; text-transform: uppercase;
           letter-spacing: .04em; }
.pass { color: var(--pass); } .fail { color: var(--fail); } .warn { color: var(--warn); }
.flake { color: var(--flake); }
.banner { background: var(--fail); color: #fff; padding: .8rem 1rem;
          border-radius: .5rem; font-weight: 600; margin: 1rem 0; }
.banner.flakeb { background: var(--flake); }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { text-align: left; padding: .5rem .7rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; font-size: .8rem;
     text-transform: uppercase; letter-spacing: .04em; }
td.num, th.num { text-align: right; }
.case { background: var(--card); border: 1px solid var(--line);
        border-left: 3px solid var(--fail); border-radius: .5rem;
        padding: 1rem 1.15rem; margin: .85rem 0; }
.case.err { border-left-color: var(--warn); }
.case.flaky { border-left-color: var(--flake); }
.case h3 { margin: 0 0 .3rem; font-size: 1rem; font-family: ui-monospace, monospace; }
.bar { display: inline-block; vertical-align: middle; width: 5.5rem; height: .5rem;
       background: var(--pass); border-radius: 999px; overflow: hidden;
       margin-right: .5rem; }
.bar > i { display: block; height: 100%; background: var(--fail); }
.badge { display: inline-block; font-size: .7rem; font-weight: 700;
         text-transform: uppercase; letter-spacing: .05em; padding: .1rem .45rem;
         border-radius: .25rem; border: 1px solid currentColor; margin-left: .5rem; }
.desc { color: var(--muted); font-size: .9rem; margin: .35rem 0 .6rem; }
ul.checks { margin: .5rem 0; padding-left: 1.1rem; }
ul.checks code { color: var(--fail); }
details { margin-top: .7rem; }
summary { cursor: pointer; color: var(--accent); font-size: .9rem; }
pre { background: var(--bg); border: 1px solid var(--line); border-radius: .4rem;
      padding: .8rem; overflow-x: auto; font-size: .82rem; white-space: pre-wrap;
      word-wrap: break-word; }
footer { margin-top: 3rem; color: var(--muted); font-size: .82rem;
         border-top: 1px solid var(--line); padding-top: 1rem; }
"""


def to_html(run: RunResult) -> str:
    e = html.escape
    score_class = "pass" if run.score >= 90 else "warn" if run.score >= 70 else "fail"

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>Guardrail — {e(run.target_name)}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        "<h1>Guardrail Report</h1>",
        f'<div class="sub">{e(run.target_name)} &middot; '
        f"<code>{e(run.model)}</code> &middot; {e(run.started_at)} &middot; "
        f"{run.duration_s}s</div>",
    ]

    if run.has_critical_failure():
        parts.append(
            '<div class="banner">⛔ CRITICAL FAILURE — at least one '
            "critical-severity case did not pass.</div>"
        )

    if run.flaky:
        parts.append(
            f'<div class="banner flakeb">🎲 {len(run.flaky)} FLAKY — these cases both '
            "passed and failed across repeats. An intermittent guardrail is not a "
            "guardrail.</div>"
        )

    parts += [
        '<div class="tiles">',
        f'<div class="tile"><div class="n score {score_class}">{run.score}%</div>'
        '<div class="l">Score</div></div>',
        f'<div class="tile"><div class="n pass">{len(run.passed)}</div>'
        '<div class="l">Passed</div></div>',
        f'<div class="tile"><div class="n fail">{len(run.failed)}</div>'
        '<div class="l">Failed</div></div>',
        f'<div class="tile"><div class="n warn">{len(run.errored)}</div>'
        '<div class="l">Errored</div></div>',
    ]
    if run.repeat > 1:
        parts.append(
            f'<div class="tile"><div class="n flake">{len(run.flaky)}</div>'
            f'<div class="l">Flaky / {run.repeat}× runs</div></div>'
        )
    parts.append("</div>")

    by_family: dict[str, list[Any]] = {}
    for group in run.results:
        by_family.setdefault(group.case.family, []).append(group)

    parts += [
        "<h2>By family</h2>",
        '<div class="tablewrap"><table><thead><tr><th>Family</th>'
        '<th class="num">Passed</th><th class="num">Failed</th>'
        '<th class="num">Errored</th><th class="num">Flaky</th>'
        "</tr></thead><tbody>",
    ]
    for family in sorted(by_family):
        rows = by_family[family]
        parts.append(
            f"<tr><td><code>{e(family)}</code></td>"
            f'<td class="num pass">{sum(1 for r in rows if r.outcome is Outcome.PASS)}</td>'
            f'<td class="num fail">{sum(1 for r in rows if r.outcome is Outcome.FAIL)}</td>'
            f'<td class="num warn">{sum(1 for r in rows if r.outcome is Outcome.ERROR)}</td>'
            f'<td class="num flake">{sum(1 for r in rows if r.is_flaky)}</td></tr>'
        )
    parts.append("</tbody></table></div>")

    if run.flaky:
        parts += [
            "<h2>Flaky cases</h2>",
            "<p>Ranked by how often the guardrail moved. These are the most important "
            "rows in the report: a case that fails 3 times in 10 looks like a clean pass "
            "or a clean fail depending on which single run you happened to see.</p>",
            '<div class="tablewrap"><table><thead><tr><th>Case</th><th>Severity</th>'
            '<th class="num">Failed</th><th>Rate</th></tr></thead><tbody>',
        ]
        for group in sorted(run.flaky, key=lambda g: -g.flake_rate):
            pct = group.flake_rate
            parts.append(
                f"<tr><td><code>{e(group.case.id)}</code></td>"
                f"<td>{e(group.case.severity.value)}</td>"
                f'<td class="num">{len(group.failures)}/{len(group.graded)}</td>'
                f'<td><span class="bar"><i style="width:{pct:.0%}"></i></span>'
                f"{pct:.0%}</td></tr>"
            )
        parts.append("</tbody></table></div>")

    failures = run.failed + run.errored
    parts.append("<h2>Failures</h2>")
    if not failures:
        parts.append("<p>None — every case passed. 🎉</p>")
    for group in sorted(failures, key=lambda g: -g.case.severity.weight):
        if group.outcome is Outcome.ERROR:
            css = "case err"
        elif group.is_flaky:
            css = "case flaky"
        else:
            css = "case"
        sev_class = "fail" if group.case.severity is Severity.CRITICAL else "warn"

        heading = (
            f"<h3>{e(group.case.id)}"
            f'<span class="badge {sev_class}">{group.case.severity.value}</span>'
        )
        if len(group.runs) > 1:
            heading += (
                f'<span class="badge flake">{len(group.failures)}/'
                f"{len(group.graded)} runs failed</span>"
            )
        heading += "</h3>"
        parts += [f'<div class="{css}">', heading]

        if group.case.description:
            parts.append(f'<div class="desc">{e(redact(group.case.description))}</div>')

        result = group.representative
        if result is None:
            parts.append("</div>")
            continue
        if result.error:
            parts.append(f"<p><strong>Error:</strong> {e(redact(result.error))}</p>")
        if result.failed_checks:
            parts.append('<ul class="checks">')
            for check in result.failed_checks:
                parts.append(f"<li><code>{e(check.kind)}</code> — {e(redact(check.reason))}</li>")
            parts.append("</ul>")

        final = result.assistant_turns[-1] if result.assistant_turns else ""
        if final:
            body = redact(final[:4000])
            if len(final) > 4000:
                body += "\n... (truncated)"
            label = "Final response"
            if len(group.runs) > 1:
                label += f" (run {result.run_index + 1} of {len(group.runs)})"
            parts += [
                f"<details><summary>{label}</summary>",
                f"<pre>{e(body)}</pre></details>",
            ]
        parts.append("</div>")

    parts += [
        "<footer>Generated by "
        '<a href="https://github.com/mobius29er/guardrail">Guardrail</a>. '
        "Credentials in this report are automatically redacted, but transcripts "
        "may still contain sensitive conversation text — review before sharing."
        "</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts)


def write_html(run: RunResult, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_html(run), encoding="utf-8")
