# Site legal, discovery and agent-readable pages

Plan of record for the `/privacy`, `/terms` and `/security` pages plus the
machine-readable files (`robots.txt`, `sitemap.xml`, `llms.txt`,
`.well-known/security.txt`) added to halligan.dev.

## What the privacy policy is allowed to claim

The policy is only worth having if it is true, so every claim below was
verified against the tree rather than assumed. If any of these stop being
true, the policy is wrong and has to change with the code.

| Claim | Verified by |
|---|---|
| No analytics, tag manager or pixel | `site/public/*.html` has one `<script>` — the copy-to-clipboard helper on the landing page |
| No third-party asset requests | no `src=`/`href=` in `site/public` points off-origin except plain outbound links; fonts are the `ui-sans-serif` system stack |
| No cookies, no localStorage | no `document.cookie` / `localStorage` anywhere in `site/public` |
| No forms, no accounts, no payments | no `<form>` element on the site; sponsorship is an outbound link to GitHub/Ko-fi |
| The CLI has no telemetry | `grep -ri "telemetry\|analytics\|posthog\|sentry\|mixpanel" src/` returns nothing |
| Keys never leave the machine | `SECURITY.md` — env-only, never a CLI argument, never written to disk |

What is left is the one thing we cannot honestly deny: Cloudflare terminates
the connection and processes request metadata (IP, user agent, timestamp) as
our hosting provider. The policy says so plainly instead of claiming we
"collect nothing".

## Decisions

- **Contact** — `support@foxxception.com` (general, privacy) and
  `legal@foxxception.com` (terms, trademark, DMCA). Security reports still
  route through GitHub private vulnerability reporting first, because that is
  what `SECURITY.md` already tells people and a second channel that drifts out
  of sync is worse than one.
- **Governing law** — Florida.
- **AI crawlers** — allowed. `robots.txt` allows every agent. Blocking
  training crawlers while shipping an `llms.txt` would be two files arguing
  with each other, and discovery is worth more to an Apache-2.0 project than
  corpus exclusion is.
- **Security page** — `/security` is the web face of `SECURITY.md`, not a
  fork of it. The repo file stays canonical for contributors; the page adds
  the disclosure terms and safe harbour a researcher looks for before they
  report, and links back rather than restating the credential-handling detail.
- **`llms.txt`** — follows the [llmstxt.org](https://llmstxt.org/) spec: H1,
  blockquote summary, then H2 link sections. The `## Optional` section has
  defined meaning in the spec — a crawler working to a tight context budget
  may skip it — so the legal pages live there and the docs do not.

## Shape

Three legal pages, one shape: nav, header, a plain-English summary box, then
numbered sections with a table of contents. Shared rules go in
`site/public/style.css` under `legal pages` rather than being pasted into
three `<style>` blocks, which is the rule that stylesheet's own header sets
out. Page-specific rules stay on the page.

## Open

- `.well-known/security.txt` has an `Expires` field that RFC 9116 requires and
  browsers/scanners honour. It is set one year out and **has to be renewed**;
  an expired `security.txt` reads as an abandoned project.
- ~~Cloudflare's asset uploader has historically skipped dotfiles, which would
  silently drop `.well-known/`.~~ **Resolved.** That behaviour was
  wrangler-legacy and Workers Sites, not Workers Assets. Verified on wrangler
  4.124 with a file-count diff: 21 entries read with `.well-known/` present,
  19 without, and `WRANGLER_LOG=debug` lists `/.well-known/security.txt` in the
  manifest alongside the brand SVGs. Exclusions on Workers Assets are opt-in
  through `.assetsignore`, which this project does not have.
