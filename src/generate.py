"""
LLM generation — takes raw articles + prior email context and produces
the structured Daily News Email using Justin's master prompt.

The LLM returns a JSON object that we then render into HTML/plain text.
Using JSON instead of free-form text makes the email template deterministic
and easier to QA.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from anthropic import Anthropic

from config import (
    ANTHROPIC_API_KEY,
    CATEGORIES,
    DEDUP_CONTEXT_DAYS,
    FLAG_NAMES_3G,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    PRIORITY_COMPANIES,
    PRIORITY_TICKERS,
    WATCHLIST_KEYWORDS,
)

log = logging.getLogger(__name__)


# =========================================================================
# JUSTIN'S MASTER PROMPT — adapted to emit structured JSON
# =========================================================================

SYSTEM_PROMPT = f"""You are generating the Daily News Email for an investment professional at 3G Capital.

Follow these rules exactly. Output MUST be a single JSON object, no prose outside the JSON.

=============================================================
STRUCTURE — EXACT ORDER, DO NOT REORDER, DO NOT ADD CATEGORIES
=============================================================
Categories (in order):
1. QSR (Quick-Service Restaurant)
2. Housing / Repair & Remodel (R&R)
3. Footwear / Apparel
4. Private Equity / Investing
5. Technology
6. Keyword alerts (watchlist mentions — firm names, partners, etc.; see rules below)
7. Markets

=============================================================
CONTENT RULES
=============================================================
- Use ONLY the candidate articles provided in the user message. Do not invent articles, URLs, or quotes.
- Candidates tagged \"User forwarded\" are email the user forwarded into this system; use the snippet as the article text and keep the URL exactly as given (it may be a non-web identifier).
- KEYWORD ALERTS — Use ONLY candidates whose category line shows (Keyword alerts). Those articles were retrieved because the headline/body matched the firm's configured watchlist terms listed in the user message. Do not move sector stories into this section unless they also appear with (Keyword alerts). If a (Keyword alerts) story is redundant with a bullet already used above, include it at most once (prefer the sector category) and omit the duplicate from Keyword alerts.
- Maximum 8 bullets per category (Markets uses market_snapshot, not bullets).
- Each bullet is a 1–2 sentence business-prose summary of a single news item.
- One idea per bullet. No italics. No editorializing. No opinion pieces.
- For EVERY bullet (except Markets), include the source name and article URL exactly as provided — never fabricate.
- If a category has fewer than 3 same-day stories, leave it sparse — do not pad.
- If a category has zero qualifying stories, include the category with an empty bullets list.

=============================================================
PRIORITIZATION
=============================================================
Elevate material news involving these PRIORITY COMPANIES (and their direct peers/comparables):
{", ".join(PRIORITY_COMPANIES)}   (tickers: {", ".join(PRIORITY_TICKERS)})

Focus on impact to demand, pricing/margins, competitive positioning, valuation, capital allocation, M&A.

3G CAPITAL FLAG LAYER — include ONLY when the article explicitly references an affiliation with 3G Capital,
or the named individual is discussed in the context of their role at 3G or a 3G portfolio company:
{", ".join(FLAG_NAMES_3G)}

Do NOT include mentions of these names based on name alone.

=============================================================
DEDUP / REPEAT RULES
=============================================================
You receive (1) YESTERDAY'S EMAIL (full plain text) for continuity, and (2) a MULTI-DAY ARTICLE INDEX — one line per
stored article over roughly the last {DEDUP_CONTEXT_DAYS} calendar days (date, source, short title, URL; not full text).
Use the INDEX as the primary cross-day fingerprint: treat the same corporate event echoed across outlets, reprints,
or wire pickups as ONE story unless today's candidate clearly adds material facts (earnings revision, new deal terms,
updated guidance, regulatory action, management/board change, financing tranche, etc.). Do NOT repeat the same story
unless there is such a material new development. When uncertain, exclude.

=============================================================
SAME-DAY RULE
=============================================================
Only include articles published on TODAY's calendar date (ET). Exclude prior-day items even if still relevant.
Every candidate article is tagged with a published_at timestamp — use it.

=============================================================
MARKETS SECTION
=============================================================
Provide a concise pre-market U.S. snapshot covering (when data available):
- U.S. equity futures (S&P 500, Nasdaq-100, Dow)
- Notable movers
- Commodities (oil, gold)
- Treasury yields (10Y, 2Y)
- FX (if relevant — DXY, EUR/USD)
- Crypto (if relevant — BTC, ETH)

No sources or links in Markets. Keep it tight and actionable.
The markets data for today is provided in the user message — use it verbatim or lightly reworded.

=============================================================
OUTPUT FORMAT (STRICT JSON)
=============================================================
{{
  "subject": "string — e.g. 'Daily News Brief — Apr 17, 2026'",
  "hero": "1–2 sentence top-of-page teaser summarizing THE most important story of the day (used at top of email)",
  "categories": [
    {{
      "name": "QSR",
      "bullets": [
        {{
          "text": "Summary sentence.",
          "implication": "1-sentence investment implication — what does this mean for demand, margins, positioning, or valuation? Be specific and direct. Omit if the story has no clear investment angle.",
          "source": "Bloomberg",
          "url": "https://..."
        }},
        ...
      ]
    }},
    {{"name": "Housing / Repair & Remodel (R&R)", "bullets": [...]}},
    {{"name": "Footwear / Apparel", "bullets": [...]}},
    {{"name": "Private Equity / Investing", "bullets": [...]}},
    {{"name": "Technology", "bullets": [...]}},
    {{
      "name": "Keyword alerts",
      "bullets": [
        {{
          "text": "Summary — emphasize why the watchlist term appears (deal, quote, regulatory mention, etc.).",
          "implication": "optional",
          "source": "Bloomberg",
          "url": "https://..."
        }}
      ]
    }},
    {{
      "name": "Markets",
      "market_snapshot": {{
        "equity_futures": "string",
        "movers": "string",
        "commodities": "string",
        "yields": "string",
        "fx": "string",
        "crypto": "string"
      }}
    }}
  ]
}}

