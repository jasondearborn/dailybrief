# Daily Brief — Backlog

## Open

### Fix — README maintenance (standing instruction)
This is a standing instruction for every Claude Code session. Append to all BACKLOG.md work session prompts: "When all open items are complete, update README.md to reflect current codebase state, commit, and exit."

---

### Feature — Portfolio and candidate tracking

#### Overview
Add portfolio-aware synthesis to the midday brief. Surfaces actionable signals against held positions and watchlist items only when triggered. Silent (no section rendered) when nothing material has occurred.

#### New files
- `portfolio.md` — hand-edited by user. Source of truth for holdings, watchlist, and index positions. Parsed by fetcher on each run and upserted into DB. **Add to `.gitignore`.**
- `candidates.md` — machine-generated view of candidates table. Refreshed each morning brief run. Read-only, never hand-edit. **Add to `.gitignore`.**

#### portfolio.md structure
```markdown
## Index Holdings
VTI, VYM, VOO
Alert threshold: systemic macro only

## Holdings
| Ticker | Thesis | Sector |
|--------|--------|--------|
| SITM   | RF timing chips for AI infrastructure, underfollowed per SemiAnalysis | Semiconductors |
| MU     | HBM demand cycle, Huawei HBM constraint tightens supply for US buyers | Semiconductors |

## Watchlist
| Ticker | Thesis | Buy Trigger |
|--------|--------|-------------|
| ALAB   | PCIe retimers for AI fabric, emerging play | SemiAnalysis coverage or thesis-price pullback |
```

Thesis field may be `auto` (system-generated from source articles at first run) or `none` (no thesis — monitor for material events only).

#### New DB tables
```sql
CREATE TABLE portfolio (
    ticker TEXT PRIMARY KEY,
    thesis TEXT,
    sector TEXT,
    thesis_source TEXT,
    list_type TEXT,        -- 'holding' | 'watchlist' | 'index'
    last_updated TEXT
);

CREATE TABLE candidates (
    ticker TEXT PRIMARY KEY,
    company_name TEXT,
    macro_score INTEGER,        -- 1-5
    fundamentals_score INTEGER, -- 1-5
    conviction_score INTEGER,   -- macro * fundamentals, 1-25
    thesis TEXT,
    source_name TEXT,
    source_url TEXT,
    first_seen TEXT,
    last_updated TEXT,
    status TEXT DEFAULT 'active' -- 'active' | 'promoted' | 'dismissed'
);

CREATE TABLE score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    macro_score INTEGER,
    fundamentals_score INTEGER,
    conviction_score INTEGER,
    reason TEXT,
    scored_at TEXT
);
```

#### Candidate scoring model
Minimum threshold for inclusion in candidates: **conviction score ≥ 10**. Filters out buzz plays automatically.

**Macro Conviction (1–5):**
- 5: Established cycle with documented historical precedent (e.g. MU/HBM memory shortage cycles)
- 4: Strong structural trend, early but directionally confirmed by data
- 3: Plausible thesis, trend real but timing uncertain
- 2: Speculative, trend unconfirmed
- 1: Buzz-driven, no macro anchor

**Business Fundamentals (1–5):**
- 5: Established, profitable, dominant market position, pricing power (e.g. MU)
- 4: Proven model, strong moat, scaling
- 3: Emerging but differentiated, credible path to profitability (e.g. SITM)
- 2: Early stage, unproven at scale, single-catalyst dependent
- 1: Pre-revenue, narrative only

**Anti-buzz rules enforced in synthesis prompt:**
- Requires macro trend citation from high/medium trust source — zeitgeist and vendor sources do not qualify
- Requires business fundamentals evidence: revenue, margins, market position, or moat
- Vendor-only coverage caps Macro score at 3
- Reddit/zeitgeist mentions contribute zero to score
- Mention frequency does not increase score — one well-sourced article outranks ten buzz mentions
- Must be backed by macro trend AND business fundamentals — either alone is insufficient

