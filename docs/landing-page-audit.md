# Landing page conversion audit — what was applied, and what was refused

A conversion audit of halligan.dev scored it 62/100 against a 100-criterion
rubric and raised six issues. Three were applied. Three were not, and the
reasons are written down here because the same audit will say the same things
next time and the refusals need to survive that.

## Ground truth at the time of the audit

The repository was **eight days old**: 0 stars, 0 forks, 0 watchers, 3
contributors (one human, two co-author trailers). No users. No downloads worth
citing. Two of the six findings assumed a customer base that does not exist.

## Applied

**Accessibility — skip link and landmarks.** Every page now opens with a
`.skip` link, wraps its nav in `<header class="site">` for a banner landmark,
and wraps its content in `<main id="main">`. Real WCAG 2.4.1 and 1.3.1, no
judgment involved.

One trap worth knowing: the sticky positioning had to move from `nav` to
`header.site`. A sticky child only sticks within its parent's box, so a
wrapper the same height as the nav would have unstuck the bar the moment it
scrolled past. The page-title `<header>` on the interior pages is now inside
`<main>`, which is what stops it being a second banner — so `.legal header`
became `.legal main>header`, and the same rule in about/start's own `<style>`
became `main>header`.

**FAQ.** Six questions in `#faq`, every answer already true and already in the
README: provider support, self-hosting (none), cost and `--estimate`, CI exit
codes, the `http` provider, and domain-neutrality. Plain `<h3>`/`<p>` rather
than `<details>` — an objection you have to click to read stays unanswered,
and a collapsed answer is invisible to search.

**CTA.** "Get started" became "Run your first test". The audit's suggested
microcopy — `Open source · Apache-2.0 · No account required` — was not used
verbatim: the hero badge directly above already says Open source · Apache-2.0,
and the install bar directly below already shows `pip install halligan`. The
line that shipped says the one thing neither of them does, which is that
nothing is hosted and nothing reports back.

## Refused

**Three to five named testimonials, and "surface the one you have".** There
are none to surface. The "1 testimonial" the scraper counted is the
`<p class="pull">` in `#why` — a pull quote of the project's own prose, styled
with a left border. The scraper matched on quote-shaped markup.

Writing them would mean inventing them, and the audit's own example is a
placeholder ("Jane Smith, AI Safety Lead at Acme"). Two reasons that is not a
close call:

- Fabricated endorsements violate FTC 16 CFR Part 255.
- Halligan's entire claim is that it catches AI systems saying things that do
  not hold up. A fake testimonial on the landing page of a truthfulness
  harness is a loaded weapon pointed backwards. One reader checking whether
  "Acme" is real costs more than the testimonials could ever return.

The fix is to ship, get users, and ask five of them for one sentence each.
**When that happens, this is the section to add** — and it will score far
better than anything that could have been written today.

**Urgency: "join 47 teams", "312 ⭐".** Both numbers would be invented; the
real star count is 0, and a badge showing 0 is worse than no badge. Nothing
shipped here. If early-stage framing is wanted later, the honest form is the
version number, which is true.

**INP over 200ms, blamed on "the animated terminal component".** There is no
animated terminal. The hero terminal is a static `<pre>` of coloured spans and
the only script on the page is a fifteen-line clipboard handler — there is no
mechanism that produces 200ms of interaction latency. INP needs a real
interaction to measure and lab tools often substitute a TBT-derived proxy;
that is the likelier explanation. Measure before optimising something that
does not exist. The one thing genuinely worth a look is
`backdrop-filter: blur(12px)` on the sticky nav, which can cost on weak GPUs
— but that is scroll jank, not INP.

## On the score

The rubric is general-purpose SaaS landing-page scoring pointed at a developer
tool. It marks Trust 28/100 for missing customer logos and testimonials while
the signals developers actually use — readable source, green CI, a permissive
licence, and a real sample of the output — are all present and all scored
elsewhere or not at all.

The three applied fixes should move the number. The heaviest-weighted item can
only be cleared by lying or by waiting. Do not optimise toward the score past
that point.
