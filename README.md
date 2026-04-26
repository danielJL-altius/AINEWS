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

The pipeline runs every morning. Claude applies Justin's master prompt verbatim (adapted to return JSON so the email template is deterministic), using **two kinds of dedup context** (see [Uniqueness & deduplication](#uniqueness--deduplication)) plus a pre-market snapshot for the Markets section.

## Uniqueness & deduplication

The product goal is a briefing that feels **fresh day to day**: the same corporate event echoed across wires and outlets should not fill the digest as if it were multiple distinct stories, and stories already covered recently should not reappear unless there is **materially new** information.

**How it works (no separate duplicate-detection service).** Uniqueness is enforced by **prompt rules** plus **context** passed into the model:

1. **Yesterday’s email (full plain text)** — Loaded from the `sent_emails` table so Claude sees exactly what subscribers received the prior trading day (continuity and phrasing).
2. **Multi-day Article Index** — A compact, one-line-per-row fingerprint of articles already stored in SQLite: `date | source | title (truncated) | url`, covering roughly the last `DEDUP_CONTEXT_DAYS` calendar days (newest first). Built in `src/dedup_archive.py` from rows returned by `fetch_articles_for_dedup_corpus` in `src/db.py`. This index is the primary **cross-day** signal for “we already saw this story.”

**Model instructions** (`SYSTEM_PROMPT` in `src/generate.py`): treat the index as the main fingerprint; treat the same event across outlets as **one** story unless today’s article clearly adds material facts (e.g. revised guidance, new deal terms, regulatory action). If uncertain, **exclude**. Separately, the **same-day rule** restricts candidate bullets to articles whose **published date is today (ET)** so stale items are not recycled.

**Data lifecycle:** After ingest, rows in `articles` older than `ARTICLE_RETENTION_DAYS` are **pruned** so the archive stays bounded. The dedup index window should stay within retention (`DEDUP_CONTEXT_DAYS` ≤ `ARTICLE_RETENTION_DAYS`); if misconfigured, the app logs a warning. After a successful send, the run is **archived** to `sent_emails` so the next run has “yesterday’s email.”

**Tuning (environment variables, defaults in `config.py`):**

| Variable | Role |
|----------|------|
| `ARTICLE_RETENTION_DAYS` | Delete `articles` rows older than this many days (UTC). Default `30`. |
| `DEDUP_CONTEXT_DAYS` | How far back the title/URL index reaches. Default `30`. Should be ≤ retention. |
| `DEDUP_CONTEXT_MAX_ROWS` | Hard cap on rows in the index (LLM budget). Default `3000`. |
| `DEDUP_TITLE_MAX_CHARS` | Max characters per title line in the index. Default `140`. |
| `DEDUP_CONTEXT_MAX_CHARS` | Max total size of the index blob. Default `55000`. |

See `.env.example` for copy-paste names. If Claude hits token limits, lower `DEDUP_CONTEXT_MAX_CHARS` and/or `DEDUP_CONTEXT_MAX_ROWS` before shrinking the calendar window.

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

### Railway (cron + send real emails)

Railway can run the brief on a schedule in the **same** GitHub repo as a **second** service (the web app stays the dashboard; the cron service only runs `main.py`).

1. In the Railway project, **New** → **GitHub repo** (or **Empty** → connect repo) → pick this repo.
2. Set the service name (e.g. `daily-brief-cron`).
3. **Settings → Deploy → Custom Start Command:**  
   `python main.py`  
   (no `--dry-run` — that would skip subscriber delivery. This sends real Mailgun email.)
