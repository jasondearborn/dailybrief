"""
email.py — Format brief as HTML email and deliver via SMTP.

Usage:
    python delivery/email.py --brief-type morning [--dry-run]
    python delivery/email.py --brief-path /opt/newsfeed/output/briefs/2026-03-10_0700_morning.md [--dry-run]

Options:
    --brief-type    morning | midday — sends most recent brief of that type
    --brief-path    Send a specific brief file (overrides --brief-type lookup)
    --dry-run       Print the rendered HTML to stdout, do not send
"""

import argparse
import logging
import os
import smtplib
import sqlite3
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown
from dotenv import load_dotenv

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "newsfeed.db"
OUTPUT_DIR = BASE_DIR / "output" / "briefs"
LOG_PATH = BASE_DIR / "logs" / "email.log"
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

# --- HTML email template ---
# Inline CSS only — no external resources, works in Gmail/Outlook/Apple Mail

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;700&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<style>
  body {{
    font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 15px;
    line-height: 1.6;
    color: #1a1a1a;
    background: #f4f4f4;
    margin: 0;
    padding: 0;
  }}
  .wrapper {{
    max-width: 720px;
    margin: 24px auto;
    background: #ffffff;
    border-radius: 6px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  }}
  .content {{
    padding: 24px 28px;
  }}
  h1 {{ display: none; }}
  h2 {{
    font-size: 13px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #64748b;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 6px;
    margin: 32px 0 16px;
  }}
  h3 {{
    font-size: 16px;
    font-weight: 600;
    color: #0f172a;
    margin: 20px 0 6px;
  }}
  p {{ margin: 6px 0 10px; }}
  blockquote {{
    border-left: 3px solid #cbd5e1;
    margin: 8px 0 10px 0;
    padding: 6px 14px;
    color: #374151;
    background: #f8fafc;
    border-radius: 0 4px 4px 0;
  }}
  blockquote p {{ margin: 0; }}
  strong {{ color: #0f172a; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  hr {{
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 20px 0;
  }}
  ul, ol {{ padding-left: 20px; margin: 8px 0; }}
  li {{ margin-bottom: 4px; }}
  .tier1 h2 {{ color: #b91c1c; border-color: #fca5a5; }}
  .tier2 h2 {{ color: #b45309; border-color: #fcd34d; }}
  .tier3 h2 {{ color: #374151; }}
  .flags h2 {{ color: #4b5563; border-color: #d1d5db; }}
  .footer {{
    background: #f8fafc;
    border-top: 1px solid #e2e8f0;
    padding: 12px 28px;
    font-size: 11px;
    color: #94a3b8;
    font-family: 'IBM Plex Mono', monospace;
  }}
</style>
</head>
<body>
<div class="wrapper">
  {header_html}
  <div class="content">
    {body_html}
  </div>
  <div class="footer">dailybrief &nbsp;·&nbsp; {generated_at} UTC &nbsp;·&nbsp; {story_count} stories &nbsp;·&nbsp; {model}</div>
</div>
</body>
</html>
"""


def extract_theme(brief_content: str) -> tuple[str, str]:
    """
    Extract and remove the THEME: line from the brief.
    Returns (theme_text, brief_without_theme_line).
    """
    import re
    theme = ""
    lines = brief_content.splitlines()
    filtered = []
    for line in lines:
        m = re.match(r'^THEME:\s*(.+)', line.strip())
        if m:
            theme = m.group(1).strip()
        else:
            filtered.append(line)
    return theme, "\n".join(filtered)


def count_tier_items(brief_content: str, brief_type: str) -> dict[str, int]:
    """
    Count ### headings within each tier section.
    Returns dict with keys: act_now, monitor, background, flags.
    """
    import re

    if brief_type == "morning":
        section_map = {
            "act_now":    r"##\s+Tier\s+1",
            "monitor":    r"##\s+Tier\s+2",
            "background": r"##\s+Tier\s+3",
            "flags":      r"##\s+Flags",
        }
    else:  # midday
        section_map = {
            "act_now":    r"##\s+Immediate\s+Signals",
            "monitor":    r"##\s+Watch\s+List",
            "background": r"##\s+Background",
            "flags":      r"##\s+Flags",
        }

    # Split content into sections by ## headings
    # Build a list of (heading_line_idx, heading_text) pairs
    lines = brief_content.splitlines()
    section_starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if re.match(r'^##\s+\S', line):
            section_starts.append((i, line))
    section_starts.append((len(lines), ""))  # sentinel

    # Map section key → list of lines in that section
    section_lines: dict[str, list[str]] = {}
    for j, (start_idx, heading) in enumerate(section_starts[:-1]):
        end_idx = section_starts[j + 1][0]
        content_lines = lines[start_idx:end_idx]
        for key, pattern in section_map.items():
            if re.search(pattern, heading, re.IGNORECASE):
                section_lines[key] = content_lines
                break

    # Count ### items in each mapped section
    counts: dict[str, int] = {}
    for key in ["act_now", "monitor", "background", "flags"]:
        sec = section_lines.get(key, [])
        counts[key] = sum(1 for line in sec if re.match(r'^###\s+\S', line))

    return counts


def build_graphical_header(
    brief_type: str,
    brief_date: str,
    tier_counts: dict[str, int],
    theme: str,
) -> str:
    """Render the graphical email header HTML (fully inline CSS, Gmail-safe)."""
    label = "Morning Brief" if brief_type == "morning" else "Midday Brief"

    tiles = [
        ("ACT NOW",    tier_counts.get("act_now", 0),    "#dc2626"),  # red
        ("MONITOR",    tier_counts.get("monitor", 0),    "#d97706"),  # amber
        ("BACKGROUND", tier_counts.get("background", 0), "#475569"),  # slate
        ("FLAGS",      tier_counts.get("flags", 0),      "#7c3aed"),  # purple
    ]

    tiles_html = ""
    for tile_label, count, color in tiles:
        tiles_html += f"""
        <td style="width:25%; padding:0 6px; vertical-align:top;">
          <div style="background:#1e293b; border-top:3px solid {color}; border-radius:4px; padding:10px 12px; text-align:center;">
            <div style="font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:700; color:#f8fafc; line-height:1;">{count}</div>
            <div style="font-family:'IBM Plex Mono',monospace; font-size:9px; font-weight:500; color:#94a3b8; text-transform:uppercase; letter-spacing:0.1em; margin-top:4px;">{tile_label}</div>
          </div>
        </td>"""

    theme_html = ""
    if theme:
        theme_html = f"""
  <div style="background:#0f172a; border-top:1px solid #1e3a5f; padding:10px 24px; display:flex; align-items:center; gap:10px;">
    <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#3b82f6; box-shadow:0 0 6px #3b82f6; flex-shrink:0;"></span>
    <span style="font-family:'IBM Plex Sans',sans-serif; font-size:13px; color:#93c5fd; font-style:italic;">{theme}</span>
  </div>"""

    return f"""
  <div style="background:#0f172a; padding:20px 24px 14px;">
    <div style="font-family:'IBM Plex Sans',sans-serif; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.12em; color:#475569; margin-bottom:4px;">Daily Brief</div>
    <div style="font-family:'IBM Plex Mono',monospace; font-size:18px; font-weight:700; color:#f8fafc; letter-spacing:0.02em;">{label} — {brief_date}</div>
    <table style="width:100%; border-collapse:collapse; margin-top:14px;" cellpadding="0" cellspacing="0">
      <tr>{tiles_html}
      </tr>
    </table>
  </div>{theme_html}"""


def md_to_html(md_text: str) -> str:
    """Convert markdown to HTML. Wrap tier sections in styled divs."""
    md_ext = ["extra", "smarty"]
    html_body = markdown.markdown(md_text, extensions=md_ext)

    # Wrap tier sections in divs for color-coded styling
    # The brief uses ## Tier 1, ## Tier 2, ## Tier 3, ## Flags as section markers
    import re

    def wrap_sections(html: str) -> str:
        # Split on h2 tags and wrap each section
        parts = re.split(r'(<h2>[^<]*</h2>)', html)
        out = []
        current_div = None
        for part in parts:
            m = re.match(r'<h2>(.*?)</h2>', part, re.IGNORECASE)
            if m:
                if current_div:
                    out.append("</div>")
                heading_text = m.group(1).lower()
                if "tier 1" in heading_text or "immediate" in heading_text:
                    current_div = "tier1"
                elif "tier 2" in heading_text or "watch" in heading_text:
                    current_div = "tier2"
                elif "tier 3" in heading_text or "background" in heading_text:
                    current_div = "tier3"
                elif "flag" in heading_text:
                    current_div = "flags"
                else:
                    current_div = "section"
                out.append(f'<div class="{current_div}">')
                out.append(part)
            else:
                out.append(part)
        if current_div:
            out.append("</div>")
        return "".join(out)

    return wrap_sections(html_body)


def find_latest_brief(brief_type: str) -> Path | None:
    """Return the most recently modified brief file of the given type."""
    briefs = sorted(OUTPUT_DIR.glob(f"*_{brief_type}.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return briefs[0] if briefs else None


def get_brief_metadata(conn: sqlite3.Connection, brief_type: str) -> dict:
    """Fetch metadata for the most recent brief from brief_history."""
    row = conn.execute(
        """
        SELECT model, story_count, generated_at
        FROM brief_history
        WHERE brief_type = ? AND success = 1
        ORDER BY generated_at DESC
        LIMIT 1
        """,
        (brief_type,),
    ).fetchone()
    if row:
        return {"model": row[0], "story_count": row[1], "generated_at": row[2][:19].replace("T", " ")}
    return {"model": "unknown", "story_count": 0, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}


def brief_has_actionable_content(brief_content: str, brief_type: str) -> bool:
    """Return True if the brief has at least one Tier 1 or Tier 2 story."""
    counts = count_tier_items(brief_content, brief_type)
    return counts.get("act_now", 0) > 0 or counts.get("monitor", 0) > 0


def build_suppression_email(brief_type: str) -> tuple[str, str, str]:
    """Return (subject, plain_text, html_str) for a suppression notification."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    label = "Morning Brief" if brief_type == "morning" else "Midday Brief"
    subject = f"{label} — {today}: No actionable signals. Brief suppressed."
    plain = subject
    html_content = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,sans-serif;font-size:14px;color:#374151;padding:24px;">
  <p style="color:#6b7280;">{subject}</p>
</body>
</html>"""
    return subject, plain, html_content


def build_email(
    brief_type: str,
    brief_content: str,
    metadata: dict,
) -> tuple[str, str, str]:
    """Return (subject, plain_text, html_str)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    label = "Morning Brief" if brief_type == "morning" else "Midday Brief"
    subject = f"{label} — {today}"

    # Extract theme line and tier counts for graphical header
    theme, brief_body = extract_theme(brief_content)
    tier_counts = count_tier_items(brief_body, brief_type)
    header_html = build_graphical_header(brief_type, today, tier_counts, theme)

    html_body = md_to_html(brief_body)
    full_html = HTML_TEMPLATE.format(
        header_html=header_html,
        generated_at=metadata["generated_at"],
        story_count=metadata["story_count"],
        model=metadata["model"],
        body_html=html_body,
    )
    return subject, brief_content, full_html


def send_email(
    subject: str,
    plain_text: str,
    html_content: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_address: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_address

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    log.info("Connecting to %s:%d", smtp_host, smtp_port)
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [to_address], msg.as_string())
    log.info("Email sent to %s", to_address)


def _write_suppression_log(brief_type: str) -> None:
    """Append a suppression record to logs/suppressed.log."""
    log_path = BASE_DIR / "logs" / "suppressed.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"{ts} UTC  {brief_type}  No Tier 1 or Tier 2 stories — brief suppressed\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily brief via email")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--brief-type", choices=["morning", "midday"])
    group.add_argument("--brief-path", type=Path, help="Send a specific brief file")
    parser.add_argument("--dry-run", action="store_true", help="Print HTML, do not send")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)

    # Resolve brief content and type
    if args.brief_path:
        brief_path = args.brief_path
        # Infer type from filename
        brief_type = "morning" if "morning" in brief_path.name else "midday"
        if not brief_path.exists():
            log.error("Brief file not found: %s", brief_path)
            sys.exit(1)
    else:
        brief_type = args.brief_type
        brief_path = find_latest_brief(brief_type)
        if not brief_path:
            log.error("No %s brief found in %s", brief_type, OUTPUT_DIR)
            sys.exit(1)

    log.info("Using brief: %s", brief_path)
    brief_content = brief_path.read_text()

    # Metadata from DB (best-effort)
    metadata = {"model": "unknown", "story_count": 0, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        metadata = get_brief_metadata(conn, brief_type)
        conn.close()

    # Check for actionable content — suppress if no Tier 1 or Tier 2 stories
    if not brief_has_actionable_content(brief_content, brief_type):
        log.info("Brief has no Tier 1 or Tier 2 stories — suppressing full send")
        _write_suppression_log(brief_type)
        subject, plain_text, html_content = build_suppression_email(brief_type)
    else:
        subject, plain_text, html_content = build_email(brief_type, brief_content, metadata)

    if args.dry_run:
        print(f"Subject: {subject}")
        print(f"Brief file: {brief_path}")
        print(f"HTML length: {len(html_content)} chars")
        print()
        print(html_content[:3000], "...[truncated]" if len(html_content) > 3000 else "")
        return

    # Load SMTP config
    required_vars = ["EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_USER", "EMAIL_PASSWORD", "EMAIL_TO"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        log.error("Missing .env variables: %s", ", ".join(missing))
        sys.exit(1)

    smtp_host = os.environ["EMAIL_SMTP_HOST"]
    smtp_port = int(os.environ["EMAIL_SMTP_PORT"])
    smtp_user = os.environ["EMAIL_USER"]
    smtp_password = os.environ["EMAIL_PASSWORD"]
    to_address = os.environ["EMAIL_TO"]

    send_email(subject, plain_text, html_content, smtp_host, smtp_port, smtp_user, smtp_password, to_address)
    log.info("Done")


if __name__ == "__main__":
    main()