#### Auto-sourcing for portfolio and candidate tickers
When a ticker appears in `portfolio` or `candidates` tables, automatically pull on each fetch run:
- SEC EDGAR 8-K (material events, guidance, partnerships, leadership changes)
- SEC EDGAR 10-Q / 10-K (quarterly and annual financials)
- SEC EDGAR Form 4 (insider transactions — requires enrichment below)
- Earnings call transcripts (management forward guidance only, not analyst commentary)
- Federal Reserve releases and BLS data (CPI, PPI, jobs) as macro inputs for all tickers

Primary fact-based sources only. No narrative media for portfolio synthesis. The model and user generate narrative from raw facts directly.

#### SEC Form 4 enrichment
Fetch and parse actual EDGAR filing XML to extract: transaction type (buy/sell/grant), shares, price, insider name and title. Only include Form 4 in synthesis if enrichment succeeds. Suppress bare stubs entirely.

#### Portfolio section in midday brief — exception-based
Only render when a trigger event has occurred. No section = nothing triggered.

**Triggers:**
- Material news directly involving a held ticker (8-K, earnings, guidance change, partnership, lawsuit)
- Enriched Form 4 with actual transaction detail
- Macro catalyst with direct sector impact on held positions
- Geopolitical event with supply chain implications for held sectors
- Significant analyst consensus shift

**Does NOT trigger:**
- Routine filings (prospectuses, Reg D stubs, Form 144 without detail)
- Price movement alone
- Tangentially related sector news without direct thesis impact
- Anything already covered in that day's morning brief

**Macro alert — renders above individual ticker entries:**
```
⚠ MACRO ALERT: [Event] confirmed by [N] independent sources.
Direct portfolio exposure: [affected tickers]
```
Fires only on high-confidence (3+ independent sources) systemic events. Black swan / pull-the-plug signal for index holdings.

**Per-ticker output format:**
```
### [TICKER] — [Company Name]
**Signal:** thesis confirmation | thesis risk | macro catalyst | insider activity
**What happened:** [2-3 sentences, factual, primary sources only]
**Thesis check:** [Is the original thesis intact, strengthened, or at risk?]
**Suggested action:** Hold | Add on weakness | Begin exit | Watch closely
**Confidence:** high/medium/low | **Source:** [link]
```

**Sell signal framing — thesis-based only, never price-based:**
- Thesis invalidation: the reason you bought is no longer true
- Thesis completion: catalyst has fully played out and is priced in
- Sector rotation: capital confirmed moving away from the thesis sector
- Concentration risk: position disproportionate to portfolio (flag only, not a sell)

#### candidates.md format (auto-generated, read-only)
Ranked by conviction score descending. New additions flagged. Candidates dropping below 10 removed with logged reason.

```markdown
# Candidates — generated {DATE}

| Ticker | Company | Conviction | Macro | Fund. | Thesis Summary | Source | Added |
|--------|---------|-----------|-------|-------|----------------|--------|-------|
| MU     | Micron  | 25        | 5     | 5     | HBM demand cycle... | SemiAnalysis | 2026-03-11 |
| SITM   | SiTime  | 12        | 4     | 3     | RF timing for AI... | SemiAnalysis | 2026-03-11 |
```

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
- [x] **Fix 19 — DB retention policy** — `maintenance/cleanup_db.py`; 30d raw/parsed/fetch_log, 60d story_groups, brief_history indefinite; VACUUM after run; logs to `logs/cleanup.log`
- [x] **Fix 18 — Model routing optimization** — `--model` flag in `main.py`; morning→Sonnet, midday→Haiku, dry-run→Haiku; passed through to `synthesize.py`
- [x] **Fix 17 — Suppress empty sections** — prompts updated to omit subsections instead of "None"; `strip_none_sections()` post-processor in `send_brief.py` as safety net
- [x] **Fix 16 — Suppress empty/low-signal briefs** — `brief_has_actionable_content()` in `send_brief.py`; no Tier 1/2 → suppression log + one-liner notification email
- [x] **Fix 15 — Story group deduplication** — Fuzzy Jaccard matching (threshold 0.40) with 7-day freshness window in `normalize.py`; adds `title_tokens` + `last_published` to `story_groups`; logs fuzzy merge count per run