Return ONLY the JSON object. No commentary before or after.
"""


# =========================================================================
# DATA CLASSES FOR TYPED OUTPUT
# =========================================================================

@dataclass
class Bullet:
    text: str
    implication: str = ""
    source: str = ""
    url: str = ""


@dataclass
class CategorySection:
    name: str
    bullets: List[Bullet] = field(default_factory=list)
    market_snapshot: Optional[Dict[str, str]] = None


@dataclass
class EmailContent:
    subject: str
    hero: str
    categories: List[CategorySection]


# =========================================================================
# LLM CALL
# =========================================================================

def _format_articles_for_llm(articles: List[Dict]) -> str:
    """
    Render the candidate article list as a compact block the LLM can read.
    Keeping it as plain text (not JSON) makes the prompt more token-efficient.
    """
    lines = []
    for i, a in enumerate(articles, start=1):
        lines.append(
            f"[{i}] ({a.get('category_hint','?')}) {a.get('published_at','')} — "
            f"{a.get('source','?')}: {a.get('title','')}\n"
            f"    URL: {a.get('url','')}\n"
            f"    Snippet: {(a.get('body') or '')[:400]}"
        )
    return "\n\n".join(lines) if lines else "(no articles available)"


def _format_prior_email(prior_plain: Optional[str]) -> str:
    if not prior_plain:
        return "(no prior email on file)"
    # Trim to keep tokens reasonable.
    return prior_plain[:6000]


def _format_markets(markets: Dict[str, str]) -> str:
    if not markets:
        return "(no pre-market data available)"
    return "\n".join(f"{k}: {v}" for k, v in markets.items() if v)


def generate_email_content(
    *,
    articles: List[Dict],
    prior_email_plain: Optional[str],
    markets: Dict[str, str],
    today_str: str,
    dedup_corpus_plain: str = "",
) -> EmailContent:
    """
    Call Claude with Justin's master prompt. Returns a typed EmailContent.

    ``dedup_corpus_plain`` is a compact multi-day title/URL index for cross-day duplicate detection.
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    wl = ", ".join(WATCHLIST_KEYWORDS) if WATCHLIST_KEYWORDS else "(none — Keyword alerts section only uses (Keyword alerts) candidates if any)"
    dedup_block = dedup_corpus_plain.strip() if dedup_corpus_plain else "(empty)"
    user_message = f"""TODAY'S DATE (ET): {today_str}

=== CONFIGURED WATCHLIST TERMS (Keyword alerts ingestion) ===
{wl}

=== CANDIDATE ARTICLES (last 24h) ===
{_format_articles_for_llm(articles)}

=== MULTI-DAY ARTICLE INDEX (approx. last {DEDUP_CONTEXT_DAYS} days — stored titles/URLs for dedup; not full text) ===
{dedup_block}

=== YESTERDAY'S EMAIL (for dedup reference) ===
{_format_prior_email(prior_email_plain)}

=== PRE-MARKET DATA ===
{_format_markets(markets)}

Generate today's Daily News Email as structured JSON per the system prompt."""

    log.info("Calling LLM with %d candidate articles", len(articles))
    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()
    return _parse_llm_output(raw)


def _parse_llm_output(raw: str) -> EmailContent:
    """
    Parse the JSON object returned by the LLM. Handles the common case where
    the model wraps its JSON in ```json``` fences despite instructions.
    """
    # Strip markdown fences if present.
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Last-ditch: extract the first {...} balanced block.
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"LLM returned non-JSON output: {e}") from e
        payload = json.loads(match.group(0))

    categories: List[CategorySection] = []
    for cat in payload.get("categories", []):
        name = cat.get("name", "")
        if name == "Markets":
            categories.append(
                CategorySection(
                    name=name,
                    market_snapshot=cat.get("market_snapshot") or {},
                )
            )
        else:
            bullets = [
                Bullet(
                    text=b.get("text", "").strip(),
                    implication=b.get("implication", "").strip(),
                    source=b.get("source", "").strip(),
                    url=b.get("url", "").strip(),
                )
                for b in cat.get("bullets", [])
                if (b.get("text") or "").strip()
            ]
            categories.append(CategorySection(name=name, bullets=bullets))

    # Ensure every expected category is present and in the right order.
    by_name = {c.name: c for c in categories}
    ordered: List[CategorySection] = []
    for canonical in CATEGORIES:
        if canonical in by_name:
            ordered.append(by_name[canonical])
        else:
            ordered.append(
                CategorySection(
                    name=canonical,
                    bullets=[] if canonical != "Markets" else None,
                    market_snapshot={} if canonical == "Markets" else None,
                )
            )

    return EmailContent(
        subject=payload.get("subject", "Daily News Brief"),
        hero=payload.get("hero", ""),
        categories=ordered,
    )
