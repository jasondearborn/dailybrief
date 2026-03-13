# Daily Brief — Backlog

## Open

### Standing instruction — README maintenance
Append to every Claude Code session prompt: "When all open items are complete, update README.md to reflect current codebase state, commit, and exit."

---

### Optimization — Prompt caching
Enable Anthropic prompt caching on stable, large system prompt blocks to reduce morning brief token cost by an estimated 30–50%.

Implementation:
- In `synthesize.py`, add `"cache_control": {"type": "ephemeral"}` to the system prompt message block for both morning and midday synthesis calls
- Verify prompt length qualifies (≥1024 tokens) — morning system prompt almost certainly does
- Check current Anthropic SDK docs for exact parameter placement (feature has evolved)
- Log cache hit/miss metrics if available in API response (`usage.cache_read_input_tokens`)
- Validate no change in synthesis output quality after enabling

Note: Cache TTL is 5 minutes by default. For cron-based runs this does not help across separate invocations unless extended TTL (1 hour) is available for the model in use. Check docs for current TTL options on Sonnet.

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
- [x] **Feature 20 — Portfolio and candidate tracking (core)** — `parsers/portfolio_parser.py` (portfolio.md → DB), `parsers/candidates_writer.py` (DB → candidates.md), `portfolio.md.template`; portfolio/candidates/score_history DB tables; Portfolio Signals + Candidate Signals sections in `midday_system.md`; portfolio.md + candidates.md added to `.gitignore`; pipeline integration in `main.py`
- [x] **Feature 21 — Portfolio auto-sourcing (SEC EDGAR per-ticker fetching)** — `fetchers/edgar_fetcher.py`; fetches 8-K, 10-Q, 10-K for all tickers in portfolio+candidates tables; Form 4 enriched from XML (transaction type, shares, price, insider name/title) — suppressed if enrichment fails; Federal Reserve + BLS macro RSS feeds; writes to raw_articles (category=portfolio_signals or macro); ticker→CIK via EDGAR company_tickers.json with daily cache; called as Stage 1c in `main.py` after portfolio_parser
- [x] **Fix — Story deduplication: lower Jaccard threshold + entity matching + URL domain clustering** — `FUZZY_THRESHOLD` lowered 0.40→0.20; entity match path (shared portfolio/candidates ticker or company-name token + Jaccard ≥ 0.15); URL domain cluster path (different domain + shared entity token + published within 24h → merge); `load_entity_tokens()` queries portfolio+candidates tables; `load_recent_groups()` fetches representative domain via parsed_articles join; all in `parsers/normalize.py`
- [x] **Fix — EDGAR enrichment: resolve filing documents and extract signal content** — `fetchers/edgar_fetcher.py` now fetches and parses primary documents for all filing types. 8-K: extracts item numbers + 500-char disclosure text, filters to high-signal items (1.01/1.02/1.03/2.01/2.05/2.06/5.02/7.01/8.01), skips low-signal-only filings. 10-Q/10-K: extracts MD&A section (600 chars), trust_level='medium'. Form 4: grants (A) and 10b5-1 sales suppressed; open-market purchases surfaced with insider name/role/shares/price/resulting position. Enrichment failures fall back to stub with trust_level='low' and "(enrichment failed)" title suffix. `EDGAR_USER_AGENT` env var used; 0.15s request delay. BeautifulSoup + dotenv added.
- [x] **Add — Institutional market commentary sources** — Edward Jones RSS (`/rss.xml`, category=macro, trust=medium) added to `feeds.yaml`. Charles Schwab (WAF blocks, no RSS) and Morgan Stanley (no RSS) added to `sources.md` as manual-review-only. Feed count: 44 → 45. All three documented with institutional bias note.
- [x] **Add — Human Infrastructure source** — `https://human-infrastructure.beehiiv.com/feed` added to `config/feeds.yaml` (category: semiconductors, trust: medium) and `sources.md`. Feed count: 43 → 44.
- [x] **Add — Krebs on Security** — `https://krebsonsecurity.com/feed` added to `feeds.yaml` (category: cybersecurity, trust: high) and `sources.md` under new Cybersecurity section.
- [x] **Add — CISA Advisories** — `https://www.cisa.gov/cybersecurity-advisories/all.xml` added to `feeds.yaml` (category: cybersecurity, trust: high) and `sources.md` under Cybersecurity section.
