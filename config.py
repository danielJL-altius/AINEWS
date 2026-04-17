from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

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
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")

# =========================================================================
# EMAIL DELIVERY
# =========================================================================

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

# =========================================================================
# PRIORITY COMPANIES — elevate material news involving these names
# =========================================================================

PRIORITY_COMPANIES: List[str] = [
    "Restaurant Brands International",
    "Kraft Heinz",
    "Hunter Douglas",
    "Skechers",
]

PRIORITY_TICKERS: List[str] = ["QSR", "KHC", "SKX"]

FLAG_NAMES_3G: List[str] = [
    "3G Capital",
    "Alex Behring",
    "Daniel Schwartz",
    "Jorge Paulo Lemann",
    "Joao Castro Neves",
    "João Castro Neves",
]

# =========================================================================
# KEYWORD SEED LISTS PER CATEGORY
#
# NewsAPI.ai free tier caps us at 15 *word tokens* per OR query (multi-word
# phrases count as N tokens, one per word). So "Restaurant Brands International"
# burns 3 tokens. These lists are tuned to stay under the cap.
# =========================================================================

CATEGORY_KEYWORDS = {
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

# =========================================================================
# INGESTION WINDOW
# =========================================================================

LOOKBACK_HOURS = 24
MAX_ARTICLES_PER_CATEGORY = 40

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

# =========================================================================
# DASHBOARD (admin web UI)
# =========================================================================

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "altius2026")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "change-me-in-production")