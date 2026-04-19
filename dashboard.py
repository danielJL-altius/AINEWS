"""
Altius Intelligence Console — subscriber management dashboard.

Run with:
    python dashboard.py

Then open http://localhost:5050 in your browser.

The same app serves Mailgun inbound webhooks:
    GET  /health
    POST /webhooks/inbound-email?token=...

Production: gunicorn dashboard:app
Password is set via DASHBOARD_PASSWORD in .env (default: altius2026).
"""

from __future__ import annotations

import sys
import os

# Ensure the project root is on the path when running as a script.
sys.path.insert(0, os.path.dirname(__file__))

from functools import wraps
from typing import List

from flask import Flask, flash, redirect, render_template, request, session, url_for

from config import (
    CATEGORIES,
    DASHBOARD_PASSWORD,
    DASHBOARD_SECRET_KEY,
    DB_PATH,
    PREFERRED_SOURCES,
    SECTOR_INGEST_CATEGORIES,
    get_ingest_dashboard_context,
)
from src.ingest_settings_io import save_raw
from src.inbound_routes import inbound_bp
from src.db import (
    connect,
    create_subscriber,
    delete_subscriber,
    get_all_subscribers,
    get_subscriber,
    get_subscriber_prefs,
    init_db,
    set_subscriber_prefs,
    upsert_subscriber,
)

app = Flask(__name__, template_folder="templates")
app.secret_key = DASHBOARD_SECRET_KEY
app.register_blueprint(inbound_bp)

# Ensure DB schema exists whether started via gunicorn or directly.
init_db(DB_PATH)


# =========================================================================
# AUTH
# =========================================================================

def _parse_keyword_lines(text: str) -> List[str]:
    out: List[str] = []
    for line in (text or "").splitlines():
        for part in line.split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out


def _parse_ticker_line(text: str) -> List[str]:
    return [p.strip() for p in (text or "").replace("\n", ",").split(",") if p.strip()]


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        sector: dict = {}
        for i, cat in enumerate(SECTOR_INGEST_CATEGORIES):
            raw = request.form.get(f"sector_kw_{i}", "")
            sector[cat] = _parse_keyword_lines(raw)
        try:
            mx = int(request.form.get("max_keyword_alert_articles", "32").strip())
            mx = max(1, min(mx, 200))
        except ValueError:
            mx = 32
        payload = {
            "version": 1,
            "sector_keywords": sector,
            "watchlist_keywords": _parse_keyword_lines(request.form.get("watchlist_keywords", "")),
            "priority_companies": _parse_keyword_lines(request.form.get("priority_companies", "")),
            "priority_tickers": _parse_ticker_line(request.form.get("priority_tickers", "")),
            "flag_names_3g": _parse_keyword_lines(request.form.get("flag_names_3g", "")),
            "max_keyword_alert_articles": mx,
        }
        save_raw(payload)
        flash("Ingest settings saved to data/ingest_settings.json — restart is not required for the next scheduled job.")
        return redirect(url_for("settings"))

    ctx = get_ingest_dashboard_context()
    ctx["categories_sector"] = SECTOR_INGEST_CATEGORIES
    return render_template("dashboard/settings.html", **ctx)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("index"))
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        flash("Incorrect password — please try again.")
    return render_template("dashboard/login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================================
# SUBSCRIBER LIST
# =========================================================================

@app.route("/")
@login_required
def index():
    with connect(DB_PATH) as conn:
        subscribers = get_all_subscribers(conn)
    return render_template("dashboard/subscribers.html", subscribers=subscribers)


@app.route("/add", methods=["POST"])
@login_required
def add_subscriber():
    email = request.form.get("email", "").strip().lower()
    name = request.form.get("name", "").strip()
    if not email:
        flash("Email address is required.")
        return redirect(url_for("index"))
    with connect(DB_PATH) as conn:
        existing = get_subscriber(conn, email)
        if existing:
            flash(f"{email} is already a subscriber.")
        else:
            create_subscriber(conn, email=email, name=name)
            conn.commit()
            flash(f"Added {name or email} to the subscriber list.")
    return redirect(url_for("index"))


# =========================================================================
# SUBSCRIBER DETAIL / PREFS
# =========================================================================

@app.route("/subscriber/<path:email>")
@login_required
def subscriber(email: str):
    with connect(DB_PATH) as conn:
        sub = get_subscriber(conn, email)
        if not sub:
            flash(f"Subscriber {email} not found.")
            return redirect(url_for("index"))
        prefs = get_subscriber_prefs(conn, email)
    return render_template(
        "dashboard/subscriber.html",
        sub=sub,
        prefs=prefs,
        categories=CATEGORIES,
        sources=PREFERRED_SOURCES,
    )


@app.route("/subscriber/<path:email>/save", methods=["POST"])
@login_required
def save_prefs(email: str):
    topics = request.form.getlist("topics")
    sources = request.form.getlist("sources")
    with connect(DB_PATH) as conn:
        sub = get_subscriber(conn, email)
        if not sub:
            return redirect(url_for("index"))
        set_subscriber_prefs(conn, email, topics=topics, sources=sources)
        conn.commit()
    flash("Preferences saved.")
    return redirect(url_for("subscriber", email=email))


@app.route("/subscriber/<path:email>/toggle", methods=["POST"])
@login_required
def toggle_subscriber(email: str):
    with connect(DB_PATH) as conn:
        sub = get_subscriber(conn, email)
        if sub:
            new_active = 0 if sub["active"] else 1
            upsert_subscriber(conn, email=email, name=sub["name"], active=new_active)
            conn.commit()
            state = "activated" if new_active else "paused"
            flash(f"{sub['name'] or email} {state}.")
    return redirect(url_for("index"))


@app.route("/subscriber/<path:email>/delete", methods=["POST"])
@login_required
def delete_sub(email: str):
    with connect(DB_PATH) as conn:
        sub = get_subscriber(conn, email)
        if sub:
            label = sub["name"] or email
            delete_subscriber(conn, email)
            conn.commit()
            flash(f"Removed {label} from the subscriber list.")
    return redirect(url_for("index"))


# =========================================================================
# ENTRY POINT
# =========================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    print(f"Starting Altius Intelligence Console on http://localhost:{port}")
    print(f"Password: {DASHBOARD_PASSWORD}")
    app.run(debug=False, port=port, host="0.0.0.0")
