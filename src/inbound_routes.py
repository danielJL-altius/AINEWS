"""
Inbound email webhook routes (Mailgun / compatible POST).

Registered on the main dashboard app and on standalone inbound_server.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from flask import Blueprint, abort, jsonify, request

from config import DB_PATH, INBOUND_WEBHOOK_SECRET, FORWARD_ALLOWED_SENDERS
from src.forward_ingest import ingest_forward_email, parse_allowlist_csv

log = logging.getLogger(__name__)

inbound_bp = Blueprint("inbound", __name__)


def _check_token() -> None:
    secret = (INBOUND_WEBHOOK_SECRET or "").strip()
    if not secret:
        log.error("INBOUND_WEBHOOK_SECRET is not set; refusing inbound email")
        abort(503)
    token = request.args.get("token", "") or request.headers.get("X-Webhook-Token", "")
    if token != secret:
        abort(401)


def _extract_inbound_parts() -> Tuple[str, str, Optional[str], Optional[str], Optional[str]]:
    """
    Normalize Mailgun and SendGrid inbound POST bodies to a common shape.

    Mailgun: from, sender, subject, stripped-text, body-plain, stripped-html,
             body-html, message-headers
    SendGrid: from, subject, text, html, headers
    """
    f = request.form
    from_hdr = (f.get("from") or f.get("From") or "").strip()
    if not from_hdr:
        from_hdr = (f.get("sender") or "").strip()

    subject = (f.get("subject") or "").strip()

    text = (
        f.get("stripped-text")
        or f.get("body-plain")
        or f.get("text")
    )
    if text is not None:
        text = text.strip() or None

    html = (
        f.get("stripped-html")
        or f.get("body-html")
        or f.get("html")
    )
    if html is not None:
        html = html.strip() or None

    headers = f.get("message-headers") or f.get("headers")

    return from_hdr, subject, text, html, headers


@inbound_bp.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@inbound_bp.post("/webhooks/inbound-email")
def inbound_email():
    _check_token()

    from_hdr, subject, text, html, headers = _extract_inbound_parts()

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
    return jsonify(result), 200
