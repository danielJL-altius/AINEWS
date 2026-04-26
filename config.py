from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

# Load .env file if present. Must happen before any os.getenv() calls below.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # python-dotenv is optional; env vars can be set in the shell instead


# =========================================================================
# API KEYS — loaded from environment (.env) at runtime
# =========================================================================

NEWSAPI_AI_KEY = os.getenv("NEWSAPI_AI_KEY", "e29c75b9-8da8-44a2-8dea-d86db249ddaf")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# =========================================================================
# EMAIL DELIVERY (Mailgun — https://www.mailgun.com/)
# =========================================================================
#
# MAILGUN_DOMAIN: verified sending domain in Mailgun (e.g. mg.example.com).
# MAILGUN_REGION: "us" (api.mailgun.net) or "eu" (api.eu.mailgun.net).

MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN", "").strip()
MAILGUN_REGION = os.getenv("MAILGUN_REGION", "us").strip().lower()

FROM_EMAIL = os.getenv("FROM_EMAIL", "news@altius.capital")
FROM_NAME = os.getenv("FROM_NAME", "Altius Daily News")
TO_EMAILS = [e.strip() for e in os.getenv("TO_EMAILS", "daniel.leubitz@altius.capital").split(",") if e.strip()]
REPLY_TO = os.getenv("REPLY_TO", "daniel.leubitz@altius.capital")

# =========================================================================
# CATEGORIES (in order, exactly as specified by the master prompt)
# =========================================================================

CATEGORIES: List[str] = [
    "QSR",
    "Housing / Repair & Remodel (R&R)",
    "Footwear / Apparel",
    "Private Equity / Investing",
    "Technology",
    "Keyword alerts",  # firm / partner / watchlist — configured via WATCHLIST_KEYWORDS
    "Markets",
]

# =========================================================================
# PREFERRED SOURCES — Event Registry source URIs
# =========================================================================

PREFERRED_SOURCES: List[str] = [
    "wsj.com",
    "ft.com",
    "bloomberg.com",
    "reuters.com",
    "nytimes.com",
    "barrons.com",
    "institutionalinvestor.com",
    "restaurantbusinessonline.com",
    "economictimes.indiatimes.com",
    "cnbc.com",
    "finance.yahoo.com",
    "biztoc.com",
]

# Human-readable display name for each source domain (same order as PREFERRED_SOURCES).
# Used in email footer to list all monitored sources and highlight which had content today.
SOURCE_DISPLAY_NAMES: dict = {
    "wsj.com":                        "WSJ",
    "ft.com":                         "Financial Times",
    "bloomberg.com":                  "Bloomberg",
    "reuters.com":                    "Reuters",
    "nytimes.com":                    "NYT",
    "barrons.com":                    "Barron's",
    "institutionalinvestor.com":      "Institutional Investor",
    "restaurantbusinessonline.com":   "Restaurant Business Online",
    "economictimes.indiatimes.com":   "Economic Times",
    "cnbc.com":                       "CNBC",
    "finance.yahoo.com":              "Yahoo Finance",
    "biztoc.com":                     "BizToc",
}


def normalize_source_uri(raw: str) -> str:
    """
    Map user/API input to an Event Registry sourceUri (lowercase domain, no path).
    """
    s = (raw or "").strip().lower()
    if "://" in s:
        s = (urlparse(s).netloc or s).lower()
    s = s.lstrip("www.").rstrip("/")
    if "/" in s:
        s = s.split("/")[0]
    s = s.split("?")[0]
    if s.startswith("*."):
        s = s[2:]
    return s


