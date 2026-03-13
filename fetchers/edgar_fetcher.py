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
"""

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
LOG_PATH = BASE_DIR / "logs" / "edgar_fetcher.log"
TICKER_CACHE_PATH = BASE_DIR / "data" / "edgar_tickers.json"

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
USER_AGENT = "dailybrief-aggregator/1.0 contact@localhost"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{filename}"

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

# Polite delay between EDGAR API requests (seconds)
REQUEST_DELAY = 0.5


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


def enrich_form4(cik: int, accession_no: str, primary_doc: str) -> dict | None:
    """
    Fetch and parse a Form 4 XML filing.
    Returns a dict with enrichment fields, or None if enrichment fails.

    Required fields for inclusion: transaction_code, shares, price_per_share.
    If any are missing/unparseable, returns None (filing is suppressed).
    """
    accession_no_dashes = accession_no.replace("-", "")
    # The primary doc may be an htm/html wrapper; try to find the XML
    # EDGAR naming convention: the XML is usually named like *4*.xml or the primary doc itself
    xml_filename = primary_doc
    if not xml_filename.lower().endswith(".xml"):
        # Try the standard EDGAR naming pattern
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

    # Find the first non-derivative transaction (most common for insider trades)
    tx_code = ""
    shares_str = ""
    price_str = ""
    acq_disp = ""

    for tx in root.findall(".//nonDerivativeTransaction"):
        tx_code = _xml_text(tx, ".//transactionCode")
        shares_str = _xml_text(tx, ".//transactionShares/value")
        price_str = _xml_text(tx, ".//transactionPricePerShare/value")
        acq_disp = _xml_text(tx, ".//transactionAcquiredDisposedCode/value")
        if tx_code:
            break  # Use first transaction found

    # If no non-derivative, try derivative transactions
    if not tx_code:
        for tx in root.findall(".//derivativeTransaction"):
            tx_code = _xml_text(tx, ".//transactionCode")
            shares_str = _xml_text(tx, ".//transactionShares/value")
            price_str = _xml_text(tx, ".//transactionPricePerShare/value")
            acq_disp = _xml_text(tx, ".//transactionAcquiredDisposedCode/value")
            if tx_code:
                break

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

        form_label = FORM_NAMES.get(form, form)
        title = f"{ticker} — {form_label} ({date_str})"
        if desc:
            title = f"{ticker} — {form_label}: {desc} ({date_str})"

        if form == "4":
            # Enrich Form 4 before including
            enrichment = enrich_form4(cik, accession_dashes, primary_doc)
            if enrichment is None:
                log.debug("%s Form 4 (%s) suppressed — enrichment failed", ticker, date_str)
                continue

            e = enrichment
            summary = (
                f"{e['owner_name']} ({e['role']}) made an {e['transaction_label']} "
                f"({e['direction']}) of {e['shares']:,.0f} shares at "
                f"${e['price_per_share']:.2f}/share."
            )
            title = (
                f"{ticker} Form 4: {e['owner_name']} {e['transaction_label']} "
                f"{e['shares']:,.0f} sh @ ${e['price_per_share']:.2f} ({date_str})"
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
        else:
            summary = f"{company_name} filed a {form_label} with the SEC on {date_str}."
            content = None

        source_name = f"SEC EDGAR: {ticker}"
        source_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=10"

        if dry_run:
            print(f"  [edgar] {title}")
            print(f"    {filing_url}")
            if form == "4":
                print(f"    {summary}")
            new_count += 1
            continue

        uhash = url_hash(filing_url)
        try:
            conn.execute(
                """
                INSERT INTO raw_articles
                    (url_hash, source_name, source_url, category, trust_level,
                     title, url, published, summary, content, fetched_at)
                VALUES (?, ?, ?, 'portfolio_signals', 'high', ?, ?, ?, ?, ?, ?)
                """,
                (uhash, source_name, source_url, title, filing_url,
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
