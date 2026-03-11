# Daily Brief — Backlog

## Open

---

## Done

- [x] **Fix 1 — Brief type in subject line** — `--brief-type` passed from cron → main.py → send_brief.py → email subject
- [x] **Fix 2 — Cron timezone** — `CRON_TZ=America/Los_Angeles` set; morning 06:00, midday 11:30 PDT
- [x] **Fix 3 — Add "Why it matters" to Tier 2 items** — added to synthesis prompt
- [x] **Fix 4 — SEC Form 4 suppressed** — bare stubs suppressed until EDGAR enrichment is implemented
- [x] **Fix 5 — Cap Tier 3 at 5 items** — enforced in synthesis prompt
- [x] **Fix 6 — Single-source geopolitical stories default to Tier 2** — enforced in scoring rules
- [x] **Fix 7 — Jarvis tone morning summary paragraph** — implemented in `morning_system.md`
- [x] **Fix 8 — Clickable source links in tiered detail** — one best source per story, priority hierarchy in scoring rules
- [x] **Fix 9 — arxiv items tiered + labeled unreviewed** — implemented in scoring rules
- [x] **Fix 10 — Stale article re-ingestion** — 7-day published-date cutoff in `rss_fetcher.py`; articles older than `ARTICLE_MAX_AGE_DAYS` skipped at ingest
- [x] **Fix 11 — Graphical header with tier counts + theme callout** — `send_brief.py` now parses tier counts and THEME line; renders IBM Plex tile header with colored borders and glowing theme strip; synthesis prompts emit `THEME:` line
- [x] **Fix 12 — Add new sources** — 7 feeds added to `feeds.yaml` + `sources.md`: Packet Pushers Heavy Networking, Packet Pushers Network Break, Light Reading, The Next Platform, Stacey on IoT, Calculated Risk, Net Interest; sponsored Packet Pushers episodes auto-flagged vendor in `normalize.py`
- [x] **Fix 13 — Cap arxiv items in Flags** — synthesis prompts cap Pre-Publication Research at 2–3 most relevant items
- [x] **Fix 14 — README maintenance** — README updated to reflect 43 feeds, new pipeline behaviors, graphical header, pre-filtering
- [x] **Fix 15 — Optimize token usage** — morning `max_stories` 100→80; pre-filter drops all-zeitgeist and vendor-only/low-confidence groups before synthesis; synthesis prompts condensed
