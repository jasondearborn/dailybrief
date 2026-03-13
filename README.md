# DailyBrief

Personal RSS-to-email news briefing pipeline. Fetches 47 curated feeds plus SEC EDGAR filings, macro data for portfolio tickers, and JS-rendered sources via Playwright; normalizes and deduplicates articles; synthesizes them via Claude with prompt caching; and delivers a formatted HTML email brief. Includes portfolio-aware synthesis for midday briefs and source health tracking.

## Pipeline

```
rss_fetcher.py → web_scraper.py → portfolio_parser.py → edgar_fetcher.py → normalize.py → synthesize.py → send_brief.py
                                   (if portfolio.md exists)                                  candidates_writer.py (morning only)
```

1. **fetchers/rss_fetcher.py** — Fetch all feeds from `config/feeds.yaml`, write raw articles to SQLite. Deduplicates by URL hash. Skips articles older than 7 days. Upserts `source_health` after each feed; appends `[source-health]` open items to `BACKLOG.md` for failed sources.
2. **fetchers/web_scraper.py** — Scrape JS-rendered sources (headless Chromium via Playwright). Initial target: Morgan Stanley Insights. Writes to `raw_articles` using the same schema as `rss_fetcher.py`. Upserts `source_health` with `format=scrape`. Non-fatal — pipeline continues if scrape fails.
3. **parsers/portfolio_parser.py** *(optional)* — Parse `portfolio.md` and upsert holdings, watchlist, and index positions into the `portfolio` DB table. Skipped silently if `portfolio.md` is absent.
4. **fetchers/edgar_fetcher.py** — For each ticker in the `portfolio` and `candidates` tables, fetch recent 8-K, 10-Q, 10-K, and Form 4 filings from SEC EDGAR. Each filing type is enriched by fetching and parsing the primary document:
   - **8-K**: Extracts item numbers (e.g. Item 1.01, 5.02) and first 500 chars of disclosure text. Only high-signal items are surfaced (1.01, 1.02, 1.03, 2.01, 2.05, 2.06, 5.02, 7.01, 8.01); low-signal-only filings (2.02, 9.01) are skipped. Title format: `{TICKER} 8-K — Item X.XX: Description`.
   - **10-Q / 10-K**: Extracts MD&A section opening paragraph (up to 600 chars). Title format: `{TICKER} {form} — {fiscal period}`. Trust level set to `medium`.
   - **Form 4**: Enriched from XML (insider name, role, transaction type, shares, price, resulting position). Open-market purchases (code P) are surfaced; grants/awards (code A) and 10b5-1 sales are suppressed. Failed enrichments are suppressed. Title format: `{TICKER} — {Insider} ({Role}): {transaction} {shares} @ ${price}`.
   - On enrichment failure for any filing: falls back to stub title/summary, trust level set to `low`, `(enrichment failed)` appended to title. Pipeline does not crash.
   - EDGAR rate limit respected (0.15s between requests). `EDGAR_USER_AGENT` env var used as User-Agent header.
   - Also fetches Federal Reserve and BLS macro RSS feeds. Writes to `raw_articles` with category `portfolio_signals` or `macro`.
