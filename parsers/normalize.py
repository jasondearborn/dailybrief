"""
normalize.py — Parse and normalize raw articles from SQLite.

Responsibilities:
  - Pull unprocessed raw_articles
  - Strip HTML, clean text
  - Detect language; translate non-English title+text to English
  - Deduplicate: group articles covering the same story across sources
  - Assign confidence level based on source count and trust_level
  - Write parsed_articles and story_groups to DB
  - Mark raw_articles.processed = 1

Usage:
    python parsers/normalize.py [--dry-run] [--category CATEGORY] [--limit N]
"""

import argparse
import hashlib
import html
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException
from deep_translator import GoogleTranslator

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
LOG_PATH = BASE_DIR / "logs" / "normalize.log"

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

# --- Constants ---

# Max characters of cleaned body text to store
MAX_TEXT_CHARS = 4000

# Minimum text length to attempt language detection (too short = unreliable)
LANG_DETECT_MIN_CHARS = 20

# Delay between translation API calls (seconds) — be polite to free tier
TRANSLATE_DELAY = 0.5

# Stopwords for title normalization (dedup key)
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "that", "this", "it",
    "its", "into", "than", "then", "about", "up", "out", "over", "after",
    "how", "what", "who", "why", "when", "where", "which", "not", "no",
    "new", "more", "now",
}

# Sources where sponsor mentions in title/description should flag is_vendor=1
SPONSOR_FLAGGED_SOURCES = {"Packet Pushers Heavy Networking", "Packet Pushers Network Break"}
# Keywords indicating a sponsored episode
_SPONSOR_KEYWORDS = {"sponsor", "sponsored by", "brought to you by", "in partnership with"}

# Trust levels that qualify as "high credibility" single-source
HIGH_CREDIBILITY = {"high"}

# Trust levels treated as vendor/agenda-aware (never elevate confidence alone)
VENDOR_LEVELS = {"vendor", "state_adjacent"}

# Confidence thresholds
CONF_HIGH_SOURCE_COUNT = 3     # 3+ independent (non-vendor) sources → high
CONF_MEDIUM_SOURCE_COUNT = 2   # 2 sources → medium


