"""
portfolio_parser.py — Parse portfolio.md and upsert into the portfolio DB table.

Called by main.py before the synthesis stage if portfolio.md exists.

Usage:
    python parsers/portfolio_parser.py [--dry-run]
"""

import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
PORTFOLIO_PATH = BASE_DIR / "portfolio.md"
LOG_PATH = BASE_DIR / "logs" / "portfolio_parser.log"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def init_portfolio_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            ticker       TEXT PRIMARY KEY,
            thesis       TEXT,
            sector       TEXT,
            thesis_source TEXT,
            list_type    TEXT NOT NULL,   -- 'holding' | 'watchlist' | 'index'
            last_updated TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidates (
            ticker             TEXT PRIMARY KEY,
            company_name       TEXT,
            macro_score        INTEGER,
            fundamentals_score INTEGER,
            conviction_score   INTEGER,
            thesis             TEXT,
            source_name        TEXT,
            source_url         TEXT,
            first_seen         TEXT NOT NULL,
            last_updated       TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS score_history (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker             TEXT NOT NULL,
            macro_score        INTEGER,
            fundamentals_score INTEGER,
            conviction_score   INTEGER,
            reason             TEXT,
            scored_at          TEXT NOT NULL
        );
    """)
    conn.commit()


def parse_portfolio_md(text: str) -> dict:
    """
    Parse portfolio.md into a structured dict.
    Returns {'index': [...], 'holdings': [...], 'watchlist': [...]}.
    Each holding/watchlist entry: {'ticker': str, 'thesis': str, 'sector': str, ...}
    """
    result: dict = {"index": [], "holdings": [], "watchlist": []}
    section = None
    lines = text.splitlines()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") and not stripped.startswith("##"):
            continue

        # Section headers
        if stripped.startswith("## Index Holdings"):
            section = "index"
            continue
        if stripped.startswith("## Holdings"):
            section = "holdings"
            continue
        if stripped.startswith("## Watchlist"):
            section = "watchlist"
            continue
        if stripped.startswith("##"):
            section = None
            continue

        # Skip comment lines
        if stripped.startswith("Alert threshold:") or stripped.startswith("|--") or stripped.startswith("| Ticker"):
            continue

        if section == "index":
            # Parse comma-separated tickers
            tickers = [t.strip().upper() for t in stripped.split(",") if t.strip()]
            result["index"].extend(tickers)

        elif section == "holdings":
            # Parse table row: | Ticker | Thesis | Sector |
            if stripped.startswith("|") and "|" in stripped[1:]:
                parts = [p.strip() for p in stripped.strip("|").split("|")]
                if len(parts) >= 2:
                    ticker = parts[0].strip().upper()
                    thesis = parts[1].strip() if len(parts) > 1 else ""
                    sector = parts[2].strip() if len(parts) > 2 else ""
                    if ticker and re.match(r'^[A-Z]{1,5}$', ticker):
                        result["holdings"].append({"ticker": ticker, "thesis": thesis, "sector": sector})

        elif section == "watchlist":
            # Parse table row: | Ticker | Thesis | Buy Trigger |
            if stripped.startswith("|") and "|" in stripped[1:]:
                parts = [p.strip() for p in stripped.strip("|").split("|")]
                if len(parts) >= 2:
                    ticker = parts[0].strip().upper()
                    thesis = parts[1].strip() if len(parts) > 1 else ""
                    buy_trigger = parts[2].strip() if len(parts) > 2 else ""
                    if ticker and re.match(r'^[A-Z]{1,5}$', ticker):
                        result["watchlist"].append({
                            "ticker": ticker,
                            "thesis": thesis,
                            "buy_trigger": buy_trigger,
                        })

    return result


def upsert_portfolio(conn: sqlite3.Connection, parsed: dict, dry_run: bool) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0

    # Index holdings
    for ticker in parsed["index"]:
        if dry_run:
            print(f"  index: {ticker}")
        else:
            conn.execute(
                """
                INSERT INTO portfolio (ticker, thesis, sector, thesis_source, list_type, last_updated)
                VALUES (?, ?, ?, ?, 'index', ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    list_type='index', last_updated=excluded.last_updated
                """,
                (ticker, None, None, None, now),
            )
        count += 1

    # Holdings
    for h in parsed["holdings"]:
        if dry_run:
            print(f"  holding: {h['ticker']} | {h['thesis'][:50]} | {h['sector']}")
        else:
            conn.execute(
                """
                INSERT INTO portfolio (ticker, thesis, sector, thesis_source, list_type, last_updated)
                VALUES (?, ?, ?, 'user', 'holding', ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    thesis=excluded.thesis,
                    sector=excluded.sector,
                    thesis_source='user',
                    list_type='holding',
                    last_updated=excluded.last_updated
                """,
                (h["ticker"], h["thesis"] or None, h["sector"] or None, now),
            )
        count += 1

    # Watchlist
    for w in parsed["watchlist"]:
        if dry_run:
            print(f"  watchlist: {w['ticker']} | {w['thesis'][:50]}")
        else:
            conn.execute(
                """
                INSERT INTO portfolio (ticker, thesis, sector, thesis_source, list_type, last_updated)
                VALUES (?, ?, ?, 'user', 'watchlist', ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    thesis=excluded.thesis,
                    thesis_source='user',
                    list_type='watchlist',
                    last_updated=excluded.last_updated
                """,
                (w["ticker"], w["thesis"] or None, None, now),
            )
        count += 1

    if not dry_run:
        conn.commit()

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse portfolio.md into DB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not PORTFOLIO_PATH.exists():
        log.info("portfolio.md not found at %s — skipping portfolio parse", PORTFOLIO_PATH)
        sys.exit(0)

    if not DB_PATH.exists():
        log.error("DB not found at %s — run rss_fetcher.py first", DB_PATH)
        sys.exit(1)

    text = PORTFOLIO_PATH.read_text()
    parsed = parse_portfolio_md(text)
    log.info(
        "Parsed portfolio.md: %d index, %d holdings, %d watchlist",
        len(parsed["index"]), len(parsed["holdings"]), len(parsed["watchlist"]),
    )

    conn = sqlite3.connect(DB_PATH)
    if not args.dry_run:
        init_portfolio_tables(conn)

    count = upsert_portfolio(conn, parsed, args.dry_run)
    conn.close()

    log.info("Done — %d portfolio entries %s", count, "previewed" if args.dry_run else "upserted")


if __name__ == "__main__":
    main()
