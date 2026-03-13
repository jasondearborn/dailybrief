"""
rss_fetcher.py — Fetch all RSS/Atom feeds from config/feeds.yaml and store raw articles to SQLite.

Usage:
    python fetchers/rss_fetcher.py [--dry-run] [--category CATEGORY]

Options:
    --dry-run       Print fetched entries without writing to DB
    --category      Only fetch feeds in this category
"""

import argparse
import hashlib
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "feeds.yaml"
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
LOG_PATH = BASE_DIR / "logs" / "rss_fetcher.log"

# --- Logging ---
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

# feedparser is noisy; silence it
logging.getLogger("feedparser").setLevel(logging.WARNING)

# How long to wait between feed requests (seconds)
FETCH_DELAY = 1.0
# Per-feed request timeout (seconds)
FETCH_TIMEOUT = 15
# Articles older than this many days are skipped — prevents old cached entries
# from re-surfacing in briefs (e.g. SemiAnalysis backlog from Sept 2025)
ARTICLE_MAX_AGE_DAYS = 7


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash    TEXT NOT NULL UNIQUE,   -- sha256 of canonical URL, dedup key
            source_name TEXT NOT NULL,
            source_url  TEXT NOT NULL,
            category    TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            title       TEXT,
            url         TEXT NOT NULL,
            published   TEXT,                   -- ISO8601 string
            summary     TEXT,
            content     TEXT,
            fetched_at  TEXT NOT NULL,          -- ISO8601 UTC
            processed   INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_raw_articles_fetched_at  ON raw_articles(fetched_at);
        CREATE INDEX IF NOT EXISTS idx_raw_articles_category    ON raw_articles(category);
        CREATE INDEX IF NOT EXISTS idx_raw_articles_processed   ON raw_articles(processed);

        CREATE TABLE IF NOT EXISTS fetch_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name  TEXT NOT NULL,
            fetched_at   TEXT NOT NULL,
            entries_seen INTEGER NOT NULL DEFAULT 0,
            entries_new  INTEGER NOT NULL DEFAULT 0,
            success      INTEGER NOT NULL DEFAULT 1,
            error        TEXT
        );

        CREATE TABLE IF NOT EXISTS source_health (
            source_name     TEXT PRIMARY KEY,
            url             TEXT,
            format          TEXT NOT NULL DEFAULT 'rss',
            last_fetch_at   TEXT,
            last_success_at TEXT,
            last_status     TEXT,
            article_count   INTEGER,
            notes           TEXT
        );
    """)
    conn.commit()


def load_feeds(category_filter: str | None = None) -> list[dict]:
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    feeds = config.get("feeds", [])
    if category_filter:
        feeds = [fd for fd in feeds if fd.get("category") == category_filter]
    return feeds


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def parse_published(entry: feedparser.FeedParserDict) -> str | None:
    """Return ISO8601 UTC string from feedparser entry, or None."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return None


def get_content(entry: feedparser.FeedParserDict) -> str | None:
    """Extract full content if available, else summary."""
    if hasattr(entry, "content") and entry.content:
        return entry.content[0].get("value", "")
    return None


def upsert_source_health(conn: sqlite3.Connection, feed: dict, stats: dict) -> None:
    """Update source_health row for this feed based on fetch result."""
    now = stats["fetched_at"]
    if not stats["success"]:
        status = "error"
        last_success_at = None
    elif stats["seen"] == 0:
        status = "empty"
        last_success_at = None
    else:
        status = "ok"
        last_success_at = now

    # Try update first; insert if no existing row
    updated = conn.execute(
        """
        UPDATE source_health
        SET url = ?, last_fetch_at = ?, last_status = ?, article_count = ?
            {success_clause}
        WHERE source_name = ?
        """.format(success_clause=", last_success_at = ?" if last_success_at else ""),
        (
            *(([feed["url"], now, status, stats["seen"], last_success_at, feed["name"]])
              if last_success_at else
              [feed["url"], now, status, stats["seen"], feed["name"]]),
        ),
    ).rowcount

    if updated == 0:
        conn.execute(
            """
            INSERT INTO source_health (source_name, url, format, last_fetch_at,
                                       last_success_at, last_status, article_count)
            VALUES (?, ?, 'rss', ?, ?, ?, ?)
            """,
            (feed["name"], feed["url"], now, last_success_at, status, stats["seen"]),
        )
    conn.commit()


def update_backlog_for_unhealthy_sources(conn: sqlite3.Connection, run_at: str) -> None:
    """Append [source-health] open items to BACKLOG.md for any source that failed this run."""
    backlog_path = BASE_DIR / "BACKLOG.md"
    if not backlog_path.exists():
        return

    # Find sources with non-ok status fetched in this run (same minute)
    run_minute = run_at[:16]  # YYYY-MM-DDTHH:MM
    cur = conn.execute(
        "SELECT source_name, url, last_status FROM source_health "
        "WHERE last_status != 'ok' AND last_fetch_at LIKE ?",
        (run_minute + "%",),
    )
    unhealthy = cur.fetchall()
    if not unhealthy:
        return

    content = backlog_path.read_text()
    done_pos = content.find("\n## Done")
    if done_pos == -1:
        done_pos = len(content)

    appended = False
    for source_name, url, status in unhealthy:
        tag = f"[source-health] {source_name}"
        if tag in content:
            continue  # open item already exists
        entry = (
            f"\n### {tag}\n\n"
            f"Feed fetch returned status `{status}` for source **{source_name}** "
            f"(`{url}`). Investigate feed URL, paywall, or format change.\n\n---\n"
        )
        content = content[:done_pos] + entry + content[done_pos:]
        done_pos += len(entry)
        appended = True
        log.info("Added [source-health] backlog item for %s (status=%s)", source_name, status)

    if appended:
        backlog_path.write_text(content)