4. **Settings → Deploy → Cron Schedule** (enable Cron on this service). Use a [cron expression in **UTC**](https://crontab.guru) — Railway does not apply `TIMEZONE` to the schedule string.

   | Target | Cron (UTC) | Notes |
   |--------|------------|--------|
   | **~8:00 AM Eastern, Mon–Fri** | `0 12 * * 1-5` | Matches **8:00 AM Eastern Daylight Time** (roughly Mar–Nov). |
   | **~8:00 AM Eastern, Mon–Fri** (winter) | `0 13 * * 1-5` | Matches **8:00 AM Eastern Standard Time** (roughly Nov–Mar). |

   If you need exactly 8:00 AM America/New_York year-round, switch the schedule when DST changes, or use a self-hosted box / VM that supports `TZ=America/New_York` in crontab (see above).

5. **Variables:** give this service the **same** env as production (`ANTHROPIC_API_KEY`, `NEWSAPI_AI_KEY`, `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `DB_PATH` if you use a **volume mounted at the same path** on both the web and cron services, `FROM_EMAIL`, `TO_EMAILS`, etc.). In Railway, use **Shared Variables** or “Reference” from the dashboard service to avoid drift.

6. For SQLite, the cron job and the dashboard must use the **same** database file. Mount one **shared volume** (e.g. mount path `/data`) and set `DB_PATH=/data/news.db` on **both** the web and cron services.

7. A normal production run: **~07:00–07:20 ET** start is fine if you want a buffer before 8:00 AM; use `0 11 * * 1-5` or `0 12 * * 1-5` UTC as you prefer. The README’s 07:15 ET example maps to `15 12 * * 1-5` UTC in EDT (or `15 13` in EST) if you need that specific offset.

8. **Delivery audit:** the dashboard’s **Outbound — delivery log** lists each send attempt (UTC time, brief day, recipient, outcome). Use it to confirm the cron fired and Mailgun accepted mail. **Skipped** means the run was not in that subscriber’s preferred Eastern hour (see below).

9. **Preferred send time (Eastern):** each subscriber can pick an hour (0–23) in `America/New_York`. A recipient is only emailed when the cron runs during **that** local hour. Default is **8** (8:00–8:59 AM ET). For one shared daily run at 8 AM ET, leave everyone on 8:00. For multiple times, add cron entries for each hour you need, or set **`SEND_IGNORE_PREFERRED_HOUR=1`** so every active subscriber gets mail on every run (dropdown values are then informational). Optional: **`DEFAULT_PREFERRED_SEND_HOUR_ET=8`** for new signups.

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

**Adding/removing sources.** Defaults live in `PREFERRED_SOURCES` in `config.py` (Event Registry `sourceUri` domains, e.g. `wsj.com`). In the dashboard **Settings** page, use **Additional news sources** to search the API and add more; they are stored in `data/ingest_settings.json` under `extra_sources` and are merged in on the next ingest run.

**Adding/removing priority companies.** Edit `PRIORITY_COMPANIES`, `PRIORITY_TICKERS`, and `FLAG_NAMES_3G` in `config.py`. The 3G flag layer is deliberately conservative — it only fires when the article explicitly references 3G.

**Changing tone or style.** Edit the `SYSTEM_PROMPT` constant in `src/generate.py`. That's Justin's master prompt, adapted to return JSON.

**Debugging a bad output.** Run `python main.py --dry-run --verbose`. The `--skip-ingest` flag is useful when iterating on the prompt — it reuses articles already in the DB instead of re-hitting NewsAPI.ai.

**Dedup / uniqueness.** See [Uniqueness & deduplication](#uniqueness--deduplication).

**Paywalls.** For WSJ / FT / Bloomberg, we only get headlines and ~600 chars of body via NewsAPI.ai. That's enough to write a 1–2 sentence summary — recipients click through to read the full article using their own subscription.

**Mailgun.** Outbound uses the Mailgun Messages API (`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, optional `MAILGUN_REGION=us|eu`). Inbound forwarding uses Mailgun **Receiving → Routes** to POST to **`/webhooks/inbound-email?token=...`** on the same host as the dashboard (`INBOUND_WEBHOOK_SECRET`). Routes live in `src/inbound_routes.py` and are registered on the combined app. **Production:** `gunicorn dashboard:app` (serves UI + webhook + `GET /health`). Optional second process: `gunicorn inbound_server:app` only if you split services. Verify DNS (SPF/DKIM) for your sending domain in the Mailgun dashboard.

**Dashboard — keywords & ingest.** Run `python dashboard.py` and open **Keywords & ingest** (`/settings`). Edits are saved to `data/ingest_settings.json` (sector keyword seeds, Keyword alerts watchlist, priority companies/tickers, 3G flag names, max Keyword-alert articles). Environment variables `WATCHLIST_KEYWORDS` and `MAX_KEYWORD_ALERT_ARTICLES` still override file values when set. The subscriber page continues to control **which topics** each person receives (toggles on the same category names).

## Known limitations (v1)

- **Source coverage gap.** NewsAPI.ai covers most of Justin's list well, but Institutional Investor and Barron's are thinner. If those sources become critical, add RSS fallback in `src/ingest.py`.
- **Markets data is basic.** Yahoo Finance gives quotes, not commentary. The "Movers" field is populated by the LLM from candidate articles, not from a real movers feed.
- **No link health check.** We don't verify URLs resolve before shipping. Add a HEAD-check pass in `generate.py` if this becomes an issue.
- **Single recipient rendering.** All recipients get the same email. For per-person personalization (e.g., different priority company lists), extend `main.py` to loop per recipient.
- **Dedup is LLM-mediated.** There is no post-pass that drops duplicate URLs; quality depends on the index + prompt and model behavior.

## What to build next (v2)

1. **Per-user config.** Tomer / Justin / other analysts each get their own category list + priority companies. Multi-tenant from config files or a tiny DB.
2. **Factiva or Bloomberg Terminal integration** for premium paywalled sources.
3. **Slack digest variant** — same content, posted into a #news channel.
4. **Feedback loop.** Thumbs-up/down buttons in the email that write back to a learning table the prompt can reference.
5. **Weekly roll-up.** Monday morning bonus email covering the weekend.
