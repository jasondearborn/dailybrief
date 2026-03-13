"""
edgar_fetcher.py — Fetch SEC EDGAR filings and macro data for portfolio/candidate tickers.

For each ticker in the portfolio + candidates DB tables, fetches:
  - 8-K  (material events, guidance, partnerships, leadership changes)
  - 10-Q / 10-K  (quarterly/annual financials)
  - Form 4  (insider transactions — enriched from XML; suppressed if enrichment fails)

Macro feeds (fetched once per run, not per ticker):
  - Federal Reserve press releases (RSS)
  - BLS (Bureau of Labor Statistics) press releases (RSS)

All results are written to raw_articles with category='portfolio_signals' (ticker-specific)
or category='macro' (Fed/BLS), trust_level='high'.

Usage:
    python fetchers/edgar_fetcher.py [--dry-run]

SEC EDGAR public API — no key required; User-Agent header mandatory per EDGAR policy.
Set EDGAR_USER_AGENT in config/.env (e.g. your email address).
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
LOG_PATH = BASE_DIR / "logs" / "edgar_fetcher.log"
TICKER_CACHE_PATH = BASE_DIR / "data" / "edgar_tickers.json"
ENV_PATH = BASE_DIR / "config" / ".env"

load_dotenv(ENV_PATH)

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
_edgar_ua_env = os.environ.get("EDGAR_USER_AGENT", "")
USER_AGENT = _edgar_ua_env if _edgar_ua_env else "dailybrief-aggregator/1.0 contact@localhost"

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}"
EDGAR_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}-index.json"

# Only include filings filed within this many days
FILING_MAX_AGE_DAYS = 7

# Forms to track (Form 4 handled separately for enrichment)
TRACKED_FORMS = {"8-K", "10-Q", "10-K"}

# Friendly names for filing forms
FORM_NAMES = {
    "8-K": "Material Event (8-K)",
    "10-Q": "Quarterly Report (10-Q)",
    "10-K": "Annual Report (10-K)",
    "4": "Insider Transaction (Form 4)",
}

# Friendly labels for Form 4 transaction codes
TRANSACTION_CODE_LABELS = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant/award",
    "D": "return to company",
    "F": "tax withholding (disposition)",
    "M": "option exercise",
    "G": "gift",
    "C": "option conversion",
    "E": "expiration of short derivative",
    "H": "expiration of long derivative",
    "I": "discretionary transaction",
    "J": "other acquisition/disposition",
    "K": "equity swap or similar",
    "L": "small acquisition",
    "O": "exercise out-of-money option",
    "U": "tender of shares in exchange",
    "W": "will/trust",
    "X": "in-the-money option/warrant exercise",
    "Z": "voting trust deposit/withdrawal",
}

# 8-K item signal classification
HIGH_SIGNAL_8K_ITEMS = {"1.01", "1.02", "1.03", "2.01", "2.05", "2.06", "5.02", "7.01", "8.01"}
# Low-signal items: skip filing if ONLY these are present
LOW_SIGNAL_8K_ITEMS = {"2.02", "9.01"}

# 8-K item descriptions
ITEM_DESCRIPTIONS = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Acquisition or Disposal of Assets",
    "2.02": "Results of Operations",
    "2.05": "Cost Associated with Exit/Disposal",
    "2.06": "Material Impairment",
    "5.02": "Executive Departure/Appointment",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Material Event",
    "9.01": "Financial Statements and Exhibits",
}

# Macro RSS feeds — fetched once per run, not per ticker
MACRO_FEEDS = [
    {
        "name": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "category": "macro",
        "trust_level": "high",
    },
    {
        "name": "BLS (Bureau of Labor Statistics)",
        "url": "https://www.bls.gov/feed/bls_latest.rss",
        "category": "macro",
        "trust_level": "high",
    },
]

# Polite delay between EDGAR API requests (seconds) — EDGAR limit is 10 req/s
REQUEST_DELAY = 0.15


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def edgar_get(url: str, timeout: int = 15) -> requests.Response | None:
    """GET with EDGAR-required User-Agent header. Returns None on error."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        log.warning("EDGAR request failed (%s): %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Ticker → CIK resolution
# ---------------------------------------------------------------------------

