# Darwix AI — Automated Website & SEO Optimisation

An end-to-end, 5-agent pipeline that crawls every live page on
[darwix.ai](https://darwix.ai), audits it, proposes fixes, runs QA, stages
publish-ready changes, and reports results into a dashboard — implementing
the workflow in `docs/Automated Website & SEO Optimisation.txt`.

## What's real vs. what's staged/stubbed

This runs against the **real, live** darwix.ai site (real HTTP requests,
real HTML parsing) and the **5 agents are real code**, not a mockup. Two
things are intentionally not fully live yet, because the credentials for
them don't exist:

| Thing | Status | Why |
|---|---|---|
| Crawling darwix.ai | **Real** | The site is public; no credentials needed. |
| Keyword clustering, copy, fact-checking | **Real if `OPENAI_API_KEY` or `GROQ_API_KEY` is set**, else a clearly-labelled stub | See "LLM provider" below. |
| Technical SEO QA checks | **Real** | Rule-based, runs against real crawl data either way. |
| Publishing to a live CMS | **Real PR, not a direct live push** | `GITHUB_TOKEN`/`GITHUB_REPO` configured — Agent 4 opens a review PR with the staged diffs (see "GitHub publishing" below). It never writes straight to the content repo's default branch, since this pipeline has no way to know your CMS's real page-template schema. |
| Submitting to Google Search Console | **Real if Search Console credentials are set**, else staged only | See "Google services setup" below. |
| Search Console / PageSpeed / CrUX panels on the dashboard (clicks, impressions, CTR, position, Core Web Vitals, 12-week trend, demo requests, conversion rate) | **Real per-field, wherever that service is configured** — unconfigured fields keep the dashboard's original sample values | See "Google services setup" below. |
| "Top pages" table, "CWV failures" list, Index Coverage breakdown, backlinks/Ahrefs panel | **Sample data** (unchanged) | Out of scope for this pass — no backlink API wired, and CrUX has no reliable per-page breakdown for low-traffic pages. |

Everything else on the dashboard's "Dashboard" tab — technical health,
the action queue, QA pass rates, agent activity, the audit trail — is
**computed by the pipeline from the real crawl**, every run.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # already done for you if you're reading this from a clone with .env present
```

Open `.env` and fill in **one** key — it's loaded automatically on every
run (`pipeline/config.py` calls `load_dotenv()`), so you never need to
`export` anything in your shell. `.env` is gitignored; it never gets
committed.

### LLM provider

The pipeline picks a provider in this order, decided once at startup from
whatever's in `.env` (or your shell environment, which wins if both are set):

1. **OpenAI** — `OPENAI_API_KEY` in `.env`. Real structured-output calls
   (`response_format={"type": "json_schema", "strict": true}`), schema
   enforced server-side. This is the intended production provider.
   Defaults to `gpt-5.1`; override with `OPENAI_MODEL` in `.env` (e.g.
   `gpt-5.1-mini` — see "Estimated API usage per run" below for the cost
   difference).
2. **Groq** — `GROQ_API_KEY` in `.env` instead (leave `OPENAI_API_KEY`
   blank). A free tier for testing, no credit card required — get a key
   at [console.groq.com/keys](https://console.groq.com/keys). Defaults to
   `llama-3.3-70b-versatile`; override with `GROQ_MODEL` in `.env` (e.g.
   `llama-3.1-8b-instant` for a higher daily free-tier quota at lower
   quality). Groq's JSON mode doesn't enforce our schema server-side the
   way OpenAI's does, so each response is validated against the schema's
   required fields and falls back to the stub if it doesn't match — same
   as a rate limit or any other failure.
3. **Neither set** — every LLM-backed field runs as a clearly-labelled
   stub (`_stub: true`, `_stub_note: "..."` in the JSON) so the pipeline
   still runs end to end; only the crawl and rule-based QA are "real" in
   that mode.

`run_pipeline.py` prints which provider is active at the top of every run.

### Estimated API usage per run

Three LLM calls per page, all through `pipeline/llm.py`'s `json_call()`:
Agent 1's content brief, Agent 2's page copy, Agent 3's fact-check. Loop A
(`orchestrator.py`) adds one more Agent 2 + Agent 3 call per page, but only
for a page whose QA failure named an actual fixable field (title, meta
description, h1, etc.) — a structural failure (orphan page, duplicate
content) has nothing for Page Generation to regenerate, so
`orchestrator.py` skips those rather than spending a call on a
regeneration that gets merged into nothing.

For the current site (21 crawled pages): all 21 get a brief + copy +
fact-check call. Right now all 14 first-pass QA failures are structural
(orphan pages / duplicates), so Loop A currently adds **zero** extra
calls — that'll change as soon as a real title/meta-description/h1 issue
shows up.

| Call | Per-call input | Per-call output | Calls this run (21 pages, 0 fixable retries currently) |
|---|---|---|---|
| Agent 1 brief | ~250 tokens (system + brief fields) | ~200 tokens | 21 |
| Agent 2 copy | ~350 tokens (system + full brief + link candidates) | ~250 tokens | 21 |
| Agent 3 fact-check | ~300 tokens (system + brief + proposed copy) | ~30 tokens | 21 |

Totals for a full run against all 21 pages today:
- **63 calls**, **~18.9K input tokens**, **~10K output tokens**.
- At `gpt-5.1` ($1.25/M input, $10/M output): **~$0.12/run**.
- At `gpt-5.1-mini` ($0.25/M input, $2/M output): **~$0.025/run**.

Add roughly **+500 input / +280 output tokens per fixable retry** (one
Agent 2 + one Agent 3 call) once real copy-level QA failures start
showing up — e.g. 5 fixable retries adds ~$0.01-0.02/run at either model.

These are small per-page JSON payloads (a handful of short strings), not
long-form content, which is why the whole run costs cents either way.
`gpt-5.1-mini` is plenty for this — the schema is enforced server-side
regardless of model size, so switching down doesn't risk malformed output,
only slightly less polished copy. Pricing is OpenAI's listed per-token
rate at the time of writing; check
[openai.com/api/pricing](https://openai.com/api/pricing) for current rates
before relying on this for a budget.

### GitHub publishing

Set `GITHUB_TOKEN` (a PAT with Contents + Pull-requests read/write on the
target repo), `GITHUB_REPO` ("owner/repo") and `GITHUB_DEFAULT_BRANCH` in
`.env`. On every run, Agent 4 (`pipeline/github_publisher.py`):

1. Creates (or reuses) a branch `seo-automation/<today>` off `GITHUB_DEFAULT_BRANCH`.
2. Pushes that run's staged diffs plus `sitemap.xml` and `redirect_map.json`
   under `GITHUB_CONTENT_PREFIX` (default `seo-automation/publish_package/`).
3. Opens a PR against `GITHUB_DEFAULT_BRANCH` (or reuses the one already
   open for that branch).

It deliberately never writes to the real page files in your CMS/site
repo — this pipeline has no way to know their frontmatter/template schema.
The PR is the handoff point: review the diffs and merge into your actual
content by hand (or wire a second, schema-aware step that reads the PR's
files and applies them — `agent4_publishing.run()` is where that would plug in).

Unset -> Agent 4 falls back to local-only staging in `data/publish_package/`,
same as before this was wired up.

### Google services setup

Four independent integrations (`pipeline/google_services.py`); each is
gated on its own env vars, so any subset works without the others. A
service you leave unconfigured just means that one dashboard panel stays
on its original sample value — nothing breaks.

**1. PageSpeed Insights** — real Core Web Vitals field data (used if CrUX
below isn't set) and lab performance scores.
- In Google Cloud Console, enable the **PageSpeed Insights API** on a
  project, then create an API key (APIs & Services > Credentials).
- Set `GOOGLE_PAGESPEED_API_KEY` in `.env`.

**2. Chrome UX Report (CrUX)** — real per-metric p75 + good/needs-improvement/poor
split for the Core Web Vitals panel. Preferred over PSI's field data when both are set.
- Enable the **Chrome UX Report API** on the same (or a separate) GCP
  project; create/reuse an API key.
- Set `GOOGLE_CRUX_API_KEY` in `.env`.
- Note: CrUX returns no data for an origin/URL without enough real-user
  traffic in the last 28 days — that's a normal 404, not a bug, and this
  pipeline falls back to the sample value when it happens.

**3. Search Console** — real organic clicks/impressions/CTR/avg position,
the 12-week trend, and real sitemap submission.
- A GCP service-account key already exists at
  `config/darwix-seo-automation-79ade8c5ccff.json` (gitignored —
  `config/*.json` is excluded). If you're setting this up fresh elsewhere,
  create one in IAM & Admin > Service Accounts > Keys > Add key (JSON),
  and enable the **Search Console API** on that project.
- In **Search Console** (search.google.com/search-console) for the
  darwix.ai property > Settings > Users and permissions > Add user >
  paste the service account's `client_email` (from the key file) > Restricted (read) is enough for reporting; use Full if you also want
  this pipeline's sitemap-submit call to work.
- Set in `.env`:
  - `GOOGLE_SERVICE_ACCOUNT_FILE=config/darwix-seo-automation-79ade8c5ccff.json`
  - `GSC_SITE_URL=sc-domain:darwix.ai` (use the exact property string
    Search Console shows you — a Domain property looks like that; a
    URL-prefix property is the full `https://darwix.ai/`).

**4. GA4 Data API** — real demo-request count, conversion rate, and the
funnel panel (organic sessions / CTA clicks / form starts / demo requests).
- Enable the **Google Analytics Data API** on the same GCP project.
- In GA4, Admin > Property Access Management > Add user > paste the same
  service account `client_email` > role **Viewer**.
- Set `GA4_PROPERTY_ID` in `.env` (numeric id, Admin > Property details).
- The funnel's CTA-click/form-start/demo-request numbers depend on
  darwix.ai's GA4 property already tracking custom events with those
  names. If your actual event names differ, override
  `GA4_EVENT_CTA_CLICK` / `GA4_EVENT_FORM_START` / `GA4_EVENT_DEMO_REQUEST`
  in `.env` to match (check GA4 Admin > Events for the real names).

Once any of these are set, `run_pipeline.py` prints which ones are active
at the top of the run, same as the LLM provider line.

## Run it

```bash
python run_pipeline.py
```

This crawls every URL in `https://darwix.ai/sitemap.xml`, runs all 5
agents in order, and regenerates `dashboard/optimization-dashboard.html`
with the real results. Open that file in a browser and click the
**Dashboard** tab.

Useful flags:

```bash
python run_pipeline.py --urls https://darwix.ai https://darwix.ai/sherpa  # just these pages
python run_pipeline.py --no-dashboard                                     # skip the HTML rewrite
python run_pipeline.py --quiet
```

Re-running is safe and cheap — every agent reads/writes through the
shared JSON stores in `data/`, so you can re-run the whole pipeline or
call one agent's `run()` directly from a Python shell.

## The 5 agents

Matches `docs/Optimization Guide (2).html`'s own Agents/Architecture tabs:

1. **SEO Research** (`pipeline/agent1_research.py`) — keyword clusters,
   search intent, the keyword-to-URL map, the sitemap tree, a site-wide
   content-gap scan, and one content brief per page.
2. **Page Generation** (`pipeline/agent2_generation.py`) — title, meta
   description, H1, JSON-LD schema, Open Graph/Twitter tags, canonical,
   image alt-text fixes and internal links — strictly from the brief and
   the entity list, never inventing a claim or a link.
3. **Technical SEO QA** (`pipeline/agent3_qa.py`) — 13 real checks per
   page (title/description length, canonical, schema validity, orphan
   pages, duplicate content, broken/missing alt text, noindex, HTTP
   status, and an LLM fact-check against the brief), each naming the
   exact failing field.
4. **Publishing & Indexing** (`pipeline/agent4_publishing.py`) — stages
   the approved diff per page, rebuilds `sitemap.xml`, and proposes a
   redirect map, all written to `data/publish_package/`; if configured,
   also opens a GitHub PR with that package
   (`pipeline/github_publisher.py`) and actually submits the sitemap to
   Search Console (`pipeline/google_services.py`) — see "GitHub
   publishing" and "Google services setup" above.
5. **SEO Monitoring** (`pipeline/agent5_monitoring.py`) — re-derives
   technical health from the crawl and builds the action queue, routing
   every real finding to the agent that owns the fix.

### The two feedback loops

- **Loop A (QA → Generation):** a failed check goes back with the exact
  field to fix (e.g. just `meta_description`); only that field is
  regenerated, then the page goes through QA again — capped at one retry
  so a structural issue (an orphan page, a page with two H1s) correctly
  surfaces instead of looping forever, since no amount of copy
  regeneration can fix a missing internal link or a markup error.
- **Loop B (Monitoring → the owning agent):** every real finding lands
  in the action queue with an owner agent (`data/action_queue.json`),
  same as the dashboard's "Open issues routed to the owning agent" panel
  describes. This run doesn't auto-apply those actions — they're there
  for the next run or for a human to action, matching "waiting on human"
  states already in the dashboard's own Agent Supervision panel.

## Validation: it already reproduces the professional audit

With **zero hardcoded answers** — just the live crawl, the seed keyword
themes from `docs/Darwix AI – Website SEO Audit & Remediation Plan.docx`,
and rule-based QA — Agent 3 independently re-finds the audit's own
top findings on every run:

- 8–10 orphan pages (the audit's **C2**, the #2 blocker)
- `/sherpa` has 2 H1 tags, `/voiceAgent/library` has 0 (the audit's **M1**)
- The `/idn/*` pages are both orphans and near-duplicates of the homepage
  (the audit's **H4**)
- Zero pages have server-rendered schema (the audit's **H1**)

## Project layout

```
pipeline/
  config.py              constants, the 5 agents, the entity list, all env vars
  stores.py               shared JSON "data stores" every agent reads/writes
  crawler.py              real crawler for darwix.ai (sitemap + pages)
  llm.py                  Claude wrapper with a labelled stub fallback
  github_publisher.py      GitHub Contents/PRs API for Agent 4's publish PR
  google_services.py       PageSpeed / CrUX / Search Console / GA4 clients
  agent1_research.py       -> agent5_monitoring.py
  orchestrator.py          runs all 5 agents + both feedback loops
  dashboard_data.py        shapes pipeline output to match the dashboard's JS
  dashboard_patch.py        splices real data into the given dashboard HTML
data/                      generated — the shared stores + audit trail
dashboard/                 generated — optimization-dashboard.html
docs/                      the plan, the audit, and the original dashboard asset
config/                    gitignored (*.json) — Google service-account key lives here
run_pipeline.py             CLI entry point
```

## Next steps to go fully live

1. Set `OPENAI_API_KEY` (or `GROQ_API_KEY` for free testing) for real
   research/copy/fact-checking output. **Done** if either is already in
   your `.env`.
2. GitHub CMS publishing — **done**, see "GitHub publishing" above.
3. Google services — **partly yours to finish**, see "Google services
   setup" above for the exact steps per integration (PageSpeed, CrUX,
   Search Console, GA4). A service-account key already exists in
   `config/`; the remaining steps are enabling each API, adding that
   service account as a user on the Search Console property and GA4
   property, and setting the matching env vars in `.env`.
4. Still sample, out of scope for this pass: the "Top pages" table, "CWV
   failures" list, Index Coverage breakdown, and the backlinks/Ahrefs
   panel (no backlink API wired, and CrUX has no reliable per-page
   breakdown for low-traffic pages).
