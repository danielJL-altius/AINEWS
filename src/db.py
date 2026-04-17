"""
SQLite persistence for articles, sent emails, and subscriber management.

Tables:
- articles: the firehose of candidate articles pulled from NewsAPI.ai
- sent_emails: history of what we've already shipped, used for dedup
- subscribers: people who receive the daily brief
- subscriber_prefs: per-subscriber topic/source toggles

Schema is intentionally minimal. Upgrade to Postgres when we outgrow single-writer.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url          TEXT PRIMARY KEY,
    source       TEXT,
    title        TEXT,
    body         TEXT,
    published_at TEXT,          -- ISO 8601 UTC
    fetched_at   TEXT,          -- ISO 8601 UTC
    category_hint TEXT,         -- which category bucket we pulled this for
    raw_json     TEXT           -- full article payload for debugging
);

CREATE INDEX IF NOT EXISTS idx_articles_published
    ON articles (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_articles_category_hint
    ON articles (category_hint);

CREATE TABLE IF NOT EXISTS sent_emails (
    sent_date    TEXT PRIMARY KEY,  -- YYYY-MM-DD ET
    subject      TEXT,
    html         TEXT,
    plain        TEXT,
    urls_json    TEXT,              -- JSON array of URLs that appeared in the email
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS subscribers (
    email       TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriber_prefs (
    email       TEXT NOT NULL REFERENCES subscribers(email) ON DELETE CASCADE,
    pref_type   TEXT NOT NULL,   -- 'topic' or 'source'
    pref_value  TEXT NOT NULL,   -- e.g. 'QSR' or 'wsj.com'
    enabled     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (email, pref_type, pref_value)
);
"""


def init_db(db_path: str) -> None:
    """Create the database file and schema if they don't exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def upsert_article(
    conn: sqlite3.Connection,
    *,
    url: str,
    source: str,
    title: str,
    body: str,
    published_at: str,
    category_hint: str,
    raw_json: str,
) -> None:
    """
    Insert-or-ignore — if we've already seen this URL we leave the original
    row alone (the first category_hint wins).
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (url, source, title, body, published_at, fetched_at, category_hint, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            url,
            source,
            title,
            body,
            published_at,
            datetime.utcnow().isoformat() + "Z",
            category_hint,
            raw_json,
        ),
    )


def fetch_recent_articles(
    conn: sqlite3.Connection,
    since_iso: str,
) -> List[sqlite3.Row]:
    """Pull every article we've stored with published_at >= since_iso."""
    cur = conn.execute(
        """
        SELECT url, source, title, body, published_at, category_hint
        FROM articles
        WHERE published_at >= ?
        ORDER BY published_at DESC
        """,
        (since_iso,),
    )
    return list(cur.fetchall())


def save_sent_email(
    conn: sqlite3.Connection,
    *,
    sent_date: str,
    subject: str,
    html: str,
    plain: str,
    urls_json: str,
) -> None:
    """Archive the email we just sent so tomorrow's dedup has context."""
    conn.execute(
        """
        INSERT OR REPLACE INTO sent_emails
            (sent_date, subject, html, plain, urls_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            sent_date,
            subject,
            html,
            plain,
            urls_json,
            datetime.utcnow().isoformat() + "Z",
        ),
    )
    conn.commit()


def get_prior_email(
    conn: sqlite3.Connection,
    prior_date: str,
) -> Optional[sqlite3.Row]:
    """Fetch yesterday's email for dedup context."""
    cur = conn.execute(
        "SELECT sent_date, subject, plain, urls_json FROM sent_emails WHERE sent_date = ?",
        (prior_date,),
    )
    return cur.fetchone()


# =========================================================================
# SUBSCRIBER MANAGEMENT
# =========================================================================

def get_all_subscribers(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Return all subscribers ordered by creation date descending."""
    return list(conn.execute(
        "SELECT email, name, active, created_at FROM subscribers ORDER BY created_at DESC"
    ).fetchall())


def get_subscriber(
    conn: sqlite3.Connection,
    email: str,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT email, name, active, created_at FROM subscribers WHERE email = ?",
        (email,),
    ).fetchone()


def create_subscriber(
    conn: sqlite3.Connection,
    *,
    email: str,
    name: str,
) -> None:
    """
    Insert a new subscriber and seed their prefs with all topics and sources
    enabled so they receive the full brief by default.
    """
    from config import CATEGORIES, PREFERRED_SOURCES

    conn.execute(
        "INSERT OR IGNORE INTO subscribers (email, name, active, created_at) VALUES (?, ?, 1, ?)",
        (email, name, datetime.utcnow().isoformat() + "Z"),
    )
    for topic in CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO subscriber_prefs (email, pref_type, pref_value, enabled) VALUES (?, 'topic', ?, 1)",
            (email, topic),
        )
    for source in PREFERRED_SOURCES:
        conn.execute(
            "INSERT OR IGNORE INTO subscriber_prefs (email, pref_type, pref_value, enabled) VALUES (?, 'source', ?, 1)",
            (email, source),
        )


def upsert_subscriber(
    conn: sqlite3.Connection,
    *,
    email: str,
    name: str,
    active: int = 1,
) -> None:
    conn.execute(
        "UPDATE subscribers SET name = ?, active = ? WHERE email = ?",
        (name, active, email),
    )


def delete_subscriber(conn: sqlite3.Connection, email: str) -> None:
    conn.execute("DELETE FROM subscriber_prefs WHERE email = ?", (email,))
    conn.execute("DELETE FROM subscribers WHERE email = ?", (email,))


def get_subscriber_prefs(
    conn: sqlite3.Connection,
    email: str,
) -> Dict[str, Set[str]]:
    """
    Return {'topics': {enabled_topic, ...}, 'sources': {enabled_source, ...}}.
    An empty set for a given type means nothing is enabled (subscriber sees nothing
    for that dimension). The caller should fall back to "all" only when the
    subscriber has zero rows of that pref_type — use get_subscriber_pref_counts()
    to distinguish "empty set" from "no prefs recorded".
    """
    rows = conn.execute(
        "SELECT pref_type, pref_value, enabled FROM subscriber_prefs WHERE email = ?",
        (email,),
    ).fetchall()

    topics: Set[str] = set()
    sources: Set[str] = set()
    for row in rows:
        if row["pref_type"] == "topic" and row["enabled"]:
            topics.add(row["pref_value"])
        elif row["pref_type"] == "source" and row["enabled"]:
            sources.add(row["pref_value"])

    return {"topics": topics, "sources": sources}


def set_subscriber_prefs(
    conn: sqlite3.Connection,
    email: str,
    *,
    topics: List[str],
    sources: List[str],
) -> None:
    """Replace all prefs for a subscriber. topics/sources are the enabled values."""
    from config import CATEGORIES, PREFERRED_SOURCES

    for topic in CATEGORIES:
        enabled = 1 if topic in topics else 0
        conn.execute(
            "INSERT OR REPLACE INTO subscriber_prefs (email, pref_type, pref_value, enabled) VALUES (?, 'topic', ?, ?)",
            (email, topic, enabled),
        )
    for source in PREFERRED_SOURCES:
        enabled = 1 if source in sources else 0
        conn.execute(
            "INSERT OR REPLACE INTO subscriber_prefs (email, pref_type, pref_value, enabled) VALUES (?, 'source', ?, ?)",
            (email, source, enabled),
        )