def load_ticker_to_cik(force_refresh: bool = False) -> dict[str, int]:
    """
    Return a dict mapping uppercase ticker symbol → integer CIK.
    Uses a local cache (data/edgar_tickers.json) refreshed once per day.
    """
    cache_stale = True
    if TICKER_CACHE_PATH.exists() and not force_refresh:
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            TICKER_CACHE_PATH.stat().st_mtime, tz=timezone.utc
        )
        cache_stale = age > timedelta(hours=23)

    if not cache_stale:
        try:
            data = json.loads(TICKER_CACHE_PATH.read_text())
            return {k.upper(): int(v) for k, v in data.items()}
        except Exception as exc:
            log.warning("Failed to load ticker cache: %s — refreshing", exc)

    log.info("Fetching EDGAR company tickers list...")
    resp = edgar_get(EDGAR_TICKERS_URL)
    if resp is None:
        log.error("Could not fetch EDGAR tickers list — CIK resolution unavailable")
        return {}

    # Response is a dict of index → {cik_str, ticker, title}
    raw = resp.json()
    mapping: dict[str, int] = {}
    for entry in raw.values():
        ticker = entry.get("ticker", "").upper()
        cik = entry.get("cik_str")
        if ticker and cik:
            mapping[ticker] = int(cik)

    TICKER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TICKER_CACHE_PATH.write_text(json.dumps(mapping))
    log.info("Loaded %d tickers from EDGAR", len(mapping))
    return mapping


# ---------------------------------------------------------------------------
# Form 4 XML enrichment
# ---------------------------------------------------------------------------

def _xml_text(root: ET.Element, path: str) -> str:
    """Return stripped text at XPath path, or empty string."""
    el = root.find(path)
    if el is not None and el.text:
        return el.text.strip()
    return ""


def _xml_footnotes(root: ET.Element) -> str:
    """Return concatenated text of all footnote elements."""
    parts = []
    for fn in root.findall(".//footnote"):
        if fn.text:
            parts.append(fn.text.strip())
    return " ".join(parts).lower()


def enrich_form4(cik: int, accession_no: str, primary_doc: str) -> dict | None:
    """
    Fetch and parse a Form 4 XML filing.
    Returns a dict with enrichment fields, or None if enrichment fails or
    the transaction should be suppressed (grants, 10b5-1 sales).

    Surfaced: open-market purchases (code P)
    Suppressed: grants/awards (code A), 10b5-1 sales, failed enrichment
    """
    accession_no_dashes = accession_no.replace("-", "")
    # The primary doc may be an htm/html wrapper; try to find the XML
    xml_filename = primary_doc
    if not xml_filename.lower().endswith(".xml"):
        xml_filename = accession_no_dashes + ".xml"

    url = EDGAR_ARCHIVES_URL.format(
        cik=cik, accession_no_dashes=accession_no_dashes, filename=xml_filename
    )
    time.sleep(REQUEST_DELAY)
    resp = edgar_get(url)

    if resp is None and xml_filename != primary_doc:
        # Fallback: try the primary doc itself
        url = EDGAR_ARCHIVES_URL.format(
            cik=cik, accession_no_dashes=accession_no_dashes, filename=primary_doc
        )
        time.sleep(REQUEST_DELAY)
        resp = edgar_get(url)

    if resp is None:
        return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        log.debug("Form 4 XML parse error (%s): %s", accession_no, exc)
        return None

    # Namespace-agnostic: strip namespace prefixes if present
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]

    # Reporting owner
    owner_name = _xml_text(root, ".//rptOwnerName")
    officer_title = _xml_text(root, ".//officerTitle")
    is_director = _xml_text(root, ".//isDirector") == "1"
    is_officer = _xml_text(root, ".//isOfficer") == "1"

    if officer_title:
        role = officer_title
    elif is_director:
        role = "Director"
    elif is_officer:
        role = "Officer"
    else:
        role = "Reporting Owner"

    # Find the first non-derivative transaction
    tx_code = ""
    shares_str = ""
    price_str = ""
    acq_disp = ""
    shares_owned_after_str = ""

    for tx in root.findall(".//nonDerivativeTransaction"):
        tx_code = _xml_text(tx, ".//transactionCode")
        shares_str = _xml_text(tx, ".//transactionShares/value")
        price_str = _xml_text(tx, ".//transactionPricePerShare/value")
        acq_disp = _xml_text(tx, ".//transactionAcquiredDisposedCode/value")
        shares_owned_after_str = _xml_text(tx, ".//sharesOwnedFollowingTransaction/value")
        if tx_code:
            break

    # If no non-derivative, try derivative transactions
    if not tx_code:
        for tx in root.findall(".//derivativeTransaction"):
            tx_code = _xml_text(tx, ".//transactionCode")
            shares_str = _xml_text(tx, ".//transactionShares/value")
            price_str = _xml_text(tx, ".//transactionPricePerShare/value")
            acq_disp = _xml_text(tx, ".//transactionAcquiredDisposedCode/value")
            if tx_code:
                break

    # Suppress grants/awards (code A) — these are compensation, not signal
    if tx_code == "A":
        log.debug("Form 4 suppressed — grant/award transaction (%s)", accession_no)
        return None

    # Suppress sales under a 10b5-1 plan — check footnotes for "10b5-1"
    footnotes = _xml_footnotes(root)
    if tx_code == "S" and "10b5-1" in footnotes:
        log.debug("Form 4 suppressed — 10b5-1 sale (%s)", accession_no)
        return None

    # Require all three key fields; suppress if missing
    if not tx_code or not shares_str or not price_str:
        log.debug("Form 4 enrichment incomplete (%s) — suppressed", accession_no)
        return None

    try:
        shares = float(shares_str)
        price = float(price_str)
    except ValueError:
        log.debug("Form 4 non-numeric shares/price (%s) — suppressed", accession_no)
        return None

    shares_owned_after = None
    if shares_owned_after_str:
        try:
            shares_owned_after = float(shares_owned_after_str)
        except ValueError:
            pass

    tx_label = TRANSACTION_CODE_LABELS.get(tx_code, f"transaction ({tx_code})")
    direction = "acquired" if acq_disp == "A" else "disposed"

    return {
        "owner_name": owner_name,
        "role": role,
        "transaction_code": tx_code,
        "transaction_label": tx_label,
        "shares": shares,
        "price_per_share": price,
        "direction": direction,
        "shares_owned_after": shares_owned_after,
    }


