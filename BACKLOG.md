# Daily Brief — Backlog

## Standing Instructions
Append to every Claude Code session prompt: "When all open items are complete, update README.md to reflect current codebase state, commit, and exit."

When completing a backlog item, move it from `## Open` to `## Done` and prefix with `- [x]`. Do not delete it.

Match the existing `- [x]` entry format exactly — one line, past tense, concise description of what was done and which files were affected.

Do not create new sections, headers, or formatting not already present in the file.

When adding a new open item (e.g. `[source-health]` entries), match the existing open item format exactly — bold title, blank line, descriptive paragraph, implementation notes if needed.

Never leave an item in `## Open` if all its implementation steps are complete.

Make a discrete git commit after completing each backlog item. Commit message format: `#<item-number> — <short description>`.

---

## Open

### #29 — Prompt caching
Enable Anthropic prompt caching on stable, large system prompt blocks to reduce morning brief token cost by an estimated 30–50%.

Implementation:
- In `synthesize.py`, add `"cache_control": {"type": "ephemeral"}` to the system prompt message block for both morning and midday synthesis calls
- Verify prompt length qualifies (≥1024 tokens) — morning system prompt almost certainly does
- Check current Anthropic SDK docs for exact parameter placement (feature has evolved)
- Log cache hit/miss metrics if available in API response (`usage.cache_read_input_tokens`)
- Validate no change in synthesis output quality after enabling

Note: Cache TTL is 5 minutes by default. For cron-based runs this does not help across separate invocations unless extended TTL (1 hour) is available for the model in use. Check docs for current TTL options on Sonnet.

---

### #30 — Source health tracking
Add source health tracking as a side effect of the existing fetch pipeline. Do not create a separate audit script.

Implementation:
- Add `source_health` table to DB schema (create if not exists): `source_name TEXT`, `url TEXT`, `format TEXT` (rss/scrape/api/manual), `last_fetch_at DATETIME`, `last_success_at DATETIME`, `last_status TEXT` (ok/empty/error/paywall), `article_count INTEGER`, `notes TEXT`
- In `fetchers/rss_fetcher.py`, upsert a row to `source_health` after each feed fetch attempt — status, article count, timestamp. Reuse fetch result already in hand; no additional HTTP calls.
- After each run, append any source with status not `ok` to `BACKLOG.md` as an open item tagged `[source-health]` — skip if an open item for that source already exists.
- Update `README.md` to reflect new table and behavior.

---

### #31 — General purpose ingest layer
Extend the fetcher layer beyond RSS to support JS-rendered pages and direct scraping for sources where RSS is unavailable or incomplete. Free content only — no paywall bypass.

Implementation:
- Add `fetchers/web_scraper.py` using Playwright (async Python, headless Chromium)
- Writes to `raw_articles` using same schema as `rss_fetcher.py` so downstream pipeline is unaware of fetch method
- Call from `main.py` alongside `rss_fetcher.py`
- Initial targets: Morgan Stanley Insights (`morganstanley.com/insights`)
- Record fetch method as `format=scrape` in `source_health` table
- Add Playwright to `requirements.txt`

Note: Schwab is WAF-blocked — deprioritized until Morgan Stanley scraper is validated. Paywalled sources (FT, Bloomberg BW, The Information, Puck) are out of scope.

---

### #32 — Sample brief in repo
Copy the most recent morning brief from `output/briefs/` into `output/examples/sample_brief.html` and commit it. Do not generate a new brief. Do not automate this — one-time manual copy. Add a "What it produces" section near the top of `README.md` with prose description of the email format (tiers, graphical header, theme callout) and a link to `output/examples/sample_brief.html`.

---

## Done

