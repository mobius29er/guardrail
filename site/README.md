# halligan.dev

The landing page and sample report, deployed as a Cloudflare Worker serving
static assets. There is no Worker script — `wrangler.jsonc` has no `main`, so
every request is served straight from Cloudflare's asset storage.

```
site/
  wrangler.jsonc         Worker config (assets-only)
  gen_demo_report.py     Regenerates public/report.html
  public/
    index.html           The landing page
    report.html          Generated — do not hand-edit
    404.html
```

## Develop

```bash
cd site
npm install
npm run dev          # http://localhost:8787
```

## Automatic deploys (Cloudflare Workers Builds)

The Worker is connected to this repository, so a push to `main` deploys it.
That path stores no credential anywhere — Cloudflare pulls from GitHub, rather
than CI holding an API token.

The settings live in the Cloudflare dashboard, not in this repo, so they are
recorded here:

| Field | Value | Why |
|---|---|---|
| Root directory | `/site` | `wrangler.jsonc` and `package.json` are here, not at the repo root |
| Build command | *(empty)* | Static assets — nothing to compile. Dependencies install from the committed lockfile automatically |
| Deploy command | `npx wrangler deploy` | |
| Version command | `npx wrangler versions upload` | |

Two things that have already broken this once:

- **A non-empty build command.** It was once set to `/`, which is not a
  command; every build failed instantly, before the root directory was even
  consulted. If builds fail with no useful output, check this field first.
- **Changing a setting does not replay a failed build.** Cloudflare re-runs
  with the configuration captured at trigger time, so a settings fix needs a
  new push to take effect.

## Deploy manually

Prefer the automatic path above. This is for a hotfix or a dry run:

```bash
cd site
npm run check        # dry run, validates config without deploying
npm run deploy
```

## The sample report

`public/report.html` is generated, not written by hand:

```bash
cd site
npm run report       # or: python gen_demo_report.py
```

The outcomes in it are authored — no API credits are spent to produce it — but
everything else is real. The cases are loaded from `../suites/`, graded through
the real `CaseGroup`/`RunResult` types, and rendered by
`halligan.report.to_html`, the same code path `halligan run --report` uses. A
banner is injected at the top so nobody mistakes it for a live measurement.

Regenerate it after any change to the report renderer or the suites — the
generator fails loudly if the renderer's output no longer contains the anchor it
injects the banner into, rather than silently publishing an unlabelled report.
