"""
Email delivery via Mailgun (HTTPS API).

send_email()            — low-level: send to an explicit list of addresses.
deliver_to_subscribers() — high-level: load active subscribers from the DB,
                           personalize each email (filter topics + sources per
                           subscriber prefs), render, and send individually.
                           Falls back to TO_EMAILS from config if the DB has
                           no active subscribers.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from config import (
    DB_PATH,
    FROM_EMAIL,
    FROM_NAME,
    MAILGUN_API_KEY,
    MAILGUN_DOMAIN,
    MAILGUN_REGION,
    REPLY_TO,
    TO_EMAILS,
)

log = logging.getLogger(__name__)


def _mailgun_messages_url() -> str:
    base = (
        "https://api.eu.mailgun.net/v3"
        if MAILGUN_REGION == "eu"
        else "https://api.mailgun.net/v3"
    )
    return f"{base}/{MAILGUN_DOMAIN}/messages"


def _send_via_mailgun(
    *,
    subject: str,
    html: str,
    plain: str,
    to_emails: List[str],
) -> bool:
    if not MAILGUN_DOMAIN:
        log.error("MAILGUN_DOMAIN is not set")
        return False

    auth = base64.b64encode(f"api:{MAILGUN_API_KEY}".encode()).decode()
    data = urllib.parse.urlencode(
        {
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": ", ".join(to_emails),
            "subject": subject,
            "text": plain,
            "html": html,
            "h:Reply-To": REPLY_TO,
        },
        doseq=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        _mailgun_messages_url(),
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()[:500]
            if 200 <= r.status < 300:
                log.info("Mailgun accepted email for %d recipient(s)", len(to_emails))
                return True
            log.error("Mailgun returned %s: %s", r.status, body)
            return False
    except urllib.error.HTTPError as e:
        err = e.read()[:800] if e.fp else b""
        log.error("Mailgun HTTP %s: %s", e.code, err.decode("utf-8", errors="replace"))
        return False
    except OSError as e:
        log.error("Mailgun request failed: %s", e)
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
    Send the email. If MAILGUN_API_KEY or MAILGUN_DOMAIN is missing, write to disk instead.
    """
    recipients = to_emails or TO_EMAILS

    if not MAILGUN_API_KEY or not MAILGUN_DOMAIN:
        out = Path(fallback_dir)
        out.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%SZ")
        (out / f"{stamp}.html").write_text(html, encoding="utf-8")
        (out / f"{stamp}.txt").write_text(plain, encoding="utf-8")
        log.info(
            "MAILGUN_API_KEY or MAILGUN_DOMAIN missing — wrote email to %s/%s.*",
            out,
            stamp,
        )
        return True

    return _send_via_mailgun(
        subject=subject, html=html, plain=plain, to_emails=recipients
    )


# =========================================================================
# PER-SUBSCRIBER PERSONALIZATION
# =========================================================================

def _url_matches_source_domains(url: str, enabled_domains: set) -> bool:
    """True if the article URL's host is one of the Event Registry source domains."""
    if not url or not enabled_domains:
        return False
    from urllib.parse import urlparse

    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return False
    for dom in enabled_domains:
        d = (dom or "").lower().strip()
        if not d:
            continue
        if d in netloc or netloc.endswith(d):
            return True
    return False


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
                b
                for b in (cat.bullets or [])
                if (b.url and _url_matches_source_domains(b.url, enabled_sources))
                or (b.source and b.source in enabled_sources)
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