5. **parsers/normalize.py** — Strip HTML, detect/translate language, group articles by story using three-path deduplication: exact title-token hash, fuzzy Jaccard similarity (threshold 0.20) with a 7-day freshness window, entity-match boost (shared portfolio/candidates ticker or company-name token + Jaccard ≥ 0.15), and URL domain clustering (different domain + shared entity token + published within 24h → merge). Assigns confidence levels. Flags sponsored Packet Pushers episodes as vendor.
6. **synthesis/synthesize.py** — Pull story groups from DB, pre-filter zero-signal groups, call Claude API with prompt caching (`cache_control: ephemeral` on system prompt), write brief to `output/briefs/`. Logs and stores cache read/creation tokens per run. Morning uses Sonnet; midday uses Haiku by default.
7. **parsers/candidates_writer.py** *(morning only)* — Regenerate `candidates.md` from the `candidates` DB table after synthesis.
8. **delivery/send_brief.py** — Check for actionable content; suppress and notify if no Tier 1/2 stories. Render brief as HTML email with graphical header (tier count tiles + theme callout) and send via SMTP.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/.env.example config/.env  # fill in API keys
```

### config/.env keys required

```
ANTHROPIC_API_KEY=...
EMAIL_SMTP_HOST=...
EMAIL_SMTP_PORT=...
EMAIL_USER=...
EMAIL_PASSWORD=...
EMAIL_TO=...
EDGAR_USER_AGENT=your@email.com   # Required by SEC EDGAR policy
```

## Usage

Run the full pipeline via the orchestrator:

```bash
python main.py --brief-type morning
python main.py --brief-type midday
python main.py --brief-type morning --skip-delivery   # synthesize only, no email
python main.py --brief-type morning --dry-run         # no API calls, no email
python main.py --brief-type morning --model claude-sonnet-4-6  # override model
```

Or run individual stages:

```bash
python fetchers/rss_fetcher.py
python fetchers/web_scraper.py              # JS-rendered sources (requires Playwright)
python parsers/portfolio_parser.py          # requires portfolio.md
python fetchers/edgar_fetcher.py            # requires portfolio/candidates tables in DB
python parsers/normalize.py
python synthesis/synthesize.py --brief-type morning [--hours-back 24] [--max-stories 80] [--model MODEL]
python delivery/send_brief.py --brief-type morning
python maintenance/cleanup_db.py --dry-run  # preview DB retention cleanup
```

After installing Playwright for the first time:
```bash
playwright install chromium
```

Each script supports `--dry-run`.

## Model Routing

Default models (overridable with `--model`):

| Brief type | Default model |
|------------|---------------|
| morning | `claude-sonnet-4-6` |
| midday | `claude-haiku-4-5-20251001` |
| dry-run (any) | `claude-haiku-4-5-20251001` |

## Cron Schedule

Runs are scheduled via crontab (`CRON_TZ=America/Los_Angeles`):

```
0 6    * * *   main.py --brief-type morning   # 6:00 AM PT
30 11  * * *   main.py --brief-type midday    # 11:30 AM PT
0 3    * * 0   maintenance/cleanup_db.py      # Sunday 3:00 AM PT (DB retention)
```

Logs: `logs/cron_morning.log`, `logs/cron_midday.log`

## Feed Configuration

47 feeds defined in `config/feeds.yaml`. Categories and trust levels:

| Field | Values |
|-------|--------|
| `category` | semiconductors, networking, ai, tech, finance, macro, local_safety, culture, reddit, research, vendor, china, cybersecurity |
| `trust_level` | `high` \| `medium` \| `vendor` \| `state_adjacent` \| `research` \| `zeitgeist` |

Confidence is assigned during normalization:
- **high** — 3+ independent sources on the same story
- **medium** — 2 sources, or a single high-trust source
- **low** — single source, no established track record

## Portfolio Tracking

Create `portfolio.md` from the template at `portfolio.md.template` (gitignored — personal financial data):

```markdown
## Index Holdings
VTI, VYM, VOO
Alert threshold: systemic macro only

## Holdings
| Ticker | Thesis | Sector |
|--------|--------|--------|
| SITM   | RF timing chips for AI infrastructure | Semiconductors |