def _extra_sources_from_snapshot(snapshot: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Return [{'uri': 'theguardian.com', 'title': 'The Guardian'}, ...] from ingest_settings.json.
    """
    out: List[Dict[str, str]] = []
    raw = (snapshot or {}).get("extra_sources")
    if not isinstance(raw, list):
        return out
    seen: set = set()
    for item in raw:
        if isinstance(item, str) and item.strip():
            u = normalize_source_uri(item)
            if u and u not in seen:
                seen.add(u)
                out.append({"uri": u, "title": u})
        elif isinstance(item, dict) and item.get("uri"):
            u = normalize_source_uri(str(item["uri"]))
            if not u or u in seen:
                continue
            seen.add(u)
            t = (item.get("title") or item.get("name") or "").strip()
            out.append({"uri": u, "title": t or u})
    return out


def get_monitored_sources() -> List[str]:
    """
    Built-in PREFERRED_SOURCES plus any extra domained sources saved in
    data/ingest_settings.json (key: extra_sources). Each process call re-reads
    the file so the dashboard and cron see updates without a code deploy.
    """
    snap = _load_ingest_snapshot()
    extras = _extra_sources_from_snapshot(snap)
    extra_uris = [e["uri"] for e in extras]
    seen: set = set()
    out: List[str] = []
    for u in list(PREFERRED_SOURCES) + extra_uris:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def get_effective_source_display_names() -> Dict[str, str]:
    """SOURCE_DISPLAY_NAMES plus optional titles for extra sources from ingest_settings.json."""
    names: Dict[str, str] = dict(SOURCE_DISPLAY_NAMES)
    snap = _load_ingest_snapshot()
    for e in _extra_sources_from_snapshot(snap):
        u, t = e["uri"], (e.get("title") or "").strip()
        if t:
            names[u] = t
    ovr = snap.get("source_display_names")
    if isinstance(ovr, dict):
        for k, v in ovr.items():
            ku = normalize_source_uri(str(k))
            if ku and v and str(v).strip():
                names[ku] = str(v).strip()
    return names


# =========================================================================
# PRIORITY COMPANIES / TICKERS / 3G FLAGS — defaults (merged with data/ingest_settings.json)
# =========================================================================
# PRIORITY_COMPANIES, PRIORITY_TICKERS, FLAG_NAMES_3G are assigned after _INGEST_SNAPSHOT.

# =========================================================================
# KEYWORD SEED LISTS PER CATEGORY
#
# NewsAPI.ai free tier caps us at 15 *word tokens* per OR query (multi-word
# phrases count as N tokens, one per word). So "Restaurant Brands International"
# burns 3 tokens. These lists are tuned to stay under the cap.
# =========================================================================

_SECTOR_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "QSR": [
        "Restaurant Brands",   # 2
        "McDonald's",          # 1
        "Chipotle",            # 1
        "Starbucks",           # 1
        "Yum Brands",          # 2
        "Kraft Heinz",         # 2
        "fast food",           # 2
        # total: 11
    ],
    "Housing / Repair & Remodel (R&R)": [
        "Hunter Douglas",      # 2
        "Home Depot",          # 2
        "Lowe's",              # 1
        "Sherwin-Williams",    # 1
        "housing starts",      # 2
        "home improvement",    # 2
        "mortgage rates",      # 2
        # total: 12
    ],
    "Footwear / Apparel": [
        "Skechers", "Nike", "Adidas", "Lululemon",
        "Under Armour", "footwear", "apparel",
        "Deckers", "HOKA", "Crocs",
        # total: 11
    ],
    "Private Equity / Investing": [
        "private equity",      # 2
        "leveraged buyout",    # 2
        "KKR",                 # 1
        "Blackstone",          # 1
        "Apollo",              # 1
        "Carlyle",             # 1
        "3G Capital",          # 2
        "take-private",        # 1
        "activist investor",   # 2
        # total: 13
    ],
    "Technology": [
        "artificial intelligence",  # 2
        "semiconductors",           # 1
        "Nvidia",                   # 1
        "Microsoft",                # 1
        "Apple",                    # 1
        "Alphabet",                 # 1
        "Amazon",                   # 1
        "Meta",                     # 1
        "data center",              # 2
        "cloud computing",          # 2
        # total: 13
    ],
}

# Ordered sector keys for ingestion UI and data/ingest_settings.json
SECTOR_INGEST_CATEGORIES: List[str] = list(_SECTOR_CATEGORY_KEYWORDS.keys())

_DEFAULT_WATCHLIST_FALLBACK: List[str] = [
    "Altius Capital",
    "3G Capital",
    "Restaurant Brands International",
    "Kraft Heinz",
]

_DEFAULT_PRIORITY_COMPANIES: List[str] = [
    "Restaurant Brands International",
    "Kraft Heinz",
    "Hunter Douglas",
    "Skechers",
]

_DEFAULT_PRIORITY_TICKERS: List[str] = ["QSR", "KHC", "SKX"]

_DEFAULT_FLAG_NAMES_3G: List[str] = [
    "3G Capital",
    "Alex Behring",
    "Daniel Schwartz",
    "Jorge Paulo Lemann",
    "Joao Castro Neves",
    "João Castro Neves",
]


def _load_ingest_snapshot() -> Dict[str, Any]:
    """On-disk settings from dashboard (data/ingest_settings.json)."""
    p = Path(__file__).resolve().parent / "data" / "ingest_settings.json"
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_str_list(snapshot: Dict[str, Any], key: str, default: List[str]) -> List[str]:
    if not snapshot or key not in snapshot:
        return list(default)
    v = snapshot[key]
    if not isinstance(v, list):
        return list(default)
    return [str(x).strip() for x in v if str(x).strip()]


def _merge_sector_keywords(snapshot: Dict[str, Any]) -> Dict[str, List[str]]:
    merged = {k: list(v) for k, v in _SECTOR_CATEGORY_KEYWORDS.items()}
    sk = snapshot.get("sector_keywords") if snapshot else None
    if not isinstance(sk, dict):
        return merged
    for k, v in sk.items():
        if k in merged and isinstance(v, list):
            merged[k] = [str(x).strip() for x in v if str(x).strip()]
    return merged


def _resolve_watchlist(snapshot: Dict[str, Any]) -> List[str]:
    """
    WATCHLIST_KEYWORDS env overrides file and defaults (12-factor).
    If ingest_settings.json sets watchlist_keywords (including []), use that.
    Otherwise use built-in defaults.
    """
    raw = os.getenv("WATCHLIST_KEYWORDS", "").strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    if snapshot and "watchlist_keywords" in snapshot:
        wl = snapshot.get("watchlist_keywords")
        if isinstance(wl, list):
            return [str(x).strip() for x in wl if str(x).strip()]
        return []
    return list(_DEFAULT_WATCHLIST_FALLBACK)


def _int_from_env_or_snapshot(
    env_key: str, snapshot: Dict[str, Any], snap_key: str, default: int
) -> int:
    ev = os.getenv(env_key)
    if ev is not None and str(ev).strip() != "":
        try:
            return int(ev)
        except ValueError:
            pass
    if snapshot and snap_key in snapshot:
        try:
            return int(snapshot[snap_key])
        except (TypeError, ValueError):
            pass
    return default


_INGEST_SNAPSHOT = _load_ingest_snapshot()

_SECTOR_MERGED = _merge_sector_keywords(_INGEST_SNAPSHOT)
CATEGORY_KEYWORDS: Dict[str, List[str]] = dict(_SECTOR_MERGED)
_WATCHLIST = _resolve_watchlist(_INGEST_SNAPSHOT)
if _WATCHLIST:
    CATEGORY_KEYWORDS["Keyword alerts"] = _WATCHLIST

# Exposed for prompts so the LLM knows which terms drive this section.
WATCHLIST_KEYWORDS: List[str] = list(_WATCHLIST)

PRIORITY_COMPANIES: List[str] = _merge_str_list(
    _INGEST_SNAPSHOT, "priority_companies", _DEFAULT_PRIORITY_COMPANIES
)
PRIORITY_TICKERS: List[str] = _merge_str_list(
    _INGEST_SNAPSHOT, "priority_tickers", _DEFAULT_PRIORITY_TICKERS
)
FLAG_NAMES_3G: List[str] = _merge_str_list(
    _INGEST_SNAPSHOT, "flag_names_3g", _DEFAULT_FLAG_NAMES_3G
)

# =========================================================================
# INGESTION WINDOW
# =========================================================================

LOOKBACK_HOURS = 24
MAX_ARTICLES_PER_CATEGORY = 40
MAX_KEYWORD_ALERT_ARTICLES = _int_from_env_or_snapshot(
    "MAX_KEYWORD_ALERT_ARTICLES",
    _INGEST_SNAPSHOT,
    "max_keyword_alert_articles",
    32,
)

# Prune SQLite `articles` rows older than this (days, UTC). LLM dedup index uses DEDUP_CONTEXT_DAYS.
ARTICLE_RETENTION_DAYS = int(os.getenv("ARTICLE_RETENTION_DAYS", "30"))
# Compact multi-day dedup context for Claude (titles/URLs from DB). Should be <= retention.
DEDUP_CONTEXT_DAYS = int(os.getenv("DEDUP_CONTEXT_DAYS", "30"))
DEDUP_CONTEXT_MAX_ROWS = int(os.getenv("DEDUP_CONTEXT_MAX_ROWS", "3000"))
DEDUP_TITLE_MAX_CHARS = int(os.getenv("DEDUP_TITLE_MAX_CHARS", "140"))
DEDUP_CONTEXT_MAX_CHARS = int(os.getenv("DEDUP_CONTEXT_MAX_CHARS", "55000"))

# =========================================================================
# DATABASE
# =========================================================================

DB_PATH = os.getenv("DB_PATH", "data/news.db")

# =========================================================================
# LLM
# =========================================================================

LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_MAX_TOKENS = 8000

# =========================================================================
# TIMEZONE
# =========================================================================

TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

# New subscribers: hour (0–23) in TIMEZONE when they want the daily brief delivered.
# The scheduled job’s clock (Eastern) must match this hour, or set SEND_IGNORE_PREFERRED_HOUR.
_default_pref_h = int(os.getenv("DEFAULT_PREFERRED_SEND_HOUR_ET", "8"))
DEFAULT_PREFERRED_SEND_HOUR_ET: int = max(0, min(23, _default_pref_h))

# If true, every active subscriber gets mail on every run; preferred hour is stored but not enforced.
SEND_IGNORE_PREFERRED_HOUR: bool = os.getenv("SEND_IGNORE_PREFERRED_HOUR", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def format_preferred_send_hour_label(hour_et: int) -> str:
    """Readable label in 12-hour clock (Eastern) for admin UI and logs."""
    h = int(hour_et) % 24
    if h == 0:
        return "12:00 AM (ET)"
    if h < 12:
        return f"{h}:00 AM (ET)"
    if h == 12:
        return "12:00 PM (ET)"
    return f"{h - 12}:00 PM (ET)"


# =========================================================================
# INBOUND EMAIL (forward-to-ingest webhook) — see inbound_server.py
# =========================================================================
#
# Mailgun Routes forward inbound mail to /webhooks/inbound-email?token=...
# FORWARD_ALLOWED_SENDERS: comma-separated allowlist. Use full email and/or
# @domain entries (e.g. "alice@firm.com,@client.com"). Empty = allow any
# sender that presents the webhook secret (use only with HTTPS + secret).

INBOUND_WEBHOOK_SECRET = os.getenv("INBOUND_WEBHOOK_SECRET", "")
FORWARD_ALLOWED_SENDERS = os.getenv("FORWARD_ALLOWED_SENDERS", "")


def _int_from_file_only(snapshot: Dict[str, Any], key: str, default: int) -> int:
    if snapshot and key in snapshot:
        try:
            return int(snapshot[key])
        except (TypeError, ValueError):
            pass
    return default


def _watchlist_keywords_for_form(snapshot: Dict[str, Any]) -> List[str]:
    """Values shown in the Keyword alerts textarea (file overrides; else defaults)."""
    if "watchlist_keywords" in snapshot:
        v = snapshot.get("watchlist_keywords")
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []
    return list(_DEFAULT_WATCHLIST_FALLBACK)


def get_ingest_dashboard_context() -> Dict[str, Any]:
    """
    Fresh read for the /settings page (do not rely on module-level snapshot
    so edits appear immediately after save).
    """
    snap = _load_ingest_snapshot()
    env_wl = bool(os.getenv("WATCHLIST_KEYWORDS", "").strip())
    env_max = os.getenv("MAX_KEYWORD_ALERT_ARTICLES", "").strip() != ""
    return {
        "sector_keywords": _merge_sector_keywords(snap),
        "watchlist_keywords": _watchlist_keywords_for_form(snap),
        "priority_companies": _merge_str_list(snap, "priority_companies", _DEFAULT_PRIORITY_COMPANIES),
        "priority_tickers": _merge_str_list(snap, "priority_tickers", _DEFAULT_PRIORITY_TICKERS),
        "flag_names_3g": _merge_str_list(snap, "flag_names_3g", _DEFAULT_FLAG_NAMES_3G),
        "max_keyword_alert_articles": _int_from_env_or_snapshot(
            "MAX_KEYWORD_ALERT_ARTICLES", snap, "max_keyword_alert_articles", 32
        ),
        "max_keyword_alert_file": _int_from_file_only(snap, "max_keyword_alert_articles", 32),
        "env_overrides_watchlist": env_wl,
        "env_overrides_max_kw": env_max,
        "extra_sources": _extra_sources_from_snapshot(snap),
    }


# =========================================================================
# DASHBOARD (admin web UI)
# =========================================================================

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "altius2026")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "change-me-in-production")