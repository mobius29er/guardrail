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

## Deploy

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
