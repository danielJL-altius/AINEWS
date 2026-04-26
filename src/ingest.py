"""
News ingestion — pulls candidate articles from NewsAPI.ai (Event Registry)
for every category defined in config.CATEGORY_KEYWORDS.

Design notes:
- One Event Registry query per category, filtered to our preferred sources.
- Articles newer than LOOKBACK_HOURS are kept.
- Duplicates (same URL) across categories are collapsed at the DB layer.
- The raw article JSON is stored so we can debug/re-run the LLM step without
  re-hitting the API.

NewsAPI.ai free tier is rate-limited. We back off gently between calls.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from eventregistry import (
    EventRegistry,
    QueryArticlesIter,
    QueryItems,
    ReturnInfo,
    ArticleInfoFlags,
    SourceInfoFlags,
)

from config import (
    CATEGORY_KEYWORDS,
    LOOKBACK_HOURS,
    MAX_ARTICLES_PER_CATEGORY,
    MAX_KEYWORD_ALERT_ARTICLES,
    NEWSAPI_AI_KEY,
    get_monitored_sources,
)
from src.db import upsert_article, connect

log = logging.getLogger(__name__)


def _er() -> EventRegistry:
    """Build a single EventRegistry client we reuse across calls."""
    if not NEWSAPI_AI_KEY:
        raise RuntimeError("NEWSAPI_AI_KEY is not set")
    return EventRegistry(apiKey=NEWSAPI_AI_KEY, allowUseOfArchive=False)


def _return_info() -> ReturnInfo:
    """Ask Event Registry for the fields we actually need — keep payload small."""
    return ReturnInfo(
        articleInfo=ArticleInfoFlags(
            bodyLen=600,          # first ~600 chars of body is enough to summarize
            concepts=True,
            categories=False,
            links=False,
            videos=False,
            image=False,
            socialScore=False,
            duplicateList=False,
            originalArticle=False,
            storyUri=False,
        ),
        sourceInfo=SourceInfoFlags(title=True, location=False),
    )


def _extract(article: dict) -> dict:
    """Normalize an Event Registry article payload to our internal shape."""
    src = article.get("source") or {}
    return {
        "url":          article.get("url") or "",
        "source":       (src.get("title") or src.get("uri") or "unknown"),
        "title":        article.get("title") or "",
        "body":         (article.get("body") or "")[:600],
        "published_at": article.get("dateTime") or article.get("date") or "",
    }


def ingest_category(
    er: EventRegistry,
    *,
    category: str,
    keywords: List[str],
    since: datetime,
    max_items: int,
) -> List[dict]:
    """
    Run one Event Registry query for a category and return a list of article
    dicts. Filters to preferred sources and recent publication time.
    """
    source_uris = get_monitored_sources()
    q = QueryArticlesIter(
        keywords=QueryItems.OR(keywords),
        keywordsLoc="title,body",
        lang="eng",
        dateStart=since.strftime("%Y-%m-%d"),
        sourceUri=QueryItems.OR(source_uris),
        isDuplicateFilter="skipDuplicates",
        dataType=["news"],
    )

    results: List[dict] = []
    try:
        for art in q.execQuery(
            er,
            sortBy="date",
            sortByAsc=False,
            maxItems=max_items,
            returnInfo=_return_info(),
        ):
            if not art.get("url"):
                continue

            # Belt-and-suspenders time filter — Event Registry's dateStart is
            # calendar-day granularity, so we filter again by the lookback window.
            dt_str = art.get("dateTime")
            if dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    if dt < since:
                        continue
                except Exception:
                    pass

            normalized = _extract(art)
            normalized["_raw"] = art
            results.append(normalized)
    except Exception as e:
        log.warning("Event Registry query failed for category=%s: %s", category, e)

    return results


def ingest_all(db_path: str, *, verbose: bool = True) -> Dict[str, int]:
    """
    Main entry point. Iterates every category, writes articles to SQLite,
    and returns a {category: count_ingested} summary.
    """
    er = _er()
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    summary: Dict[str, int] = {}
    with connect(db_path) as conn:
        for category, keywords in CATEGORY_KEYWORDS.items():
            if not keywords:
                summary[category] = 0
                continue
            cap = (
                min(MAX_ARTICLES_PER_CATEGORY, MAX_KEYWORD_ALERT_ARTICLES)
                if category == "Keyword alerts"
                else MAX_ARTICLES_PER_CATEGORY
            )
            if verbose:
                log.info("Ingesting category=%s", category)
            articles = ingest_category(
                er,
                category=category,
                keywords=keywords,
                since=since,
                max_items=cap,
            )
            for a in articles:
                upsert_article(
                    conn,
                    url=a["url"],
                    source=a["source"],
                    title=a["title"],
                    body=a["body"],
                    published_at=a["published_at"],
                    category_hint=category,
                    raw_json=json.dumps(a.get("_raw") or {}),
                )
            conn.commit()
            summary[category] = len(articles)

            # Gentle rate-limit respect.
            time.sleep(0.5)

    return summary