# ---------------------------------------------------------------------------
# 8-K enrichment
# ---------------------------------------------------------------------------

def _extract_plain_text(html_content: str) -> str:
    """Strip HTML tags and return plain text."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        # Fallback: strip tags with regex
        return re.sub(r"<[^>]+>", " ", html_content)


def enrich_8k(
    cik: int, accession_no: str, primary_doc: str
) -> dict | None:
    """
    Fetch and parse an 8-K filing document.
    Returns enrichment dict or None if should be skipped/failed.

    Enrichment dict keys:
        items: list of (item_number, item_text_snippet) tuples for high-signal items
        title_suffix: e.g. "Item 5.02: Executive Departure/Appointment"
        summary: extracted disclosure text
        skip: True if only low-signal items present
        failed: True if fetch/parse failed (caller should write stub with low trust)
    """
    accession_no_dashes = accession_no.replace("-", "")
    url = EDGAR_ARCHIVES_URL.format(
        cik=cik, accession_no_dashes=accession_no_dashes, filename=primary_doc
    )
    time.sleep(REQUEST_DELAY)
    resp = edgar_get(url)

    if resp is None:
        log.debug("8-K fetch failed (%s)", accession_no)
        return {"failed": True}

    try:
        text = _extract_plain_text(resp.text)
    except Exception as exc:
        log.debug("8-K parse error (%s): %s", accession_no, exc)
        return {"failed": True}

    # Find all "Item X.XX" occurrences and their positions
    item_pattern = re.compile(r"Item\s+(\d+\.\d+)", re.IGNORECASE)
    matches = list(item_pattern.finditer(text))

    if not matches:
        # No items found — could be an amendment or unusual format
        log.debug("8-K no items found (%s)", accession_no)
        return {"failed": True}

    found_items: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        item_num = m.group(1)
        # Extract text snippet after item header until next item or 500 chars
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else start + 600
        snippet = text[start:min(start + 500, end)].strip()
        # Clean up whitespace
        snippet = re.sub(r"\s+", " ", snippet)[:500]
        found_items.append((item_num, snippet))

    # Deduplicate — keep first occurrence of each item number
    seen: set[str] = set()
    unique_items: list[tuple[str, str]] = []
    for item_num, snippet in found_items:
        if item_num not in seen:
            seen.add(item_num)
            unique_items.append((item_num, snippet))

    # Check signal level
    high_signal = [i for i in unique_items if i[0] in HIGH_SIGNAL_8K_ITEMS]
    all_low = all(i[0] in LOW_SIGNAL_8K_ITEMS for i in unique_items)

    if not high_signal and all_low:
        log.debug("8-K only low-signal items (%s) — skipping", accession_no)
        return {"skip": True}

    if not high_signal:
        # Has items but none are in our high-signal list — write as stub
        log.debug("8-K no high-signal items (%s) — using stub", accession_no)
        return {"failed": True}

    # Build title suffix from first high-signal item
    first_item_num, first_snippet = high_signal[0]
    item_desc = ITEM_DESCRIPTIONS.get(first_item_num, f"Item {first_item_num}")

    # If multiple high-signal items, note them in title
    if len(high_signal) > 1:
        extra = ", ".join(f"Item {n}" for n, _ in high_signal[1:])
        title_suffix = f"Item {first_item_num}: {item_desc} (+ {extra})"
    else:
        title_suffix = f"Item {first_item_num}: {item_desc}"

    # Build summary from high-signal item snippets
    summary_parts = []
    for item_num, snippet in high_signal:
        desc = ITEM_DESCRIPTIONS.get(item_num, f"Item {item_num}")
        if snippet:
            summary_parts.append(f"[Item {item_num} — {desc}] {snippet}")
    summary = " | ".join(summary_parts)[:1000]

    return {
        "items": high_signal,
        "title_suffix": title_suffix,
        "summary": summary,
        "skip": False,
        "failed": False,
    }


# ---------------------------------------------------------------------------
# 10-Q / 10-K enrichment
# ---------------------------------------------------------------------------

def enrich_10q_10k(
    cik: int, accession_no: str, primary_doc: str, form_type: str, desc: str
) -> dict | None:
    """
    Fetch and parse a 10-Q or 10-K filing.
    Returns enrichment dict with MD&A extract, or None on failure.

    Enrichment dict keys:
        fiscal_period: string extracted from document or description
        mda_text: first 600 chars of MD&A section
        failed: True if fetch/parse failed
    """
    accession_no_dashes = accession_no.replace("-", "")
    url = EDGAR_ARCHIVES_URL.format(
        cik=cik, accession_no_dashes=accession_no_dashes, filename=primary_doc
    )
    time.sleep(REQUEST_DELAY)
    resp = edgar_get(url)

    if resp is None:
        log.debug("10-Q/10-K fetch failed (%s)", accession_no)
        return {"failed": True}

    try:
        text = _extract_plain_text(resp.text)
    except Exception as exc:
        log.debug("10-Q/10-K parse error (%s): %s", accession_no, exc)
        return {"failed": True}

    # Extract fiscal period from desc or document text
    fiscal_period = ""
    if desc:
        # descriptions like "10-Q" or "FORM 10-Q" — look for period info
        period_match = re.search(
            r"(quarter|year|period)\s+(ended?|ending)\s+([A-Za-z]+\s+\d+,?\s*\d{4}|\d{4}-\d{2}-\d{2})",
            text[:3000], re.IGNORECASE
        )
        if period_match:
            fiscal_period = period_match.group(0).strip()

    # Find MD&A section
    mda_text = ""
    mda_pattern = re.compile(
        r"management.{0,10}s\s+discussion\s+and\s+analysis",
        re.IGNORECASE
    )
    mda_match = mda_pattern.search(text)
    if mda_match:
        start = mda_match.end()
        raw = text[start:start + 800].strip()
        raw = re.sub(r"\s+", " ", raw)
        mda_text = raw[:600]

    return {
        "fiscal_period": fiscal_period,
        "mda_text": mda_text,
        "failed": False,
    }


# ---------------------------------------------------------------------------
# EDGAR filing fetch
# ---------------------------------------------------------------------------

def fetch_ticker_filings(
    ticker: str,
    cik: int,
    conn: sqlite3.Connection | None,
    dry_run: bool,
) -> int:
    """
    Fetch recent EDGAR filings for a single ticker/CIK.
    Returns count of new articles written.
    """
    time.sleep(REQUEST_DELAY)
    url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
    resp = edgar_get(url)
    if resp is None:
        log.warning("Could not fetch EDGAR submissions for %s (CIK %d)", ticker, cik)
        return 0

    try:
        data = resp.json()
    except Exception as exc:
        log.warning("JSON parse error for %s submissions: %s", ticker, exc)
        return 0

    company_name = data.get("name", ticker)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    cutoff = datetime.now(timezone.utc) - timedelta(days=FILING_MAX_AGE_DAYS)
    fetched_at = datetime.now(timezone.utc).isoformat()
    new_count = 0

    for form, date_str, accession, primary_doc, desc in zip(
        forms, dates, accessions, primary_docs, descriptions
    ):
        if form not in TRACKED_FORMS and form != "4":
            continue

        try:
            filed_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if filed_dt < cutoff:
            continue

        accession_dashes = accession  # already in XX-XXXXX-XXXXX format
        accession_nodashes = accession.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodashes}/{primary_doc}"
        )

        source_name = f"SEC EDGAR: {ticker}"
        source_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type={form}&dateb=&owner=include&count=1"
        )

        # --- Form 4: insider transaction enrichment ---
        if form == "4":
            enrichment = enrich_form4(cik, accession_dashes, primary_doc)
            if enrichment is None:
                log.debug("%s Form 4 (%s) suppressed", ticker, date_str)
                continue

            e = enrichment
            shares_after_str = ""
            if e.get("shares_owned_after") is not None:
                shares_after_str = f" Resulting position: {e['shares_owned_after']:,.0f} shares."

            summary = (
                f"{e['owner_name']} ({e['role']}) {e['transaction_label']} "
                f"{e['shares']:,.0f} shares at ${e['price_per_share']:.2f}/share "
                f"(total ${e['shares'] * e['price_per_share']:,.0f})."
                f"{shares_after_str}"
            )
            title = (
                f"{ticker} — {e['owner_name']} ({e['role']}): "
                f"{e['transaction_label']} {e['shares']:,.0f} shares @ ${e['price_per_share']:.2f}"
            )
            content = (
                f"Ticker: {ticker} | Company: {company_name}\n"
                f"Insider: {e['owner_name']} ({e['role']})\n"
                f"Transaction: {e['transaction_label']} ({e['transaction_code']})\n"
                f"Direction: {e['direction']}\n"
                f"Shares: {e['shares']:,.0f}\n"
                f"Price per share: ${e['price_per_share']:.2f}\n"
                f"Total value: ${e['shares'] * e['price_per_share']:,.0f}\n"
                f"Filed: {date_str}"
            )
            trust_level = "high"

        # --- 8-K: material event enrichment ---
        elif form == "8-K":
            enrichment = enrich_8k(cik, accession_dashes, primary_doc)

            if enrichment is None or enrichment.get("skip"):
                log.debug("%s 8-K (%s) skipped — low-signal items only", ticker, date_str)
                continue

            if enrichment.get("failed"):
                # Fall back to stub with low trust
                title = f"{ticker} 8-K — {desc or 'Material Event'} ({date_str}) (enrichment failed)"
                summary = f"{company_name} filed an 8-K with the SEC on {date_str}."
                content = None
                trust_level = "low"
            else:
                title = f"{ticker} 8-K — {enrichment['title_suffix']}"
                summary = enrichment["summary"]
                content = None
                trust_level = "high"

        # --- 10-Q / 10-K: financial report enrichment ---
        elif form in ("10-Q", "10-K"):
            enrichment = enrich_10q_10k(cik, accession_dashes, primary_doc, form, desc)

            if enrichment is None or enrichment.get("failed"):
                # Fall back to stub with low trust
                title = f"{ticker} {form} — {desc or form} ({date_str}) (enrichment failed)"
                summary = f"{company_name} filed a {form} with the SEC on {date_str}."
                content = None
                trust_level = "low"
            else:
                e = enrichment
                period_str = f" — {e['fiscal_period']}" if e["fiscal_period"] else f" ({date_str})"
                title = f"{ticker} {form}{period_str}"
                if e["mda_text"]:
                    summary = f"[MD&A] {e['mda_text']}"
                else:
                    summary = f"{company_name} filed a {form} with the SEC on {date_str}."
                content = None
                trust_level = "medium"

        else:
            continue

        if dry_run:
            print(f"  [edgar/{form}] {title}")
            print(f"    URL: {filing_url}")
            print(f"    Summary: {(summary or '')[:120]}")
            new_count += 1
            continue

        uhash = url_hash(filing_url)
        try:
            conn.execute(
                """
                INSERT INTO raw_articles
                    (url_hash, source_name, source_url, category, trust_level,
                     title, url, published, summary, content, fetched_at)
                VALUES (?, ?, ?, 'portfolio_signals', ?, ?, ?, ?, ?, ?, ?)
                """,
                (uhash, source_name, source_url, trust_level, title, filing_url,
                 filed_dt.isoformat(), summary, content, fetched_at),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            pass  # Already have this filing
        except sqlite3.Error as exc:
            log.error("DB error inserting %s filing %s: %s", ticker, accession, exc)

    if not dry_run and new_count > 0:
        conn.commit()

    log.info("  %s (CIK %d): %d new filings", ticker, cik, new_count)
    return new_count


# ---------------------------------------------------------------------------
# Macro RSS feeds (Fed + BLS)
# ---------------------------------------------------------------------------

def fetch_macro_feed(
    feed: dict,
    conn: sqlite3.Connection | None,
    dry_run: bool,
) -> int:
    """Fetch a macro RSS feed and write to raw_articles. Returns count of new articles."""
    name = feed["name"]
    url = feed["url"]
    category = feed["category"]
    trust_level = feed["trust_level"]
    fetched_at = datetime.now(timezone.utc).isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(days=FILING_MAX_AGE_DAYS)

    log.info("Fetching macro feed: %s", name)
    try:
        parsed = feedparser.parse(
            url,
            request_headers={"User-Agent": USER_AGENT},
        )
    except Exception as exc:
        log.warning("Failed to fetch macro feed %s: %s", name, exc)
        return 0

    new_count = 0
    for entry in parsed.entries:
        link = entry.get("link", "").strip()
        if not link:
            continue

        title = entry.get("title", "").strip()
        summary = entry.get("summary", "").strip() or None

        # Parse published date
        published = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                published = pub_dt.isoformat()
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            try:
                pub_dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                published = pub_dt.isoformat()
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass

        if dry_run:
            print(f"  [macro/{name}] {title[:80]}")
            new_count += 1
            continue

        uhash = url_hash(link)
        try:
            conn.execute(
                """
                INSERT INTO raw_articles
                    (url_hash, source_name, source_url, category, trust_level,
                     title, url, published, summary, content, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (uhash, name, url, category, trust_level,
                 title, link, published, summary, fetched_at),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            pass
        except sqlite3.Error as exc:
            log.error("DB error inserting macro article %s: %s", link, exc)

    if not dry_run and new_count > 0:
        conn.commit()

    log.info("  %s: %d new articles", name, new_count)
    return new_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_tickers_from_db(conn: sqlite3.Connection) -> list[str]:
    """Return deduplicated list of tickers from portfolio and candidates tables."""
    tickers: set[str] = set()

    try:
        rows = conn.execute("SELECT ticker FROM portfolio").fetchall()
        tickers.update(r[0].upper() for r in rows if r[0])
    except sqlite3.OperationalError:
        pass  # Table may not exist if portfolio.md was never parsed

    try:
        rows = conn.execute(
            "SELECT ticker FROM candidates WHERE status = 'active'"
        ).fetchall()
        tickers.update(r[0].upper() for r in rows if r[0])
    except sqlite3.OperationalError:
        pass

    return sorted(tickers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SEC EDGAR filings and macro data")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing to DB")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("DB not found at %s — run rss_fetcher.py first", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    tickers = get_tickers_from_db(conn)
    if not tickers:
        log.info("No tickers found in portfolio/candidates tables — skipping EDGAR fetch")
    else:
        log.info("EDGAR fetch starting — %d tickers: %s", len(tickers), ", ".join(tickers))

        ticker_to_cik = load_ticker_to_cik()
        total_new = 0
        unresolved = []

        for ticker in tickers:
            cik = ticker_to_cik.get(ticker)
            if cik is None:
                log.warning("CIK not found for ticker %s — skipping", ticker)
                unresolved.append(ticker)
                continue
            new = fetch_ticker_filings(ticker, cik, conn if not args.dry_run else None, args.dry_run)
            total_new += new

        if unresolved:
            log.warning("Unresolved tickers (not in EDGAR): %s", ", ".join(unresolved))
        log.info("EDGAR filings complete — %d new articles", total_new)

    # Macro feeds — always run regardless of portfolio
    macro_new = 0
    for feed in MACRO_FEEDS:
        time.sleep(REQUEST_DELAY)
        macro_new += fetch_macro_feed(feed, conn if not args.dry_run else None, args.dry_run)
    log.info("Macro feeds complete — %d new articles", macro_new)

    conn.close()


if __name__ == "__main__":
    main()
