"""
synthesize.py — Pull parsed articles from SQLite, call Claude API, save brief.

Usage:
    python synthesis/synthesize.py --brief-type morning [--dry-run] [--hours-back 24] [--max-stories 80]
    python synthesis/synthesize.py --brief-type midday  [--dry-run] [--hours-back 8]

Options:
    --brief-type    morning | midday (required)
    --dry-run       Build and print the prompt without calling the API
    --hours-back    How many hours of articles to include (default: 24 morning, 8 midday)
    --max-stories   Cap story groups sent to Claude (default: 80)
    --model         Override Claude model (default: claude-sonnet-4-6)
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
import anthropic

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
PROMPTS_DIR = BASE_DIR / "config" / "prompts"
OUTPUT_DIR = BASE_DIR / "output" / "briefs"
LOG_PATH = BASE_DIR / "logs" / "synthesize.log"
ENV_PATH = BASE_DIR / "config" / ".env"

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

# --- Config ---
DEFAULT_MODEL = "claude-sonnet-4-6"

# Characters of article text to include per article in the prompt
# Keeps token usage reasonable; Claude doesn't need the full 4000-char body
PROMPT_TEXT_CHARS = 400

# Form 4 source name — suppress until EDGAR enrichment is implemented.
# Form 4 stubs have no transaction detail (type/shares/price) and are low value.
FORM4_SOURCE = "SEC Form 4 Insider Transactions"

# Categories included in midday brief
MIDDAY_CATEGORIES = {"finance", "semiconductors", "ai"}

# Brief type defaults
BRIEF_DEFAULTS = {
    "morning": {"hours_back": 24, "max_stories": 80},
    "midday":  {"hours_back": 8,  "max_stories": 40},
}

# Per-category story slot allocation for morning brief.
# Ensures balanced coverage even when high-trust sources saturate confidence ranking.
# Categories not listed share any remaining slots up to max_stories.
MORNING_CATEGORY_SLOTS = {
    "semiconductors": 18,
    "ai":             15,
    "finance":        15,
    "tech":           12,
    "networking":     8,
    "local_safety":   10,
    "culture":        8,
    "reddit":         5,
    "research":       5,
    "vendor":         4,
    "china":          4,
}

# Midday is already category-filtered; no per-category slots needed


# --- DB ---

def init_brief_history(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS brief_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_type    TEXT NOT NULL,
            generated_at  TEXT NOT NULL,
            model         TEXT NOT NULL,
            story_count   INTEGER NOT NULL,
            article_count INTEGER NOT NULL,
            output_path   TEXT,
            prompt_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens    INTEGER,
            cache_creation_tokens INTEGER,
            success       INTEGER NOT NULL DEFAULT 1,
            error         TEXT
        );
    """)
    # Migrate existing DBs that lack cache columns
    for col in ("cache_read_tokens", "cache_creation_tokens"):
        try:
            conn.execute(f"ALTER TABLE brief_history ADD COLUMN {col} INTEGER")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def _fetch_groups_query(
    conn: sqlite3.Connection,
    cutoff: str,
    category: str | None,
    limit: int,
) -> list[dict]:
    """Run a single ranked query for one category (or all if category is None)."""
    params: list = [cutoff]
    cat_filter = ""
    if category:
        cat_filter = "AND sg.top_category = ?"
        params.append(category)
    params.append(limit)

    query = f"""
        SELECT
            sg.id,
            sg.representative_title,
            sg.confidence,
            sg.source_count,
            sg.categories,
            sg.has_divergence
        FROM story_groups sg
        WHERE sg.last_seen >= ?
        {cat_filter}
        ORDER BY
            CASE sg.confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            sg.source_count DESC,
            sg.last_seen DESC
        LIMIT ?
    """
    cur = conn.execute(query, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_story_groups(
    conn: sqlite3.Connection,
    brief_type: str,
    hours_back: int,
    max_stories: int,
) -> list[dict]:
    """
    Fetch story_groups with their parsed_articles for the given window.

    Morning: per-category slot allocation (MORNING_CATEGORY_SLOTS) so no single
    high-trust category can crowd out others. Remaining slots filled from any category.

    Midday: filter to MIDDAY_CATEGORIES, single ranked query.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    if brief_type == "midday":
        placeholders = ",".join("?" * len(MIDDAY_CATEGORIES))
        params: list = [cutoff] + sorted(MIDDAY_CATEGORIES) + [max_stories]
        query = f"""
            SELECT sg.id, sg.representative_title, sg.confidence,
                   sg.source_count, sg.categories, sg.has_divergence
            FROM story_groups sg
            WHERE sg.last_seen >= ?
            AND sg.top_category IN ({placeholders})
            ORDER BY
                CASE sg.confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                sg.source_count DESC,
                sg.last_seen DESC
            LIMIT ?
        """
        cur = conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        groups = [dict(zip(cols, row)) for row in cur.fetchall()]
    else:
        # Morning: fill per-category slots first, then top-off with any remaining
        seen_ids: set[int] = set()
        groups: list[dict] = []

        for category, slots in MORNING_CATEGORY_SLOTS.items():
            batch = _fetch_groups_query(conn, cutoff, category, slots)
            for g in batch:
                if g["id"] not in seen_ids:
                    seen_ids.add(g["id"])
                    groups.append(g)

        # Fill remaining slots from any category not already included
        remaining = max_stories - len(groups)
        if remaining > 0:
            overflow = _fetch_groups_query(conn, cutoff, None, max_stories)
            for g in overflow:
                if g["id"] not in seen_ids and remaining > 0:
                    seen_ids.add(g["id"])
                    groups.append(g)
                    remaining -= 1

    # Fetch articles for each group
    for group in groups:
        art_cur = conn.execute(
            """
            SELECT source_name, category, trust_level, title, url, published,
                   text, is_vendor, is_research, is_zeitgeist
            FROM parsed_articles
            WHERE story_group_id = ?
            ORDER BY
                CASE trust_level WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END
            """,
            (group["id"],),
        )
        art_cols = [d[0] for d in art_cur.description]
        group["articles"] = [dict(zip(art_cols, row)) for row in art_cur.fetchall()]

    # Suppress Form 4 stubs — no enrichment yet, bare entries are low value.
    # Remove Form 4 articles from every group; drop groups that become empty.
    filtered: list[dict] = []
    for group in groups:
        group["articles"] = [
            a for a in group["articles"] if a["source_name"] != FORM4_SOURCE
        ]
        if group["articles"]:
            filtered.append(group)
        else:
            log.debug("Dropped story group %d — only Form 4 articles", group["id"])
    groups = filtered

    # Pre-filter zero-signal groups to reduce token usage:
    # 1. Drop groups where every article is zeitgeist-only (pure Reddit noise)
    # 2. Drop groups where every article is vendor AND confidence is low
    #    (no independent confirmation — vendor press release with no pickup)
    signal_groups: list[dict] = []
    for group in groups:
        arts = group["articles"]
        all_zeitgeist = all(a["is_zeitgeist"] for a in arts)
        all_vendor_low = (
            all(a["is_vendor"] for a in arts) and group["confidence"] == "low"
        )
        if all_zeitgeist:
            log.debug("Dropped zero-signal group %d — all zeitgeist", group["id"])
        elif all_vendor_low:
            log.debug("Dropped zero-signal group %d — vendor-only, low confidence", group["id"])
        else:
            signal_groups.append(group)
    dropped = len(groups) - len(signal_groups)
    if dropped:
        log.info("Pre-filtered %d zero-signal groups (zeitgeist/vendor-only)", dropped)
    groups = signal_groups

    return groups


# --- Prompt Building ---

def format_article_block(art: dict, idx: int) -> str:
    """Format a single article as a structured text block for the prompt."""
    flags = []
    if art["is_vendor"]:
        flags.append("VENDOR")
    if art["is_research"]:
        flags.append("PRE-PUBLICATION")
    if art["is_zeitgeist"]:
        flags.append("ZEITGEIST-ONLY")
    flag_str = f" [{', '.join(flags)}]" if flags else ""

    published = art["published"] or "unknown"
    text = (art["text"] or "")[:PROMPT_TEXT_CHARS].strip()
    if len(art["text"] or "") > PROMPT_TEXT_CHARS:
        text += "…"

    lines = [
        f"ARTICLE {idx}",
        f"SOURCE: {art['source_name']}{flag_str}",
        f"CATEGORY: {art['category']} | TRUST: {art['trust_level']}",
        f"TITLE: {art['title'] or '(no title)'}",
        f"PUBLISHED: {published}",
        f"URL: {art['url']}",
    ]
    if text:
        lines.append(f"TEXT: {text}")
    lines.append("")
    return "\n".join(lines)


def build_user_message(groups: list[dict], brief_type: str) -> str:
    """Build the user message containing all story groups and their articles."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"DATE: {today}",
        f"BRIEF TYPE: {brief_type}",
        f"STORY GROUPS: {len(groups)}",
        "",
        "=" * 60,
        "",
    ]

    article_idx = 1
    for group in groups:
        lines.append(
            f"STORY GROUP {group['id']} | CONFIDENCE: {group['confidence']} "
            f"| SOURCES: {group['source_count']} | DIVERGENCE: {'YES' if group['has_divergence'] else 'no'}"
        )
        lines.append(f"CATEGORIES: {group['categories'] or 'unknown'}")
        lines.append("")

        for art in group["articles"]:
            lines.append(format_article_block(art, article_idx))
            article_idx += 1

        lines.append("-" * 40)
        lines.append("")

    return "\n".join(lines)


