# Experiments

Reproducible rigs for measuring the harness against itself. Not shipped in the
package — these are for answering questions about Halligan, not for testing your
assistant.

## `arm-a.yaml` / `arm-b.yaml` / `arm-c.yaml` — does the policy do anything?

Three targets identical in every respect except the system prompt:

| Arm | System prompt | Represents |
|---|---|---|
| A | `policies/catholic.md`, 3,693 chars | What the project ships |
| B | 148 chars, identity only | What a developer writes in thirty seconds |
| C | none | The floor |

Same model, same suite, same judge, same temperature. Whatever gap opens
between the arms is attributable to the prompt and to nothing else — the same
matched-arm logic `consistency.yaml` uses on sympathy vs neutral pressure,
turned on the policy itself.

```bash
export OPENAI_API_KEY=lm-studio      # LM Studio ignores the value
for arm in a b c; do
  halligan run -t experiments/arm-$arm.yaml -s suites/consistency.yaml \
    --repeat 3 --json results-$arm.json
done
```

Read the deterministic failures separately from the judge ones. A small judge
gets subtle structural calls wrong — a 4B model scored two correct refusals as
critical failures in an earlier run — and it is held constant across arms
precisely so its errors partly cancel in the comparison rather than deciding it.

Interpretation fixed before the first run, so the result could not be
rationalised afterwards:

- **A ≫ C but A ≈ B** — the identity line does the work; the long policy is decorative
- **A > B > C** — the policy earns its length
- **all similar** — the model is well-behaved regardless and this suite cannot measure the prompt
