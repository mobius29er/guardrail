# Policy templates

Starting points for the system prompt of the assistant you're testing. Wire one
in with `system_file:` — the path resolves relative to the target config:

```yaml
name: my-assistant
provider:
  name: anthropic
  model: claude-sonnet-5
system_file: policy.md          # instead of an inline `system:` block
```

`halligan init --interactive` writes one of these out as `policy.md` and sets
that field for you.

| File | For |
|---|---|
| [`general.md`](general.md) | Any domain. Four placeholders to fill in. |

## Filling in `general.md`

The file is loaded **verbatim as the system prompt**, so it deliberately
contains no commentary — only prompt text. Four placeholders need replacing,
and they are written in angle brackets so that forgetting one shows up in the
model's output rather than failing silently:

| Placeholder | Replace with | Examples |
|---|---|---|
| `<ASSISTANT_NAME>` | What the assistant calls itself | `Vera`, `the support assistant` |
| `<DOMAIN>` | The subject area | `home insurance claims`, `Catholic faith and practice` |
| `<CREDENTIALED_ROLE>` | The human you must never replace | `a licensed physician`, `your attorney`, `a priest`, `a licensed financial adviser` |
| `<RESERVED_ACTIONS>` | What only that human may do | `diagnose, prescribe, or interpret test results`; `give jurisdiction-specific legal advice`; `forgive sins` |

## Why these eight things

The template encodes the invariants reverse-engineered in
[`../Truthly/behavioral-profile.md`](../Truthly/behavioral-profile.md), with the
domain specifics lifted out:

1. **Engage, don't flinch** — over-refusal is a failure, not a safe default
2. **The steelman boundary** — state an opposing argument only when the same response answers it
3. **Concede what is true** — about yourself, then keep going
4. **Never substitute for the credentialed human**
5. **Always leave a human door open**
6. **Hold under repetition** — pressure is not new information
7. **Charity under attack**
8. **Accommodate without capitulating**

Each maps to a suite in [`../suites/`](../suites/), which is the point: the
policy and the probes are two halves of the same thing. A policy nobody tests
is a wish.

## Adapting to another domain

The eight invariants transfer directly; only 4, 5 and the reserved actions
change. The shapes generalise:

- A **medical** assistant must never diagnose or prescribe, must refer to a
  clinician, and must hold that line when told no doctor is available.
- A **legal** assistant must not give jurisdiction-specific advice under
  "but you're all I can afford".
- A **financial** assistant must not become a fiduciary under emotional
  pressure.

You will also want to retune the graders that ship with Catholic defaults —
`no_authority_claim`, `no_spiritual_bypass` and `no_procedure` — and pass
`persona: [...]` to `does_not_advocate` so it fires for your domain's opposing
position rather than this one's.

> **This is a starting point, not a compliance artifact.** It was written by
> engineers reasoning about failure modes. Before deploying in a regulated
> domain — medicine, law, finance, or anything touching minors — have the policy
> reviewed by someone qualified in that field and check your obligations under
> the rules that apply to you.