## Watchlist
| Ticker | Thesis | Buy Trigger |
|--------|--------|-------------|
| ALAB   | PCIe retimers for AI fabric | SemiAnalysis coverage |
```

When `portfolio.md` is present:
- EDGAR 8-K, 10-Q, 10-K, and enriched Form 4 filings are fetched automatically for each ticker on every run.
- Federal Reserve and BLS macro releases are fetched on every run regardless of portfolio state.
- The midday brief gains a **Portfolio Signals** section (exception-based — only renders when a trigger event has occurred) and a **Candidate Signals** section (new tickers with conviction ≥ 10).

`candidates.md` is auto-generated after each morning brief (gitignored). Ranked by conviction score (macro × fundamentals, max 25). Anti-buzz rules enforced at synthesis time.

## DB Retention

`maintenance/cleanup_db.py` enforces per-table retention (run weekly):

| Table | Retention |
|-------|-----------|
| raw_articles | 30 days |
| parsed_articles | 30 days |
| story_groups | 60 days |
| fetch_log | 30 days |
| brief_history | indefinite |
| source_health | indefinite (one row per source, upserted each run) |

Deletes are logged to `logs/cleanup.log`.

## Source Health

`source_health` is updated after every fetch attempt (rss_fetcher.py and web_scraper.py). One row per source:

| Column | Description |
|--------|-------------|
| `source_name` | Primary key |
| `url` | Feed or scrape URL |
| `format` | `rss` or `scrape` |
| `last_fetch_at` | Timestamp of most recent attempt |
| `last_success_at` | Timestamp of most recent `ok` result |
| `last_status` | `ok` / `empty` / `error` |
| `article_count` | Article count from most recent fetch |
| `notes` | Manual notes |

After each rss_fetcher.py run, any source with status `error` or `empty` that does not already have an open `[source-health]` item is appended to `BACKLOG.md` automatically.

## Project Structure

```
main.py                   # Pipeline orchestrator
config/
  feeds.yaml              # 47 RSS feeds with trust levels
  prompts/
    morning_system.md     # Claude system prompt — morning brief
    midday_system.md      # Claude system prompt — midday brief (+ portfolio/candidates)
  .env                    # API keys (not committed)
data/
  newsfeed.db             # SQLite runtime DB (not committed)
fetchers/
  rss_fetcher.py          # RSS/Atom fetch + raw_articles write + source_health upsert
  web_scraper.py          # Playwright scraper for JS-rendered sources + source_health upsert
  edgar_fetcher.py        # SEC EDGAR filings + macro feeds (Fed/BLS) for portfolio tickers
parsers/
  normalize.py            # HTML strip, translate, fuzzy dedup, story_groups write
  portfolio_parser.py     # portfolio.md → portfolio DB table
  candidates_writer.py    # candidates DB table → candidates.md
synthesis/
  synthesize.py           # Claude API call, brief_history write
delivery/
  send_brief.py           # HTML email builder + SMTP delivery
maintenance/
  cleanup_db.py           # DB retention cleanup (weekly cron)
output/
  briefs/                 # Generated brief .md files (not committed)
logs/                     # Run logs (not committed)
portfolio.md.template     # Template for portfolio.md (copy + edit)
sources.md                # Annotated source list with trust rationale
BACKLOG.md                # Fix tracker
```

## Brief Types

| Type | Lookback | Max stories | Categories | Model |
|------|----------|-------------|------------|-------|
| morning | 24h | 80 | all | Sonnet |
| midday | 8h | 40 | finance, semiconductors, ai | Haiku |

### Email header

Each brief email includes a graphical header (IBM Plex Mono/Sans, dark theme) with:
- Tier count tiles (Act Now / Monitor / Background / Flags) with color-coded borders
- Theme callout strip — one-sentence dominant narrative, generated by synthesis

### Pre-filtering

Before synthesis, zero-signal story groups are dropped to reduce token usage:
- All-zeitgeist groups (pure Reddit noise)
- Vendor-only groups with low confidence (no independent pickup)

Story deduplication uses three-path matching:
1. Exact title-token hash (fast path)
2. Fuzzy Jaccard similarity ≥ 0.20 against groups seen in the last 7 days (lowered from 0.40); entity match boost: shared portfolio/candidates ticker or company-name token AND Jaccard ≥ 0.15 → merge
3. URL domain clustering: different URL domain + shared entity token + published within 24h → merge regardless of Jaccard score

## Dependencies

Key packages: `feedparser`, `anthropic`, `playwright`, `beautifulsoup4`, `deep-translator`, `langdetect`, `python-dotenv`, `PyYAML`, `Markdown`.

Full pinned list in `requirements.txt`.
