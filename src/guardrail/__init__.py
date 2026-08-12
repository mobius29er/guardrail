"""Guardrail — automated guardrail testing for AI assistants.

Fire a suite of adversarial probes at any model (Anthropic, OpenAI, Gemini,
Ollama, or your own HTTP endpoint), grade the responses against declarative
checks, and fail the build when a guardrail moves.

    from guardrail.config import TargetConfig, load_suites
    from guardrail.runner import run_suites

    target = TargetConfig.load("target.yaml")
    suites = load_suites(["suites/"])
    result = await run_suites(target, suites)
    print(result.score)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