def load_system_prompt(brief_type: str) -> str:
    prompt_file = PROMPTS_DIR / f"{brief_type}_system.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"System prompt not found: {prompt_file}")
    return prompt_file.read_text()


# --- Output ---

def save_brief(brief_type: str, content: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out_path = OUTPUT_DIR / f"{timestamp}_{brief_type}.md"
    out_path.write_text(content)
    log.info("Brief saved: %s", out_path)
    return out_path


# --- Main ---

def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize news brief via Claude API")
    parser.add_argument("--brief-type", required=True, choices=["morning", "midday"])
    parser.add_argument("--dry-run", action="store_true", help="Build prompt, skip API call")
    parser.add_argument("--hours-back", type=int, default=None)
    parser.add_argument("--max-stories", type=int, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    defaults = BRIEF_DEFAULTS[args.brief_type]
    hours_back = args.hours_back or defaults["hours_back"]
    max_stories = args.max_stories or defaults["max_stories"]

    load_dotenv(ENV_PATH)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        log.error("ANTHROPIC_API_KEY not set in %s", ENV_PATH)
        sys.exit(1)

    if not DB_PATH.exists():
        log.error("DB not found at %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    init_brief_history(conn)

    log.info(
        "Building %s brief — last %dh, max %d story groups, model=%s",
        args.brief_type, hours_back, max_stories, args.model,
    )

    groups = fetch_story_groups(conn, args.brief_type, hours_back, max_stories)
    article_count = sum(len(g["articles"]) for g in groups)
    log.info("Fetched %d story groups, %d articles total", len(groups), article_count)

    if not groups:
        log.warning("No story groups in window — nothing to synthesize.")
        conn.close()
        return

    system_prompt = load_system_prompt(args.brief_type)
    user_message = build_user_message(groups, args.brief_type)

    if args.dry_run:
        print("=" * 60)
        print("SYSTEM PROMPT")
        print("=" * 60)
        print(system_prompt[:500], "...[truncated]")
        print()
        print("=" * 60)
        print("USER MESSAGE")
        print("=" * 60)
        print(user_message[:2000], "...[truncated]" if len(user_message) > 2000 else "")
        print(f"\n[Total user message: {len(user_message)} chars, ~{len(user_message)//4} tokens estimated]")
        conn.close()
        return

    client = anthropic.Anthropic(api_key=api_key)
    generated_at = datetime.now(timezone.utc).isoformat()

    log.info("Calling Claude API (%s)…", args.model)
    try:
        response = client.messages.create(
            model=args.model,
            max_tokens=4096,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        log.error("Claude API error: %s", exc)
        conn.execute(
            """
            INSERT INTO brief_history
                (brief_type, generated_at, model, story_count, article_count, success, error)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (args.brief_type, generated_at, args.model, len(groups), article_count, str(exc)),
        )
        conn.commit()
        conn.close()
        sys.exit(1)

    brief_content = response.content[0].text
    prompt_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cache_read_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    cache_creation_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0

    log.info(
        "API response: %d prompt tokens, %d output tokens | cache: %d read, %d creation",
        prompt_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    )

    out_path = save_brief(args.brief_type, brief_content)

    conn.execute(
        """
        INSERT INTO brief_history
            (brief_type, generated_at, model, story_count, article_count,
             output_path, prompt_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            args.brief_type, generated_at, args.model,
            len(groups), article_count,
            str(out_path), prompt_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens,
        ),
    )
    conn.commit()
    conn.close()

    log.info("Done — %s brief complete", args.brief_type)
    print(brief_content)


if __name__ == "__main__":
    main()
