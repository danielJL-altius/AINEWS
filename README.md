# Daily News Email — MVP

Generates the Daily News Brief described in Justin Fox's (3G Capital) master prompt and delivers it at 8:00 AM ET every morning.

## How it works

```
                       ┌──────────────┐
                       │ NewsAPI.ai   │  (Event Registry)
                       │ last 24h     │
                       └──────┬───────┘
                              │
                              ▼
┌───────────┐         ┌──────────────┐          ┌─────────────┐
│ Market    │────────▶│  Orchestrator│─────────▶│  Claude     │
│ snapshot  │         │  (main.py)   │          │  (Sonnet)   │
└───────────┘         └──────┬───────┘          └──────┬──────┘
                              │                        │
                              ▼                        ▼
                       ┌──────────────┐         ┌─────────────┐
                       │ SQLite       │         │ Jinja HTML  │
                       │ articles +   │         │ renderer    │
                       │ sent emails  │         └──────┬──────┘
                       └──────────────┘                │
                                                       ▼
                                              ┌─────────────┐
                                              │  Mailgun    │
                                              └─────────────┘
```

The pipeline runs every morning. Claude applies Justin's master prompt verbatim (adapted to return JSON so the email template is deterministic), using yesterday's email for dedup context and a pre-market snapshot for the Markets section.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY, MAILGUN_API_KEY, MAILGUN_DOMAIN (if sending)

# 3. Preview without sending
python main.py --dry-run --verbose

# The rendered email will be written to data/preview/YYYY-MM-DD.html
# Open it in your browser to QA.

# 4. Real run
python main.py --verbose
```

## Scheduling

Cron it at 07:15 ET (gives ~45 min buffer before the 8:00 AM delivery target):

```cron
# /etc/cron.d/daily-news
TZ=America/New_York
15 7 * * * cd /opt/daily_news && /opt/daily_news/venv/bin/python main.py >> /var/log/daily_news.log 2>&1
```

Or on AWS Lambda + EventBridge with the same cron expression (UTC: `15 12 * * ? *`).

## Project layout

```
daily_news/
├── main.py                 # Orchestrator + CLI
├── inbound_server.py       # Optional: inbound-only Flask (use dashboard:app in prod)
├── config.py               # API keys, categories, priority companies
├── requirements.txt
├── .env.example
├── src/
│   ├── db.py               # SQLite schema + helpers
│   ├── ingest.py           # NewsAPI.ai fetching
│   ├── generate.py         # Master prompt + Claude call + JSON parsing
│   ├── markets.py          # Yahoo Finance snapshot
│   ├── render.py           # Jinja renderer
│   ├── deliver.py          # Mailgun + disk fallback
│   ├── inbound_routes.py   # Mailgun webhook blueprint (mounted on dashboard)
│   └── dedup_archive.py    # Format multi-day title index for LLM dedup
├── templates/
│   ├── email.html.j2       # Mobile-first HTML email
│   └── email.txt.j2        # Plain-text fallback
└── data/                   # SQLite DB + preview outputs (gitignored)
```

## Operating notes

**Adding/removing sources.** Edit `PREFERRED_SOURCES` in `config.py`. These are Event Registry domain URIs.

**Adding/removing priority companies.** Edit `PRIORITY_COMPANIES`, `PRIORITY_TICKERS`, and `FLAG_NAMES_3G` in `config.py`. The 3G flag layer is deliberately conservative — it only fires when the article explicitly references 3G.

**Changing tone or style.** Edit the `SYSTEM_PROMPT` constant in `src/generate.py`. That's Justin's master prompt, adapted to return JSON.

**Debugging a bad output.** Run `python main.py --dry-run --verbose`. The `--skip-ingest` flag is useful when iterating on the prompt — it reuses articles already in the DB instead of re-hitting NewsAPI.ai.

**Dedup behavior.** Yesterday's plain-text email is passed to Claude, plus a **compact multi-day index** (title, source, date, URL) built from `articles` for roughly the last **30 days** by default (`DEDUP_CONTEXT_DAYS`). Old article rows are **pruned** after **`ARTICLE_RETENTION_DAYS`** (default 30). Tune `DEDUP_CONTEXT_MAX_ROWS` / `DEDUP_CONTEXT_MAX_CHARS` if Claude hits token limits.

**Paywalls.** For WSJ / FT / Bloomberg, we only get headlines and ~600 chars of body via NewsAPI.ai. That's enough to write a 1–2 sentence summary — recipients click through to read the full article using their own subscription.

**Mailgun.** Outbound uses the Mailgun Messages API (`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, optional `MAILGUN_REGION=us|eu`). Inbound forwarding uses Mailgun **Receiving → Routes** to POST to **`/webhooks/inbound-email?token=...`** on the same host as the dashboard (`INBOUND_WEBHOOK_SECRET`). Routes live in `src/inbound_routes.py` and are registered on the combined app. **Production:** `gunicorn dashboard:app` (serves UI + webhook + `GET /health`). Optional second process: `gunicorn inbound_server:app` only if you split services. Verify DNS (SPF/DKIM) for your sending domain in the Mailgun dashboard.

**Dashboard — keywords & ingest.** Run `python dashboard.py` and open **Keywords & ingest** (`/settings`). Edits are saved to `data/ingest_settings.json` (sector keyword seeds, Keyword alerts watchlist, priority companies/tickers, 3G flag names, max Keyword-alert articles). Environment variables `WATCHLIST_KEYWORDS` and `MAX_KEYWORD_ALERT_ARTICLES` still override file values when set. The subscriber page continues to control **which topics** each person receives (toggles on the same category names).

## Known limitations (v1)

- **Source coverage gap.** NewsAPI.ai covers most of Justin's list well, but Institutional Investor and Barron's are thinner. If those sources become critical, add RSS fallback in `src/ingest.py`.
- **Markets data is basic.** Yahoo Finance gives quotes, not commentary. The "Movers" field is populated by the LLM from candidate articles, not from a real movers feed.
- **No link health check.** We don't verify URLs resolve before shipping. Add a HEAD-check pass in `generate.py` if this becomes an issue.
- **Single recipient rendering.** All recipients get the same email. For per-person personalization (e.g., different priority company lists), extend `main.py` to loop per recipient.

## What to build next (v2)

1. **Per-user config.** Tomer / Justin / other analysts each get their own category list + priority companies. Multi-tenant from config files or a tiny DB.
2. **Factiva or Bloomberg Terminal integration** for premium paywalled sources.
3. **Slack digest variant** — same content, posted into a #news channel.
4. **Feedback loop.** Thumbs-up/down buttons in the email that write back to a learning table the prompt can reference.
5. **Weekly roll-up.** Monday morning bonus email covering the weekend.