def fetch_feed(feed: dict, dry_run: bool, conn: sqlite3.Connection | None) -> dict:
    """Fetch a single feed. Returns stats dict."""
    name = feed["name"]
    url = feed["url"]
    category = feed["category"]
    trust_level = feed["trust_level"]
    fetched_at = datetime.now(timezone.utc).isoformat()

    log.info("Fetching: %s", name)

    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": "dailybrief-aggregator/1.0"}, agent=None)
    except Exception as exc:
        log.error("Exception fetching %s: %s", name, exc)
        return {"source": name, "seen": 0, "new": 0, "success": False, "error": str(exc), "fetched_at": fetched_at}

    if parsed.bozo and parsed.bozo_exception:
        # bozo means malformed feed — log but continue if we got entries
        log.warning("Bozo feed %s: %s", name, parsed.bozo_exception)

    entries = parsed.entries
    seen = len(entries)
    new_count = 0

    for entry in entries:
        link = entry.get("link", "").strip()
        if not link:
            continue

        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip() or None
        content = get_content(entry)
        published = parse_published(entry)

        # Skip articles older than ARTICLE_MAX_AGE_DAYS — prevents stale RSS
        # cache entries (e.g. SemiAnalysis backlog) from appearing in briefs.
        if published:
            try:
                pub_dt = datetime.fromisoformat(published)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - pub_dt > timedelta(days=ARTICLE_MAX_AGE_DAYS):
                    log.debug("Skipping stale article (%s): %s", published[:10], title[:60])
                    continue
            except ValueError:
                pass  # unparseable date — let it through

        uhash = url_hash(link)

        if dry_run:
            print(f"  [{trust_level}] {title[:80]}")
            print(f"    {link}")
            new_count += 1
            continue

        try:
            conn.execute(
                """
                INSERT INTO raw_articles
                    (url_hash, source_name, source_url, category, trust_level,
                     title, url, published, summary, content, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uhash, name, url, category, trust_level,
                 title, link, published, summary, content, fetched_at),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            # url_hash UNIQUE constraint — already have this article
            pass
        except sqlite3.Error as exc:
            log.error("DB error inserting %s: %s", link, exc)

    if not dry_run:
        conn.commit()
        conn.execute(
            """
            INSERT INTO fetch_log (source_name, fetched_at, entries_seen, entries_new, success)
            VALUES (?, ?, ?, ?, 1)
            """,
            (name, fetched_at, seen, new_count),
        )
        conn.commit()

    log.info("  %s: %d seen, %d new", name, seen, new_count)
    return {"source": name, "seen": seen, "new": new_count, "success": True, "error": None, "fetched_at": fetched_at}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch RSS feeds into SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Print entries without writing to DB")
    parser.add_argument("--category", default=None, help="Only fetch feeds in this category")
    args = parser.parse_args()

    feeds = load_feeds(category_filter=args.category)
    if not feeds:
        log.error("No feeds found (category filter: %s)", args.category)
        sys.exit(1)

    log.info("Starting fetch run — %d feeds, dry_run=%s", len(feeds), args.dry_run)

    conn = None
    if not args.dry_run:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)

    total_seen = 0
    total_new = 0
    errors = []
    run_at = datetime.now(timezone.utc).isoformat()

    for i, feed in enumerate(feeds):
        if i > 0:
            time.sleep(FETCH_DELAY)

        stats = fetch_feed(feed, dry_run=args.dry_run, conn=conn)
        total_seen += stats["seen"]
        total_new += stats["new"]
        if not stats["success"]:
            errors.append(stats["source"])
            if conn:
                conn.execute(
                    """
                    INSERT INTO fetch_log (source_name, fetched_at, entries_seen, entries_new, success, error)
                    VALUES (?, ?, 0, 0, 0, ?)
                    """,
                    (stats["source"], datetime.now(timezone.utc).isoformat(), stats["error"]),
                )
                conn.commit()
        if conn and not args.dry_run:
            upsert_source_health(conn, feed, stats)

    if conn and not args.dry_run:
        update_backlog_for_unhealthy_sources(conn, run_at)

    if conn:
        conn.close()

    log.info(
        "Fetch run complete — %d feeds, %d articles seen, %d new, %d errors",
        len(feeds), total_seen, total_new, len(errors),
    )
    if errors:
        log.warning("Failed feeds: %s", ", ".join(errors))


if __name__ == "__main__":
    main()
