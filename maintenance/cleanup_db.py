"""
cleanup_db.py — Weekly DB retention cleanup.

Retention policy:
  raw_articles   30 days
  parsed_articles 30 days
  story_groups   60 days
  fetch_log      30 days
  brief_history  indefinite

Rows deleted are logged to logs/cleanup.log.

Usage:
    python maintenance/cleanup_db.py [--dry-run]

Cron (weekly, Sunday 03:00 PT):
    CRON_TZ=America/Los_Angeles
    0 3 * * 0  /opt/newsfeed/venv/bin/python /opt/newsfeed/maintenance/cleanup_db.py
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
LOG_PATH = BASE_DIR / "logs" / "cleanup.log"

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

# Retention windows per table (None = keep indefinitely)
RETENTION: dict[str, int | None] = {
    "raw_articles":    30,
    "parsed_articles": 30,
    "story_groups":    60,
    "fetch_log":       30,
    "brief_history":   None,
}

# Column used for date filtering per table
DATE_COL: dict[str, str] = {
    "raw_articles":    "fetched_at",
    "parsed_articles": "normalized_at",
    "story_groups":    "last_seen",
    "fetch_log":       "fetched_at",
}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def cleanup(conn: sqlite3.Connection, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    total_deleted = 0

    for table, days in RETENTION.items():
        if days is None:
            log.info("%-20s  retained indefinitely (skipping)", table)
            continue

        if not table_exists(conn, table):
            log.info("%-20s  table does not exist (skipping)", table)
            continue

        date_col = DATE_COL[table]
        cutoff = (now - timedelta(days=days)).isoformat()

        # Count first
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {date_col} < ?", (cutoff,)
        ).fetchone()
        count = count_row[0] if count_row else 0

        if count == 0:
            log.info("%-20s  0 rows to delete (cutoff %s)", table, cutoff[:10])
            continue

        if dry_run:
            log.info("%-20s  DRY RUN: would delete %d rows (cutoff %s)", table, count, cutoff[:10])
        else:
            conn.execute(f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,))
            conn.commit()
            log.info("%-20s  deleted %d rows (cutoff %s)", table, count, cutoff[:10])
            total_deleted += count

    if not dry_run:
        # Reclaim space
        conn.execute("VACUUM")
        conn.commit()
        log.info("VACUUM complete — %d total rows deleted this run", total_deleted)
    else:
        log.info("Dry run complete — no changes made")


def main() -> None:
    parser = argparse.ArgumentParser(description="DB retention cleanup")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("DB not found at %s", DB_PATH)
        sys.exit(1)

    log.info("=== DB cleanup START — dry_run=%s ===", args.dry_run)
    conn = sqlite3.connect(DB_PATH)
    try:
        cleanup(conn, args.dry_run)
    finally:
        conn.close()
    log.info("=== DB cleanup DONE ===")


if __name__ == "__main__":
    main()
