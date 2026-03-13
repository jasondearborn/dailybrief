# Daily Brief — Backlog

## Standing Instructions
Append to every Claude Code session prompt: "When all open items are complete, update README.md to reflect current codebase state, commit, and exit."

When completing a backlog item, move it from `## Open` to `## Done` and prefix with `- [x]`. Do not delete it.

Match the existing `- [x]` entry format exactly — one line, past tense, concise description of what was done and which files were affected.

Do not create new sections, headers, or formatting not already present in the file.

When adding a new open item (e.g. `[source-health]` entries), match the existing open item format exactly — bold title, blank line, descriptive paragraph, implementation notes if needed.

Never leave an item in `## Open` if all its implementation steps are complete.

---

## Open

---

## Done

- [x] **Fix 1 — Brief type in subject line** — `--brief-type` passed from cron → main.py → send_brief.py → email subject
- [x] **Fix 2 — Cron timezone** — `CRON_TZ=America/Los_Angeles`; morning 06:00, midday 11:30 PDT
- [x] **Fix 3 — Add "Why it matters" to Tier 2 items** — added to synthesis prompt
- [x] **Fix 4 — SEC Form 4 suppressed** — bare stubs suppressed until EDGAR enrichment is implemented
- [x] **Fix 5 — Cap Tier 3 at 5 items** — enforced in synthesis prompt
- [x] **Fix 6 — Single-source geopolitical stories default to Tier 2** — enforced in scoring rules
- [x] **Fix 7 — Jarvis tone morning summary paragraph** — implemented in `morning_system.md`
- [x] **Fix 8 — Clickable source links in tiered detail** — one best source per story, priority hierarchy in scoring rules
- [x] **Fix 9 — arxiv items tiered + labeled unreviewed** — implemented in scoring rules
- [x] **Fix 10 — Stale article re-ingestion** — `ARTICLE_MAX_AGE_DAYS = 7` cutoff in `rss_fetcher.py`; articles older than 7 days skipped at fetch time
- [x] **Fix 11 — Graphical header with tier counts + theme callout** — `build_graphical_header`, `extract_theme`, `count_tier_items` in `send_brief.py`; THEME: line in synthesis prompts
- [x] **Fix 12 — Add new sources** — Packet Pushers, Light Reading, The Next Platform, Stacey on IoT, Calculated Risk, Net Interest added to `feeds.yaml` and `sources.md`
- [x] **Fix 13 — Cap arxiv items in Flags** — Pre-Publication Research capped at 2–3 items in both `morning_system.md` and `midday_system.md` scoring rules
- [x] **Fix 14 — Optimize token usage** — `PROMPT_TEXT_CHARS = 400` in `synthesize.py`; zero-signal pre-filter (zeitgeist-only + vendor-low groups dropped); per-category slot allocation for morning brief
- [x] **Fix 15 — Story group deduplication** — Fuzzy Jaccard matching (threshold 0.40) with 7-day freshness window in `normalize.py`; adds `title_tokens` + `last_published` to `story_groups`; logs fuzzy merge count per run
- [x] **Fix 16 — Suppress empty/low-signal briefs** — `brief_has_actionable_content()` in `send_brief.py`; no Tier 1/2 → suppression log + one-liner notification email
- [x] **Fix 17 — Suppress empty sections** — prompts updated to omit subsections instead of "None"; `strip_none_sections()` post-processor in `send_brief.py` as safety net
- [x] **Fix 18 — Model routing optimization** — `--model` flag in `main.py`; morning→Sonnet, midday→Haiku, dry-run→Haiku; passed through to `synthesize.py`
- [x] **Fix 19 — DB retention policy** — `maintenance/cleanup_db.py`; 30d raw/parsed/fetch_log, 60d story_groups, brief_history indefinite; VACUUM after run; logs to `logs/cleanup.log`
- [x] **Feature 20 — Portfolio and candidate tracking (core)** — `parsers/portfolio_parser.py` (portfolio.md → DB), `parsers/candidates_writer.py` (DB → candidates.md), `portfolio.md.template`; portfolio/candidates/score_history DB tables; Portfolio Signals + Candidate Signals sections in `midday_system.md`; pipeline integration in `main.py`
- [x] **Feature 21 — Portfolio auto-sourcing (SEC EDGAR per-ticker fetching)** — `fetchers/edgar_fetcher.py`; fetches 8-K, 10-Q, 10-K for all tickers in portfolio+candidates tables; Form 4 enriched from XML; Federal Reserve + BLS macro RSS feeds; writes to raw_articles; ticker→CIK via EDGAR company_tickers.json with daily cache; called as Stage 1c in `main.py`
- [x] **Fix — Story deduplication: lower Jaccard threshold + entity matching + URL domain clustering** — `FUZZY_THRESHOLD` lowered 0.40→0.20; entity match path; URL domain cluster path; all in `parsers/normalize.py`
- [x] **Fix — EDGAR enrichment: resolve filing documents and extract signal content** — `fetchers/edgar_fetcher.py` now fetches and parses primary documents for all filing types; enrichment failures fall back to stub with trust_level='low'
- [x] **Add — Institutional market commentary sources** — Edward Jones RSS added to `feeds.yaml` (category=macro, trust=medium); Schwab and Morgan Stanley added to `sources.md` as manual-review-only
- [x] **Add — Human Infrastructure source** — `https://human-infrastructure.beehiiv.com/feed` added to `feeds.yaml` (category: semiconductors, trust: medium) and `sources.md`
- [x] **Add — Krebs on Security** — `https://krebsonsecurity.com/feed` added to `feeds.yaml` (category: cybersecurity, trust: high) and `sources.md`
- [x] **Add — CISA Advisories** — `https://www.cisa.gov/cybersecurity-advisories/all.xml` added to `feeds.yaml` (category: cybersecurity, trust: high) and `sources.md`
- [x] **Add — Backlog formatting standing instructions** — standing instructions block expanded with rules for item completion format, Done migration, and open item formatting consistency
- [x] **Optimization — Prompt caching** — `cache_control: ephemeral` on system prompt in `synthesize.py`; `cache_read_tokens`/`cache_creation_tokens` logged and stored in `brief_history`; DB migration via ALTER TABLE
- [x] **Feature — Source health tracking** — `source_health` table added to DB in `rss_fetcher.py`; upsert after each feed fetch (ok/empty/error); `update_backlog_for_unhealthy_sources` appends `[source-health]` open items for failed sources at end of run
- [x] **Feature — General purpose ingest layer** — `fetchers/web_scraper.py` added (Playwright async, headless Chromium); initial target Morgan Stanley Insights; writes to `raw_articles`; upserts `source_health` with `format=scrape`; called as Stage 1a in `main.py`; `playwright` added to `requirements.txt`