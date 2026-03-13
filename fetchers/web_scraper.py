"""
web_scraper.py — Fetch JS-rendered pages for sources where RSS is unavailable or incomplete.

Writes to raw_articles using the same schema as rss_fetcher.py so downstream pipeline
is unaware of the fetch method.

Usage:
    python fetchers/web_scraper.py [--dry-run]

Options:
    --dry-run       Print scraped entries without writing to DB
"""

import argparse
import asyncio
import hashlib
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
LOG_PATH = BASE_DIR / "logs" / "web_scraper.log"

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

ARTICLE_MAX_AGE_DAYS = 7

# Each target defines how to scrape one source.
# selector hierarchy: try each in order until one returns results.
SCRAPE_TARGETS = [
    {
        "name": "Morgan Stanley Insights",
        "url": "https://www.morganstanley.com/insights",
        "category": "macro",
        "trust_level": "medium",
        "wait_for": "article, .ms-insight-card, .card, [class*='insight'], [class*='article-card']",
        "article_selectors": [
            "article",
            ".ms-insight-card",
            "[class*='insight-card']",
            "[class*='article-card']",
            ".card",
        ],
        "title_selectors": ["h1", "h2", "h3", "[class*='title']", "[class*='headline']"],
        "link_attr": "href",
        "date_selectors": ["time", "[class*='date']", "[class*='published']", "[datetime]"],
        "summary_selectors": ["p", "[class*='description']", "[class*='summary']", "[class*='excerpt']"],
        "base_url": "https://www.morganstanley.com",
    },
]


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash    TEXT NOT NULL UNIQUE,
            source_name TEXT NOT NULL,
            source_url  TEXT NOT NULL,
            category    TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            title       TEXT,
            url         TEXT NOT NULL,
            published   TEXT,
            summary     TEXT,
            content     TEXT,
            fetched_at  TEXT NOT NULL,
            processed   INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_raw_articles_fetched_at ON raw_articles(fetched_at);
        CREATE INDEX IF NOT EXISTS idx_raw_articles_category   ON raw_articles(category);
        CREATE INDEX IF NOT EXISTS idx_raw_articles_processed  ON raw_articles(processed);

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


def upsert_source_health(conn: sqlite3.Connection, name: str, url: str,
                          fetched_at: str, status: str, count: int) -> None:
    last_success_at = fetched_at if status == "ok" else None
    updated = conn.execute(
        """
        UPDATE source_health
        SET url = ?, format = 'scrape', last_fetch_at = ?, last_status = ?, article_count = ?
            {sc}
        WHERE source_name = ?
        """.format(sc=", last_success_at = ?" if last_success_at else ""),
        (
            *([url, fetched_at, status, count, last_success_at, name]
              if last_success_at else
              [url, fetched_at, status, count, name]),
        ),
    ).rowcount
    if updated == 0:
        conn.execute(
            """
            INSERT INTO source_health (source_name, url, format, last_fetch_at,
                                       last_success_at, last_status, article_count)
            VALUES (?, ?, 'scrape', ?, ?, ?, ?)
            """,
            (name, url, fetched_at, last_success_at, status, count),
        )
    conn.commit()


