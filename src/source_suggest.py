"""
News source lookup via Event Registry (NewsAPI.ai) suggestNewsSources.
"""

from __future__ import annotations

from typing import Any, List

from eventregistry import EventRegistry

from config import NEWSAPI_AI_KEY, normalize_source_uri


def suggest_news_source_prefix(prefix: str, *, max_items: int = 20) -> List[dict[str, Any]]:
    """
    Return up to max_items of {"uri", "title"} for the dashboard autocomplete.
    """
    p = (prefix or "").strip()
    if len(p) < 2 or not NEWSAPI_AI_KEY:
        return []
    er = EventRegistry(apiKey=NEWSAPI_AI_KEY, allowUseOfArchive=False)
    raw = er.suggestNewsSources(p, dataType=["news", "pr", "blog"], count=max_items, page=1)
    if not raw or not isinstance(raw, list):
        return []
    out: List[dict[str, Any]] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_uri = item.get("uri")
        if not raw_uri:
            continue
        uri = normalize_source_uri(str(raw_uri))
        if not uri or uri in seen:
            continue
        seen.add(uri)
        title = (item.get("title") or item.get("name") or uri).strip() or uri
        out.append({"uri": uri, "title": title})
    return out
