# DailyBrief

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

Run the full pipeline via the orchestrator:

```bash
python main.py --brief-type morning
python main.py --brief-type midday
python main.py --brief-type morning --skip-delivery   # synthesize only, no email
python main.py --brief-type morning --dry-run         # no API calls, no email
```

Or run individual stages:

```bash
python fetchers/rss_fetcher.py
python parsers/normalize.py
python synthesis/synthesize.py --brief-type morning [--hours-back 24] [--max-stories 80]
python delivery/send_brief.py --brief-type morning
```

Each script supports `--dry-run`.

## Cron Schedule

Runs are scheduled via crontab (`CRON_TZ=America/Los_Angeles`):

```
0 6    * * *   main.py --brief-type morning   # 6:00 AM PT
30 11  * * *   main.py --brief-type midday    # 11:30 AM PT
```

Logs: `logs/cron_morning.log`, `logs/cron_midday.log`

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
main.py               # Pipeline orchestrator
config/
  feeds.yaml          # 37 RSS feeds
  prompts/            # Claude system prompts (morning_system.md, midday_system.md)
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

Key packages: `feedparser`, `anthropic`, `beautifulsoup4`, `deep-translator`, `langdetect`, `python-dotenv`, `PyYAML`, `Markdown`.

Full pinned list in `requirements.txt`.
