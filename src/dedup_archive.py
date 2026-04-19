"""
Build a compact text index of stored articles for multi-day LLM dedup context.
"""

from __future__ import annotations

from typing import List, Sequence

from sqlite3 import Row


def build_dedup_corpus_text(
    rows: Sequence[Row],
    *,
    title_max_chars: int = 140,
    max_total_chars: int = 55_000,
) -> str:
    """
    One line per article: date | source | title (truncated) | url.
    Stops when max_total_chars would be exceeded.
    """
    lines: List[str] = []
    size = 0
    for i, r in enumerate(rows):
        title = (str(r["title"]) if r["title"] is not None else "").replace("\n", " ").strip()
        if len(title) > title_max_chars:
            title = title[: title_max_chars - 1] + "…"
        pub = str(r["published_at"] or "")
        day = pub[:10] if len(pub) >= 10 else pub
        src = str(r["source"] or "")[:50]
        url = str(r["url"] or "")
        line = f"{day} | {src} | {title} | {url}"
        if size + len(line) + 1 > max_total_chars:
            rem = len(rows) - i
            if rem > 0:
                lines.append(f"[… {rem} additional headline(s) omitted (size cap) …]")
            break
        lines.append(line)
        size += len(line) + 1

    if not lines:
        return "(no articles indexed in this window yet — first runs will populate the archive)"
    return "\n".join(lines)
