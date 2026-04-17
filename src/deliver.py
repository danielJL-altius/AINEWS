"""
Email delivery via SendGrid.

send_email()            — low-level: send to an explicit list of addresses.
deliver_to_subscribers() — high-level: load active subscribers from the DB,
                           personalize each email (filter topics + sources per
                           subscriber prefs), render, and send individually.
                           Falls back to TO_EMAILS from config if the DB has
                           no active subscribers.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import urllib.request
from pathlib import Path
from typing import List, Optional

from config import (
    DB_PATH,
    FROM_EMAIL,
    FROM_NAME,
    REPLY_TO,
    SENDGRID_API_KEY,
    TO_EMAILS,
)

log = logging.getLogger(__name__)


# =========================================================================
# LOW-LEVEL SENDGRID SEND
# =========================================================================

def _send_via_sendgrid(
    *,
    subject: str,
    html: str,
    plain: str,
    to_emails: List[str],
) -> bool:
    payload = {
        "personalizations": [{"to": [{"email": e} for e in to_emails]}],
        "from": {"email": FROM_EMAIL, "name": FROM_NAME},
        "reply_to": {"email": REPLY_TO},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": plain},
            {"type": "text/html", "value": html},
        ],
    }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        if 200 <= r.status < 300:
            log.info("SendGrid accepted email for %d recipient(s)", len(to_emails))
            return True
        log.error("SendGrid returned %s: %s", r.status, r.read()[:500])
        return False


def send_email(
    *,
    subject: str,
    html: str,
    plain: str,
    to_emails: Optional[List[str]] = None,
    fallback_dir: str = "data/sent",
) -> bool:
    """
    Send the email. If SENDGRID_API_KEY is missing, write to disk instead.
    """
    recipients = to_emails or TO_EMAILS

    if not SENDGRID_API_KEY:
        out = Path(fallback_dir)
        out.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%SZ")
        (out / f"{stamp}.html").write_text(html, encoding="utf-8")
        (out / f"{stamp}.txt").write_text(plain, encoding="utf-8")
        log.info("SENDGRID_API_KEY missing — wrote email to %s/%s.*", out, stamp)
        return True

    return _send_via_sendgrid(
        subject=subject, html=html, plain=plain, to_emails=recipients
    )


# =========================================================================
# PER-SUBSCRIBER PERSONALIZATION
# =========================================================================

def _filter_content_for_subscriber(content, prefs: dict):
    """
    Return a copy of EmailContent with categories and bullets filtered to the
    subscriber's enabled topics and sources.

    prefs dict keys:
      'topics'  — set of enabled topic names  (empty set → show all)
      'sources' — set of enabled source domains (empty set → show all)
    """
    from src.generate import CategorySection  # local import avoids circular dep at load time

    enabled_topics = prefs.get("topics") or set()
    enabled_sources = prefs.get("sources") or set()
    filter_topics = bool(enabled_topics)
    filter_sources = bool(enabled_sources)

    filtered_cats = []
    for cat in content.categories:
        if filter_topics and cat.name not in enabled_topics:
            continue

        if cat.name == "Markets":
            filtered_cats.append(cat)
            continue

        if filter_sources:
            filtered_bullets = [
                b for b in (cat.bullets or [])
                if not b.source or b.source in enabled_sources
            ]
        else:
            filtered_bullets = list(cat.bullets or [])

        filtered_cats.append(dataclasses.replace(cat, bullets=filtered_bullets))

    return dataclasses.replace(content, categories=filtered_cats)


# =========================================================================
# HIGH-LEVEL: DELIVER TO ALL SUBSCRIBERS
# =========================================================================

def deliver_to_subscribers(
    *,
    content,
    today_str: str,
    ingest_stats: dict,
    db_path: str = DB_PATH,
) -> int:
    """
    Load all active subscribers from the DB, personalize and send each their
    own copy of the brief filtered to their topic/source preferences.

    Falls back to TO_EMAILS from config if no active subscribers are in the DB.
    Returns the number of emails successfully sent.
    """
    from src.db import connect, get_all_subscribers, get_subscriber_prefs
    from src.render import render_email

    with connect(db_path) as conn:
        all_subs = get_all_subscribers(conn)

    active_subs = [s for s in all_subs if s["active"]]

    if not active_subs:
        log.info("No active DB subscribers — falling back to TO_EMAILS config list")
        html, plain = render_email(content, today_str=today_str, ingest_stats=ingest_stats)
        ok = send_email(subject=content.subject, html=html, plain=plain)
        return len(TO_EMAILS) if ok else 0

    sent_count = 0
    for sub in active_subs:
        with connect(db_path) as conn:
            prefs = get_subscriber_prefs(conn, sub["email"])

        personalized = _filter_content_for_subscriber(content, prefs)
        html, plain = render_email(personalized, today_str=today_str, ingest_stats=ingest_stats)

        ok = send_email(
            subject=personalized.subject,
            html=html,
            plain=plain,
            to_emails=[sub["email"]],
        )
        if ok:
            sent_count += 1
            log.info("Sent to %s (%s)", sub["name"] or sub["email"], sub["email"])
        else:
            log.error("Failed to send to %s", sub["email"])

    return sent_count
