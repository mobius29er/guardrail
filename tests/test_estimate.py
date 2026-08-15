"""Cost estimation. No network — every number is derived from text we have."""

from __future__ import annotations

from halligan.estimate import DEFAULT_REPLY_TOKENS, estimate_run, tokens
from halligan.models import Case, Check


def _case(cid: str, turns: list[str], checks: list[Check] | None = None) -> Case:
    return Case(id=cid, turns=turns, checks=checks or [Check(kind="refuses")])


class TestCallCounts:
    """Counts are exact, not estimated — one call per turn, one per judge check."""

    def test_one_target_call_per_turn(self):
        est = estimate_run([_case("c", ["a", "b", "c"])])
        assert est.target_calls == 3

    def test_one_judge_call_per_judge_check(self):
        case = _case(
            "c",
            ["a"],
            [Check(kind="refuses"), Check(kind="judge", params={"rubric": "r"}),
             Check(kind="judge", params={"rubric": "r2"})],
        )
        est = estimate_run([case])
        assert est.judge_calls == 2
        assert est.target_calls == 1

    def test_keyword_only_cases_need_no_judge_calls(self):
        assert estimate_run([_case("c", ["a"])]).judge_calls == 0

    def test_repeat_multiplies_everything(self):
        one = estimate_run([_case("c", ["a", "b"])])
        twenty = estimate_run([_case("c", ["a", "b"])], repeat=20)
        assert twenty.target_calls == one.target_calls * 20
        assert twenty.input_tokens == one.input_tokens * 20
        assert twenty.cases == 20


class TestConversationGrowth:
    """The point of the module: multi-turn cost is quadratic, not linear.

    Every turn resends the whole conversation so far, so a six-turn ladder
    costs far more than six single-turn probes. Getting this wrong is what
    makes people think --repeat 20 is affordable when it isn't.
    """

    def test_multi_turn_costs_more_than_the_same_turns_split_up(self):
        turn = "x" * 400
        together = estimate_run([_case("c", [turn] * 4)])
        apart = estimate_run([_case(f"c{i}", [turn]) for i in range(4)])
        assert together.target_calls == apart.target_calls == 4
        assert together.target_input > apart.target_input * 2

    def test_input_grows_triangularly_in_turn_count(self):
        turn = "x" * 400
        two = estimate_run([_case("c", [turn] * 2)]).target_input
        four = estimate_run([_case("c", [turn] * 4)]).target_input
        # Doubling the turns more than doubles the input.
        assert four > two * 2

    def test_system_prompt_is_resent_every_turn(self):
        long_system = "s" * 8000
        without = estimate_run([_case("c", ["hi"] * 3)]).target_input
        with_sys = estimate_run([_case("c", ["hi"] * 3)], system=long_system).target_input
        assert with_sys - without == tokens(long_system) * 3

    def test_case_system_overrides_the_target_system(self):
        case = _case("c", ["hi"])
        case.system = "short"
        est = estimate_run([case], system="s" * 8000)
        assert est.target_input < tokens("s" * 8000)


class TestCost:
    def test_no_rates_means_no_number(self):
        """Better to say nothing than to invent a price that goes stale."""
        assert estimate_run([_case("c", ["a"])]).cost(None, None) is None
        assert estimate_run([_case("c", ["a"])]).cost(3.0, None) is None

    def test_cost_uses_both_rates(self):
        est = estimate_run([_case("c", ["a"])])
        expected = (est.input_tokens / 1e6) * 3.0 + (est.output_tokens / 1e6) * 15.0
        assert est.cost(3.0, 15.0) == expected

    def test_reply_length_assumption_is_tunable(self):
        cheap = estimate_run([_case("c", ["a"])], reply_tokens=50)
        rich = estimate_run([_case("c", ["a"])], reply_tokens=2000)
        assert rich.output_tokens > cheap.output_tokens
        assert cheap.reply_tokens == 50
        assert estimate_run([_case("c", ["a"])]).reply_tokens == DEFAULT_REPLY_TOKENS


class TestEmptyAndEdges:
    def test_no_cases_costs_nothing(self):
        est = estimate_run([])
        assert est.calls == 0 and est.input_tokens == 0 and est.cost(3.0, 15.0) == 0.0

    def test_tokens_never_returns_zero_for_real_text(self):
        assert tokens("") == 0
        assert tokens(None) == 0
        assert tokens("a") == 1