async def scrape_target(target: dict, dry_run: bool, conn: sqlite3.Connection | None) -> dict:
    """Scrape one target using Playwright. Returns stats dict."""
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    name = target["name"]
    source_url = target["url"]
    category = target["category"]
    trust_level = target["trust_level"]
    base_url = target.get("base_url", "")
    fetched_at = datetime.now(timezone.utc).isoformat()

    log.info("Scraping: %s", name)

    articles_found: list[dict] = []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await page.goto(source_url, wait_until="networkidle", timeout=30000)

            # Try to wait for article elements
            try:
                await page.wait_for_selector(target["wait_for"], timeout=10000)
            except PWTimeout:
                log.warning("%s: wait_for selector timed out — continuing anyway", name)

            # Try each article selector until we get results
            article_els = []
            for sel in target["article_selectors"]:
                els = await page.query_selector_all(sel)
                if els:
                    article_els = els
                    log.debug("%s: matched %d articles with selector '%s'", name, len(els), sel)
                    break

            if not article_els:
                log.warning("%s: no article elements found on page", name)
                await browser.close()
                return {"source": name, "seen": 0, "new": 0, "success": True, "error": None,
                        "fetched_at": fetched_at, "status": "empty"}

            cutoff = datetime.now(timezone.utc) - timedelta(days=ARTICLE_MAX_AGE_DAYS)

            for el in article_els[:30]:  # cap at 30 per run
                # Extract link — check element itself, then children
                href = None
                link_el = await el.query_selector("a")
                if link_el:
                    href = await link_el.get_attribute("href")
                if not href:
                    href = await el.get_attribute("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = base_url + href
                if not href.startswith("http"):
                    continue

                # Extract title
                title = None
                for sel in target["title_selectors"]:
                    title_el = await el.query_selector(sel)
                    if title_el:
                        title = (await title_el.inner_text()).strip()
                        if title:
                            break
                if not title:
                    continue

                # Extract date
                published = None
                for sel in target["date_selectors"]:
                    date_el = await el.query_selector(sel)
                    if date_el:
                        dt_attr = await date_el.get_attribute("datetime")
                        if dt_attr:
                            try:
                                pub_dt = datetime.fromisoformat(dt_attr)
                                if pub_dt.tzinfo is None:
                                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                                published = pub_dt.isoformat()
                                break
                            except ValueError:
                                pass
                        text = (await date_el.inner_text()).strip()
                        if text:
                            published = text  # store as-is; normalize.py handles parsing
                            break

                # Skip stale articles when published date is parseable
                if published:
                    try:
                        pub_dt = datetime.fromisoformat(published)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            log.debug("Skipping stale article: %s", title[:60])
                            continue
                    except ValueError:
                        pass

                # Extract summary
                summary = None
                for sel in target["summary_selectors"]:
                    sum_el = await el.query_selector(sel)
                    if sum_el:
                        summary = (await sum_el.inner_text()).strip() or None
                        if summary:
                            break

                articles_found.append({
                    "title": title,
                    "url": href,
                    "published": published,
                    "summary": summary,
                })

            await browser.close()

    except Exception as exc:
        log.error("Exception scraping %s: %s", name, exc)
        return {"source": name, "seen": 0, "new": 0, "success": False, "error": str(exc),
                "fetched_at": fetched_at, "status": "error"}

    seen = len(articles_found)
    new_count = 0

    for art in articles_found:
        if dry_run:
            print(f"  [{trust_level}] {art['title'][:80]}")
            print(f"    {art['url']}")
            new_count += 1
            continue

        uhash = url_hash(art["url"])
        try:
            conn.execute(
                """
                INSERT INTO raw_articles
                    (url_hash, source_name, source_url, category, trust_level,
                     title, url, published, summary, content, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (uhash, name, source_url, category, trust_level,
                 art["title"], art["url"], art["published"], art["summary"], fetched_at),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            pass  # already have this article
        except sqlite3.Error as exc:
            log.error("DB error inserting %s: %s", art["url"], exc)

    if not dry_run and conn:
        conn.commit()

    status = "ok" if seen > 0 else "empty"
    log.info("  %s: %d seen, %d new", name, seen, new_count)
    return {"source": name, "seen": seen, "new": new_count, "success": True, "error": None,
            "fetched_at": fetched_at, "status": status}


async def run(dry_run: bool) -> None:
    conn = None
    if not dry_run:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)

    total_seen = 0
    total_new = 0

    for target in SCRAPE_TARGETS:
        stats = await scrape_target(target, dry_run=dry_run, conn=conn)
        total_seen += stats["seen"]
        total_new += stats["new"]
        if conn and not dry_run:
            upsert_source_health(
                conn,
                target["name"],
                target["url"],
                stats["fetched_at"],
                stats.get("status", "error" if not stats["success"] else "ok"),
                stats["seen"],
            )

    if conn:
        conn.close()

    log.info(
        "Scrape run complete — %d sources, %d articles seen, %d new",
        len(SCRAPE_TARGETS), total_seen, total_new,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape JS-rendered sources into SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Print entries without writing to DB")
    args = parser.parse_args()

    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
