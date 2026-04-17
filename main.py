"""
Main orchestrator for the Daily News Email.

Pipeline:
  1. ingest_all()     — pull last 24h articles from NewsAPI.ai
  2. get_market_snapshot() — pre-market futures/FX/yields/commodities/crypto
  3. generate_email_content()  — LLM categorization + dedup using Justin's master prompt
  4. render_email()   — HTML + plain-text
  5. send_email()     — SendGrid (or disk fallback)
  6. save_sent_email() — archive for tomorrow's dedup

Run manually with `python main.py` or schedule via cron at ~07:15 ET.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import DB_PATH, LOOKBACK_HOURS, PREFERRED_SOURCES, SOURCE_DISPLAY_NAMES, TIMEZONE
from src.db import connect, fetch_recent_articles, get_prior_email, init_db, save_sent_email
from src.deliver import deliver_to_subscribers, send_email
from src.generate import generate_email_content
from src.ingest import ingest_all
from src.markets import get_market_snapshot
from src.render import render_email


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def run(*, dry_run: bool = False, skip_ingest: bool = False, verbose: bool = True) -> int:
    _setup_logging(verbose)
    log = logging.getLogger("main")

    tz = ZoneInfo(TIMEZONE)
    now_local = datetime.now(tz)
    today_str = now_local.strftime("%b %-d, %Y") if sys.platform != "win32" else now_local.strftime("%b %#d, %Y")
    today_date = now_local.strftime("%Y-%m-%d")
    yesterday_date = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

    init_db(DB_PATH)

    # --- 1. Ingest ---
    ingest_summary: dict = {}
    if not skip_ingest:
        log.info("Starting ingestion …")
        ingest_summary = ingest_all(DB_PATH, verbose=verbose)
        log.info("Ingestion summary: %s", ingest_summary)

    # --- 2. Pull candidates from DB ---
    since_utc = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    with connect(DB_PATH) as conn:
        rows = fetch_recent_articles(conn, since_utc)
        prior = get_prior_email(conn, yesterday_date)

    articles = [dict(r) for r in rows]
    log.info("Loaded %d candidate articles from DB", len(articles))

    # --- 3. Markets snapshot ---
    log.info("Fetching market snapshot …")
    markets = get_market_snapshot()

    # --- 4. LLM generation ---
    log.info("Generating email content via LLM …")
    content = generate_email_content(
        articles=articles,
        prior_email_plain=(prior["plain"] if prior else None),
        markets=markets,
        today_str=today_str,
    )

    # --- 5. Render ---
    # Count bullets surfaced in the final email (Markets has no bullets).
    articles_surfaced = sum(
        len(cat.bullets or []) for cat in content.categories
    )

    # Determine which monitored source domains were cited in at least one bullet.
    # Match by checking if a preferred domain appears in the bullet's URL.
    from urllib.parse import urlparse
    active_domains: set = set()
    for cat in content.categories:
        for b in (cat.bullets or []):
            if b.url:
                try:
                    netloc = urlparse(b.url).netloc.lower().lstrip("www.")
                    for domain in PREFERRED_SOURCES:
                        if domain in netloc or netloc.endswith(domain):
                            active_domains.add(domain)
                            break
                except Exception:
                    pass

    # Build ordered source list with active flag for the template.
    all_sources = [
        {"domain": d, "name": SOURCE_DISPLAY_NAMES.get(d, d), "active": d in active_domains}
        for d in PREFERRED_SOURCES
    ]

    ingest_stats = {
        "sources_count": len(PREFERRED_SOURCES),
        "articles_scanned": sum(ingest_summary.values()) if ingest_summary else len(articles),
        "articles_to_llm": len(articles),
        "articles_surfaced": articles_surfaced,
        "all_sources": all_sources,
    }
    html, plain = render_email(content, today_str=today_str, ingest_stats=ingest_stats)

    # --- 6. Send (or dry-run) ---
    if dry_run:
        log.info("DRY RUN — writing preview to data/preview/")
        from pathlib import Path
        out = Path("data/preview")
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{today_date}.html").write_text(html, encoding="utf-8")
        (out / f"{today_date}.txt").write_text(plain, encoding="utf-8")
        (out / f"{today_date}.subject.txt").write_text(content.subject, encoding="utf-8")
        log.info("Preview saved. Open data/preview/%s.html in your browser.", today_date)
    else:
        sent = deliver_to_subscribers(
            content=content,
            today_str=today_str,
            ingest_stats=ingest_stats,
        )
        log.info("Delivered to %d subscriber(s).", sent)

    # --- 7. Archive for dedup ---
    urls = []
    for cat in content.categories:
        for b in (cat.bullets or []):
            if b.url:
                urls.append(b.url)
    with connect(DB_PATH) as conn:
        save_sent_email(
            conn,
            sent_date=today_date,
            subject=content.subject,
            html=html,
            plain=plain,
            urls_json=json.dumps(urls),
        )

    log.info("Done.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate & send the Daily News Email")
    parser.add_argument("--dry-run", action="store_true", help="Write email to disk instead of sending")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip NewsAPI.ai fetch; use what's already in the DB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    args = parser.parse_args()
    return run(dry_run=args.dry_run, skip_ingest=args.skip_ingest, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