- [x] **#1 — Brief type in subject line** — `--brief-type` passed from cron → main.py → send_brief.py → email subject
- [x] **#2 — Cron timezone** — `CRON_TZ=America/Los_Angeles`; morning 06:00, midday 11:30 PDT
- [x] **#3 — Add "Why it matters" to Tier 2 items** — added to synthesis prompt
- [x] **#4 — SEC Form 4 suppressed** — bare stubs suppressed until EDGAR enrichment is implemented
- [x] **#5 — Cap Tier 3 at 5 items** — enforced in synthesis prompt
- [x] **#6 — Single-source geopolitical stories default to Tier 2** — enforced in scoring rules
- [x] **#7 — Jarvis tone morning summary paragraph** — implemented in `morning_system.md`
- [x] **#8 — Clickable source links in tiered detail** — one best source per story, priority hierarchy in scoring rules
- [x] **#9 — arxiv items tiered + labeled unreviewed** — implemented in scoring rules
- [x] **#10 — Stale article re-ingestion** — `ARTICLE_MAX_AGE_DAYS = 7` cutoff in `rss_fetcher.py`; articles older than 7 days skipped at fetch time
- [x] **#11 — Graphical header with tier counts + theme callout** — `build_graphical_header`, `extract_theme`, `count_tier_items` in `send_brief.py`; THEME: line in synthesis prompts
- [x] **#12 — Add new sources** — Packet Pushers, Light Reading, The Next Platform, Stacey on IoT, Calculated Risk, Net Interest added to `feeds.yaml` and `sources.md`
- [x] **#13 — Cap arxiv items in Flags** — Pre-Publication Research capped at 2–3 items in both `morning_system.md` and `midday_system.md` scoring rules
- [x] **#14 — Optimize token usage** — `PROMPT_TEXT_CHARS = 400` in `synthesize.py`; zero-signal pre-filter (zeitgeist-only + vendor-low groups dropped); per-category slot allocation for morning brief
- [x] **#15 — Story group deduplication** — Fuzzy Jaccard matching (threshold 0.40) with 7-day freshness window in `normalize.py`; adds `title_tokens` + `last_published` to `story_groups`; logs fuzzy merge count per run
- [x] **#16 — Suppress empty/low-signal briefs** — `brief_has_actionable_content()` in `send_brief.py`; no Tier 1/2 → suppression log + one-liner notification email
- [x] **#17 — Suppress empty sections** — prompts updated to omit subsections instead of "None"; `strip_none_sections()` post-processor in `send_brief.py` as safety net
- [x] **#18 — Model routing optimization** — `--model` flag in `main.py`; morning→Sonnet, midday→Haiku, dry-run→Haiku; passed through to `synthesize.py`
- [x] **#19 — DB retention policy** — `maintenance/cleanup_db.py`; 30d raw/parsed/fetch_log, 60d story_groups, brief_history indefinite; VACUUM after run; logs to `logs/cleanup.log`
- [x] **#20 — Portfolio and candidate tracking (core)** — `parsers/portfolio_parser.py` (portfolio.md → DB), `parsers/candidates_writer.py` (DB → candidates.md), `portfolio.md.template`; portfolio/candidates/score_history DB tables; Portfolio Signals + Candidate Signals sections in `midday_system.md`; pipeline integration in `main.py`
- [x] **#21 — Portfolio auto-sourcing (SEC EDGAR per-ticker fetching)** — `fetchers/edgar_fetcher.py`; fetches 8-K, 10-Q, 10-K, Form 4 for all tickers in portfolio+candidates tables; Federal Reserve + BLS macro RSS feeds; ticker→CIK via EDGAR company_tickers.json with daily cache; called as Stage 1c in `main.py`
- [x] **#22 — Story deduplication: lower Jaccard threshold + entity matching + URL domain clustering** — `FUZZY_THRESHOLD` lowered 0.40→0.20; entity match path; URL domain cluster path; all in `parsers/normalize.py`
- [x] **#23 — EDGAR enrichment: resolve filing documents and extract signal content** — `fetchers/edgar_fetcher.py` now fetches and parses primary documents for all filing types; enrichment failures fall back to stub with trust_level='low'
- [x] **#24 — Add institutional market commentary sources** — Edward Jones RSS added to `feeds.yaml` (category=macro, trust=medium); Schwab and Morgan Stanley added to `sources.md` as manual-review-only
- [x] **#25 — Add Human Infrastructure source** — `https://human-infrastructure.beehiiv.com/feed` added to `feeds.yaml` (category: semiconductors, trust: medium) and `sources.md`
- [x] **#26 — Add Krebs on Security** — `https://krebsonsecurity.com/feed` added to `feeds.yaml` (category: cybersecurity, trust: high) and `sources.md`
- [x] **#27 — Add CISA Advisories** — `https://www.cisa.gov/cybersecurity-advisories/all.xml` added to `feeds.yaml` (category: cybersecurity, trust: high) and `sources.md`
- [x] **#28 — Backlog formatting standing instructions** — standing instructions block expanded with rules for item completion format, Done migration, open item formatting consistency, and per-item commit discipline
