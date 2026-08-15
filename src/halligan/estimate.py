"""Predicting what a run costs before it spends anything.

A full suite at ``--repeat 20`` is thousands of API calls, and finding that out
from a bill is the wrong way to find it out.

Most of these numbers are measurements, not guesses. The call counts are exact:
one target call per turn, one judge call per ``kind: judge`` check. The input
tokens are computed from text that already exists — the system prompt in the
target config and the turns in the suites. Only the reply length is assumed,
because it hasn't happened yet.

The one thing worth internalising: **a multi-turn case is not N times a
single-turn case.** Every turn resends the whole conversation so far, so input
tokens grow with the square of the turn count. A six-turn ladder costs roughly
twenty-one turns' worth of input, not six.
"""

from __future__ import annotations

from dataclasses import dataclass

from halligan.models import Case

#: Rough across the GPT/Claude/Gemini tokenizers for English prose. Good enough
#: for a number whose job is to prevent a surprise, not to reconcile an invoice.
CHARS_PER_TOKEN = 4

#: Assumed reply length. The only genuinely unknown quantity here.
DEFAULT_REPLY_TOKENS = 400

#: The judge grader truncates both the conversation and the response it is
#: shown. Mirrored here so long transcripts don't inflate the estimate.
_JUDGE_CAP_TOKENS = 12_000 // CHARS_PER_TOKEN

#: JUDGE_PROMPT's fixed scaffolding, and its deliberately tiny two-line reply.
_JUDGE_TEMPLATE_TOKENS = 120
_JUDGE_REPLY_TOKENS = 40


def tokens(text: str | None) -> int:
    return 0 if not text else max(1, len(text) // CHARS_PER_TOKEN)


@dataclass
class Estimate:
    """What a run will cost, before it runs."""

    cases: int
    target_calls: int
    judge_calls: int
    target_input: int
    target_output: int
    judge_input: int
    judge_output: int
    reply_tokens: int

    @property
    def calls(self) -> int:
        return self.target_calls + self.judge_calls

    @property
    def input_tokens(self) -> int:
        return self.target_input + self.judge_input

    @property
    def output_tokens(self) -> int:
        return self.target_output + self.judge_output

    def cost(self, price_in: float | None, price_out: float | None) -> float | None:
        """Dollars, given per-million-token rates. None when rates are unknown.

        No price table ships with Halligan on purpose: a hardcoded rate goes
        stale silently and then lies with authority. Rates come from the target
        config or the command line, where they are the user's to keep current.
        """
        if price_in is None or price_out is None:
            return None
        return (self.input_tokens / 1e6) * price_in + (self.output_tokens / 1e6) * price_out


def estimate_run(
    cases: list[Case],
    *,
    system: str | None = None,
    repeat: int = 1,
    reply_tokens: int = DEFAULT_REPLY_TOKENS,
) -> Estimate:
    """Cost of running ``cases`` ``repeat`` times.

    ``system`` is the target-level system prompt, used for any case that does
    not override it — it is resent on every single call, so for a short probe
    against a long system prompt it dominates everything else.
    """
    target_calls = judge_calls = 0
    target_input = target_output = 0
    judge_input = judge_output = 0

    for case in cases:
        case_system = case.system if case.system is not None else system
        s = tokens(case_system)
        turn_tokens = [tokens(t) for t in case.turns]
        n = len(turn_tokens)

        target_calls += n
        target_output += n * reply_tokens

        # Call i resends the system prompt, every turn up to i, and every reply
        # before i. Hence the triangular growth.
        running = s
        for t in turn_tokens:
            running += t
            target_input += running
            running += reply_tokens

        judged = [c for c in case.checks if c.kind == "judge"]
        judge_calls += len(judged)
        if judged:
            # The judge sees the conversation without the system prompt.
            conversation = min(sum(turn_tokens) + n * reply_tokens, _JUDGE_CAP_TOKENS)
            graded = min(reply_tokens, _JUDGE_CAP_TOKENS)
            for check in judged:
                rubric = tokens(str(check.params.get("rubric", "")))
                judge_input += _JUDGE_TEMPLATE_TOKENS + rubric + conversation + graded
                judge_output += _JUDGE_REPLY_TOKENS

    return Estimate(
        cases=len(cases) * repeat,
        target_calls=target_calls * repeat,
        judge_calls=judge_calls * repeat,
        target_input=target_input * repeat,
        target_output=target_output * repeat,
        judge_input=judge_input * repeat,
        judge_output=judge_output * repeat,
        reply_tokens=reply_tokens,
    )
