"""
main.py — Daily Brief pipeline orchestrator.

Runs: fetch → normalize → synthesize → deliver

Usage:
    python main.py --brief-type morning
    python main.py --brief-type midday
    python main.py --brief-type morning --skip-delivery   # synthesize only
    python main.py --brief-type morning --dry-run         # no API calls, no email
"""

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "logs" / "main.log"
PYTHON = BASE_DIR / "venv" / "bin" / "python"

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


def run_stage(name: str, cmd: list[str], dry_run: bool = False) -> bool:
    """Run a pipeline stage subprocess. Returns True on success."""
    display_cmd = " ".join(str(c) for c in cmd)
    log.info("[%s] %s", name, display_cmd)
    t0 = time.monotonic()
    try:
        result = subprocess.run(cmd, check=True)
        elapsed = time.monotonic() - t0
        log.info("[%s] OK (%.1fs)", name, elapsed)
        return True
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - t0
        log.error("[%s] FAILED after %.1fs — exit code %d", name, elapsed, exc.returncode)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Brief pipeline")
    parser.add_argument("--brief-type", required=True, choices=["morning", "midday"])
    parser.add_argument("--skip-delivery", action="store_true", help="Skip email delivery stage")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to all stages (no writes, no API)")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    log.info("=== Daily Brief pipeline START — type=%s dry_run=%s ===", args.brief_type, args.dry_run)

    dry = ["--dry-run"] if args.dry_run else []
    stages_failed = []

    # --- Stage 1: Fetch ---
    fetch_cmd = [PYTHON, BASE_DIR / "fetchers" / "rss_fetcher.py"] + dry
    if not run_stage("fetch", fetch_cmd):
        stages_failed.append("fetch")
        log.error("Fetch stage failed — aborting pipeline")
        sys.exit(1)

    # --- Stage 2: Normalize ---
    normalize_cmd = [PYTHON, BASE_DIR / "parsers" / "normalize.py"] + dry
    if not run_stage("normalize", normalize_cmd):
        stages_failed.append("normalize")
        log.error("Normalize stage failed — aborting pipeline")
        sys.exit(1)

    # --- Stage 3: Synthesize ---
    synthesize_cmd = [
        PYTHON, BASE_DIR / "synthesis" / "synthesize.py",
        "--brief-type", args.brief_type,
    ] + dry
    if not run_stage("synthesize", synthesize_cmd):
        stages_failed.append("synthesize")
        log.error("Synthesize stage failed — aborting pipeline")
        sys.exit(1)

    # --- Stage 4: Deliver ---
    if args.skip_delivery or args.dry_run:
        log.info("[deliver] Skipped (skip_delivery=%s, dry_run=%s)", args.skip_delivery, args.dry_run)
    else:
        deliver_cmd = [
            PYTHON, BASE_DIR / "delivery" / "send_brief.py",
            "--brief-type", args.brief_type,
        ]
        if not run_stage("deliver", deliver_cmd):
            stages_failed.append("deliver")
            # Delivery failure is non-fatal — brief was synthesized successfully
            log.warning("Delivery failed but brief was generated — check logs/email.log")

    elapsed_total = (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()
    if stages_failed:
        log.error("=== Pipeline DONE with failures: %s (%.1fs) ===", stages_failed, elapsed_total)
        sys.exit(1)
    else:
        log.info("=== Pipeline DONE — %s brief complete (%.1fs) ===", args.brief_type, elapsed_total)


if __name__ == "__main__":
    main()