# --- DB Setup ---

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS story_groups (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title_hash       TEXT NOT NULL UNIQUE,  -- dedup key: hash of normalized title tokens
            representative_title TEXT,
            first_seen       TEXT NOT NULL,
            last_seen        TEXT NOT NULL,
            source_count     INTEGER NOT NULL DEFAULT 1,
            confidence       TEXT NOT NULL DEFAULT 'low',  -- low | medium | high
            has_divergence   INTEGER NOT NULL DEFAULT 0,   -- 1 if high-credibility sources disagree
            categories       TEXT,   -- comma-separated set
            top_category     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_story_groups_title_hash ON story_groups(title_hash);
        CREATE INDEX IF NOT EXISTS idx_story_groups_confidence ON story_groups(confidence);

        CREATE TABLE IF NOT EXISTS parsed_articles (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_article_id   INTEGER NOT NULL REFERENCES raw_articles(id),
            story_group_id   INTEGER REFERENCES story_groups(id),
            source_name      TEXT NOT NULL,
            category         TEXT NOT NULL,
            trust_level      TEXT NOT NULL,
            title            TEXT,
            url              TEXT NOT NULL,
            published        TEXT,
            text             TEXT,   -- cleaned body text, truncated
            language         TEXT,   -- ISO 639-1 detected language (null = English or undetected)
            is_vendor        INTEGER NOT NULL DEFAULT 0,
            is_research      INTEGER NOT NULL DEFAULT 0,
            is_zeitgeist     INTEGER NOT NULL DEFAULT 0,
            normalized_at    TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_parsed_articles_story_group ON parsed_articles(story_group_id);
        CREATE INDEX IF NOT EXISTS idx_parsed_articles_category     ON parsed_articles(category);
        CREATE INDEX IF NOT EXISTS idx_parsed_articles_published    ON parsed_articles(published);
    """)
    conn.commit()

    # Migrations — add columns introduced after initial schema creation
    existing = {row[1] for row in conn.execute("PRAGMA table_info(parsed_articles)")}
    if "language" not in existing:
        conn.execute("ALTER TABLE parsed_articles ADD COLUMN language TEXT")
        conn.commit()


# --- Text Cleaning ---

def strip_html(raw: str | None) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    if not raw:
        return ""
    # Unescape HTML entities first
    raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "html.parser")
    # Remove script/style blocks entirely
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_title(title: str | None) -> str:
    if not title:
        return ""
    return strip_html(title)[:512]


def clean_text(summary: str | None, content: str | None) -> str:
    """Return best available cleaned body text, truncated."""
    # Prefer content (full article body) over summary
    raw = content if content else summary
    text = strip_html(raw)
    return text[:MAX_TEXT_CHARS]


# --- Translation ---

def detect_language(text: str) -> str | None:
    """Return ISO 639-1 language code, or None if detection fails/unreliable."""
    if not text or len(text) < LANG_DETECT_MIN_CHARS:
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None


# langdetect uses 'zh-cn'/'zh-tw'; deep-translator needs 'zh-CN'/'zh-TW'
_LANG_CODE_MAP = {
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
}


def translate_to_english(text: str, source_lang: str) -> str:
    """Translate text to English using Google Translate free tier. Returns original on failure."""
    if not text:
        return text
    # Normalize lang code for deep-translator
    source_lang = _LANG_CODE_MAP.get(source_lang, source_lang)
    # Truncate to 4500 chars — Google free tier limit per call
    chunk = text[:4500]
    try:
        translated = GoogleTranslator(source=source_lang, target="en").translate(chunk)
        time.sleep(TRANSLATE_DELAY)
        return translated or text
    except Exception as exc:
        log.warning("Translation failed (%s→en): %s", source_lang, exc)
        return text


def maybe_translate(title: str, body: str) -> tuple[str, str, str | None]:
    """
    Detect language of combined title+body. If non-English, translate both.
    Returns (title, body, detected_lang). detected_lang is None if English or undetected.
    """
    # Use title + first 200 chars of body for detection (faster, more reliable)
    sample = f"{title} {body[:200]}".strip()
    lang = detect_language(sample)

    if lang is None or lang == "en":
        return title, body, lang

    log.info("  Non-English detected: %s — translating", lang)
    translated_title = translate_to_english(title, lang) if title else title
    translated_body = translate_to_english(body, lang) if body else body
    return translated_title, translated_body, lang


# --- Deduplication ---

def normalize_title_tokens(title: str) -> frozenset[str]:
    """Lowercase, strip punctuation, remove stopwords → frozenset of tokens."""
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    tokens = title.split()
    tokens = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    return frozenset(tokens)


def title_hash(tokens: frozenset[str]) -> str:
    """Stable hash of a normalized token set."""
    key = " ".join(sorted(tokens))
    return hashlib.sha256(key.encode()).hexdigest()


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


# --- Confidence Scoring ---

def compute_confidence(rows: list[dict]) -> str:
    """
    Confidence rules from HANDOFF.md:
    - high: 3+ independent (non-vendor) sources
    - medium: 1-2 sources, OR single high-credibility source (SemiAnalysis etc.)
    - low: single source, no established track record
    Vendor sources never count toward independent source count.
    """
    independent = [r for r in rows if r["trust_level"] not in VENDOR_LEVELS]
    independent_count = len({r["source_name"] for r in independent})

    if independent_count >= CONF_HIGH_SOURCE_COUNT:
        return "high"

    if independent_count >= CONF_MEDIUM_SOURCE_COUNT:
        return "medium"

    # Single source — check if it's high-credibility
    if independent_count == 1:
        tl = independent[0]["trust_level"] if independent else None
        if tl in HIGH_CREDIBILITY:
            return "medium"  # single high-credibility source → medium per HANDOFF
        return "low"

    # Only vendor/state_adjacent sources
    return "low"


# --- Main Normalization Logic ---

def fetch_unprocessed(conn: sqlite3.Connection, category: str | None, limit: int | None) -> list[dict]:
    query = """
        SELECT id, source_name, source_url, category, trust_level,
               title, url, published, summary, content
        FROM raw_articles
        WHERE processed = 0
    """
    params: list = []
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY fetched_at ASC"
    if limit:
        query += f" LIMIT {int(limit)}"

    cur = conn.execute(query, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_or_create_story_group(
    conn: sqlite3.Connection,
    thash: str,
    title: str,
    now: str,
    category: str,
) -> int:
    """Return story_group.id, creating if needed."""
    row = conn.execute(
        "SELECT id FROM story_groups WHERE title_hash = ?", (thash,)
    ).fetchone()
    if row:
        return row[0]
    conn.execute(
        """
        INSERT INTO story_groups (title_hash, representative_title, first_seen, last_seen, categories, top_category)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (thash, title[:512], now, now, category, category),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_story_group(conn: sqlite3.Connection, group_id: int, category: str, now: str) -> None:
    """Increment source_count, update last_seen and categories."""
    row = conn.execute(
        "SELECT source_count, categories FROM story_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not row:
        return
    count = row[0] + 1
    cats = set((row[1] or "").split(","))
    cats.add(category)
    cats.discard("")
    conn.execute(
        """
        UPDATE story_groups
        SET source_count = ?, last_seen = ?, categories = ?
        WHERE id = ?
        """,
        (count, now, ",".join(sorted(cats)), group_id),
    )


def update_confidence_all(conn: sqlite3.Connection) -> None:
    """Recompute confidence for all story_groups based on their parsed_articles."""
    groups = conn.execute("SELECT id FROM story_groups").fetchall()
    for (gid,) in groups:
        rows = conn.execute(
            "SELECT source_name, trust_level FROM parsed_articles WHERE story_group_id = ?",
            (gid,),
        ).fetchall()
        row_dicts = [{"source_name": r[0], "trust_level": r[1]} for r in rows]
        conf = compute_confidence(row_dicts)
        conn.execute("UPDATE story_groups SET confidence = ? WHERE id = ?", (conf, gid))


def normalize_batch(
    conn: sqlite3.Connection,
    raw_rows: list[dict],
    dry_run: bool,
) -> tuple[int, int]:
    """Process a batch of raw_article rows. Returns (written, skipped)."""
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    skipped = 0

    # Build an in-memory index of title_hash → group_id for this batch
    # (reduces DB round-trips for articles that cluster together in a single run)
    hash_to_group: dict[str, int] = {}

    for raw in raw_rows:
        ctitle = clean_title(raw["title"])
        ctext = clean_text(raw["summary"], raw["content"])

        # Translate non-English content to English before dedup and storage
        ctitle, ctext, detected_lang = maybe_translate(ctitle, ctext)

        if not ctitle and not ctext:
            # Nothing useful — still mark processed so we don't retry forever
            if not dry_run:
                conn.execute("UPDATE raw_articles SET processed = 1 WHERE id = ?", (raw["id"],))
            skipped += 1
            continue

        tokens = normalize_title_tokens(ctitle)

        # Find best matching existing group via Jaccard similarity
        # We check the in-batch index first, then fall back to DB lookup by hash
        thash = title_hash(tokens)

        # Try exact hash match first (same story, exact same normalized tokens)
        group_id = hash_to_group.get(thash)
        if group_id is None and not dry_run:
            group_id_row = conn.execute(
                "SELECT id FROM story_groups WHERE title_hash = ?", (thash,)
            ).fetchone()
            if group_id_row:
                group_id = group_id_row[0]

        trust_level = raw["trust_level"]
        is_vendor = 1 if trust_level == "vendor" else 0
        is_research = 1 if trust_level == "research" else 0
        is_zeitgeist = 1 if trust_level == "zeitgeist" else 0

        # Packet Pushers (and similar): flag sponsored episodes as vendor
        if raw["source_name"] in SPONSOR_FLAGGED_SOURCES and not is_vendor:
            sample = f"{ctitle} {raw.get('summary', '') or ''}".lower()
            if any(kw in sample for kw in _SPONSOR_KEYWORDS):
                is_vendor = 1
                log.debug("Sponsor mention detected in %s: %s", raw["source_name"], ctitle[:60])

        if dry_run:
            conf_label = "(new group)" if group_id is None else f"(group {group_id})"
            print(
                f"  [{raw['category']}/{trust_level}] {conf_label} {ctitle[:80]}"
            )
            written += 1
            continue

        # Get or create story group
        if group_id is None:
            group_id = get_or_create_story_group(conn, thash, ctitle, now, raw["category"])
        else:
            update_story_group(conn, group_id, raw["category"], now)

        hash_to_group[thash] = group_id

        conn.execute(
            """
            INSERT INTO parsed_articles
                (raw_article_id, story_group_id, source_name, category, trust_level,
                 title, url, published, text, language, is_vendor, is_research, is_zeitgeist,
                 normalized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw["id"], group_id, raw["source_name"], raw["category"], trust_level,
                ctitle, raw["url"], raw["published"], ctext, detected_lang,
                is_vendor, is_research, is_zeitgeist, now,
            ),
        )
        conn.execute("UPDATE raw_articles SET processed = 1 WHERE id = ?", (raw["id"],))
        written += 1

    if not dry_run:
        conn.commit()
        update_confidence_all(conn)
        conn.commit()

    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize raw articles into parsed_articles")
    parser.add_argument("--dry-run", action="store_true", help="Print output without writing to DB")
    parser.add_argument("--category", default=None, help="Only process this category")
    parser.add_argument("--limit", type=int, default=None, help="Max articles to process")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("DB not found at %s — run rss_fetcher.py first", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    if not args.dry_run:
        init_db(conn)

    raw_rows = fetch_unprocessed(conn, args.category, args.limit)
    log.info(
        "Normalizing %d raw articles (category=%s, dry_run=%s)",
        len(raw_rows), args.category, args.dry_run,
    )

    if not raw_rows:
        log.info("Nothing to process.")
        conn.close()
        return

    written, skipped = normalize_batch(conn, raw_rows, dry_run=args.dry_run)
    conn.close()

    log.info("Done — %d written, %d skipped (empty content)", written, skipped)


if __name__ == "__main__":
    main()
