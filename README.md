# newsfeed

Personal RSS-to-email news briefing pipeline. Fetches 37 curated feeds, normalizes and deduplicates articles, synthesizes them via Claude, and delivers a formatted brief to email.

## Pipeline

```
rss_fetcher.py → normalize.py → synthesize.py → send_brief.py
```

1. **fetchers/rss_fetcher.py** — Fetch all feeds from `config/feeds.yaml`, write raw articles to SQLite. Deduplicates by URL hash.
2. **parsers/normalize.py** — Strip HTML, detect/translate language, group articles by story, assign confidence levels.
3. **synthesis/synthesize.py** — Pull story groups from DB, call Claude API, write brief to `output/briefs/`.
4. **delivery/send_brief.py** — Render brief as HTML email and send via SMTP.

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
SMTP_HOST=...
SMTP_PORT=...
SMTP_USER=...
SMTP_PASS=...
SMTP_TO=...
```

## Usage

Run the full pipeline:

```bash
python fetchers/rss_fetcher.py
python parsers/normalize.py
python synthesis/synthesize.py --brief-type morning
python delivery/send_brief.py --brief-type morning
```

Each script supports `--dry-run` to print output without writing to DB or sending email.

### Individual script options

```bash
# Fetch only a specific category
python fetchers/rss_fetcher.py --category semiconductors

# Normalize with a cap
python parsers/normalize.py --limit 200

# Synthesize with custom lookback window
python synthesis/synthesize.py --brief-type midday --hours-back 8

# Send a specific brief file
python delivery/send_brief.py --brief-path output/briefs/2026-03-10_0700_morning.md
```

## Feed Configuration

Feeds are defined in `config/feeds.yaml`. Each entry has:

| Field | Values |
|-------|--------|
| `category` | semiconductors, networking, ai, geopolitics, local, research, ... |
| `trust_level` | `high` \| `medium` \| `vendor` \| `state_adjacent` \| `research` \| `zeitgeist` |

Confidence is assigned during normalization:
- **high** — 3+ independent sources on the same story
- **medium** — 2 sources, or a single high-trust source
- **low** — single source, no established track record

## Project Structure

```
config/
  feeds.yaml          # 37 RSS feeds
  prompts/            # Claude system prompts (morning, midday)
  .env                # API keys (not committed)
data/
  newsfeed.db         # SQLite runtime DB (not committed)
fetchers/
  rss_fetcher.py
parsers/
  normalize.py
synthesis/
  synthesize.py
delivery/
  send_brief.py
output/
  briefs/             # Generated brief .md files (not committed)
logs/                 # Run logs (not committed)
```

## Brief Types

- **morning** — 24-hour lookback, up to 80 story groups
- **midday** — 8-hour lookback

## Dependencies

Key packages: `feedparser`, `anthropic`, `beautifulsoup4`, `deep-translator`, `langdetect`, `python-dotenv`, `schedule`, `PyYAML`, `Markdown`.

Full pinned list in `requirements.txt`.
