# DailyBrief

Personal RSS-to-email news briefing pipeline. Fetches 43 curated feeds, normalizes and deduplicates articles, synthesizes them via Claude, and delivers a formatted HTML email brief. Includes portfolio-aware synthesis for midday briefs.

## Pipeline

```
rss_fetcher.py → portfolio_parser.py → normalize.py → synthesize.py → send_brief.py
                 (if portfolio.md exists)              candidates_writer.py (morning only)
```

1. **fetchers/rss_fetcher.py** — Fetch all feeds from `config/feeds.yaml`, write raw articles to SQLite. Deduplicates by URL hash. Skips articles older than 7 days.
2. **parsers/portfolio_parser.py** *(optional)* — Parse `portfolio.md` and upsert holdings, watchlist, and index positions into the `portfolio` DB table. Skipped silently if `portfolio.md` is absent.
3. **parsers/normalize.py** — Strip HTML, detect/translate language, group articles by story using fuzzy Jaccard similarity (threshold 0.40) with a 7-day freshness window. Assigns confidence levels. Flags sponsored Packet Pushers episodes as vendor.
4. **synthesis/synthesize.py** — Pull story groups from DB, pre-filter zero-signal groups, call Claude API, write brief to `output/briefs/`. Morning uses Sonnet; midday uses Haiku by default.
5. **parsers/candidates_writer.py** *(morning only)* — Regenerate `candidates.md` from the `candidates` DB table after synthesis.
6. **delivery/send_brief.py** — Check for actionable content; suppress and notify if no Tier 1/2 stories. Render brief as HTML email with graphical header (tier count tiles + theme callout) and send via SMTP.

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
python parsers/normalize.py
python parsers/portfolio_parser.py          # requires portfolio.md
python synthesis/synthesize.py --brief-type morning [--hours-back 24] [--max-stories 80] [--model MODEL]
python delivery/send_brief.py --brief-type morning
python maintenance/cleanup_db.py --dry-run  # preview DB retention cleanup
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

43 feeds defined in `config/feeds.yaml`. Categories and trust levels:

| Field | Values |
|-------|--------|
| `category` | semiconductors, networking, ai, tech, finance, local_safety, culture, reddit, research, vendor, china |
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

When `portfolio.md` is present, the midday brief gains a **Portfolio Signals** section (exception-based — only renders when a trigger event has occurred) and a **Candidate Signals** section (new tickers with conviction ≥ 10).

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

Deletes are logged to `logs/cleanup.log`.

## Project Structure

```
main.py                   # Pipeline orchestrator
config/
  feeds.yaml              # 43 RSS feeds with trust levels
  prompts/
    morning_system.md     # Claude system prompt — morning brief
    midday_system.md      # Claude system prompt — midday brief (+ portfolio/candidates)
  .env                    # API keys (not committed)
data/
  newsfeed.db             # SQLite runtime DB (not committed)
fetchers/
  rss_fetcher.py          # RSS/Atom fetch + raw_articles write
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

Story deduplication uses two-pass matching:
1. Exact title-token hash (fast path)
2. Fuzzy Jaccard similarity ≥ 0.40 against groups seen in the last 7 days, with a 7-day freshness window to prevent merging unrelated same-topic stories

## Dependencies

Key packages: `feedparser`, `anthropic`, `beautifulsoup4`, `deep-translator`, `langdetect`, `python-dotenv`, `PyYAML`, `Markdown`.

Full pinned list in `requirements.txt`.
