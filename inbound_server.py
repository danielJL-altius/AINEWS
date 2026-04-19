"""
HTTP endpoint for SendGrid Inbound Parse (or compatible multipart POST).

Configure SendGrid: Settings → Inbound Parse → hostname → POST URL:
  https://your-host/webhooks/inbound-email?token=YOUR_SECRET

Set INBOUND_WEBHOOK_SECRET in .env to match YOUR_SECRET.

Run locally:
  python inbound_server.py

Production (example):
  gunicorn -b 0.0.0.0:8080 inbound_server:app
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, abort, jsonify, request

from config import DB_PATH, INBOUND_WEBHOOK_SECRET, FORWARD_ALLOWED_SENDERS
from src.forward_ingest import ingest_forward_email, parse_allowlist_csv

log = logging.getLogger(__name__)

app = Flask(__name__)


def _check_token() -> None:
    secret = (INBOUND_WEBHOOK_SECRET or "").strip()
    if not secret:
        log.error("INBOUND_WEBHOOK_SECRET is not set; refusing inbound email")
        abort(503)
    token = request.args.get("token", "") or request.headers.get("X-Webhook-Token", "")
    if token != secret:
        abort(401)


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/webhooks/inbound-email")
def inbound_email():
    """
    SendGrid Inbound Parse posts multipart/form-data with at least:
      from, to, subject, text, html, headers (sometimes), envelope, etc.
    """
    _check_token()

    from_hdr = request.form.get("from", "") or ""
    subject = request.form.get("subject", "") or ""
    text = request.form.get("text")
    html = request.form.get("html")
    headers = request.form.get("headers")

    allowlist = parse_allowlist_csv(FORWARD_ALLOWED_SENDERS)
    result = ingest_forward_email(
        db_path=DB_PATH,
        from_header=from_hdr,
        subject=subject,
        text=text,
        html=html,
        headers_raw=headers,
        allowlist=allowlist,
    )
    if not result.get("ok"):
        log.info("Inbound not stored: %s", result.get("error"))
    # Always 200 after auth so SendGrid stops retrying; body carries ok/error.
    return jsonify(result), 200


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    port = int(os.getenv("INBOUND_PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
