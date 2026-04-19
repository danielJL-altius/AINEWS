"""
Ingest emails forwarded by users as synthetic \"article\" rows so they flow
through the same SQLite → LLM pipeline as NewsAPI.ai items.

Stable primary key: a https://forward.invalid/... URL derived from Message-ID
when present, else from a hash of from + subject + body.

Designed for Mailgun Routes / inbound webhooks (multipart form POST); see inbound_server.py.
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import logging
import re
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.db import connect, init_db, upsert_article

log = logging.getLogger(__name__)

FORWARD_CATEGORY = "User forwarded"
MAX_BODY_CHARS = 120_000

# Synthetic host reserved for documentation; not fetched over the network.
_FORWARD_BASE = "https://forward.invalid/msg"


def _normalize_addr(from_header: str) -> str:
    """Return lowercased bare email from a From: header value."""
    _, addr = parseaddr(from_header or "")
    return (addr or "").strip().lower()


def _extract_message_id(headers: Optional[str]) -> Optional[str]:
    if not headers:
        return None
    for line in headers.splitlines():
        m = re.match(r"(?i)^Message-ID:\s*(.+)\s*$", line.strip())
        if m:
            return m.group(1).strip().strip("<>")
    return None


def _hash_hex(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", errors="replace"))
        h.update(b"\x1e")
    return h.hexdigest()


def stable_forward_url(
    *,
    message_id: Optional[str],
    from_addr: str,
    subject: str,
    body: str,
) -> str:
    """
    Deterministic URL used as articles.url primary key. Same forward → same row
    (INSERT OR IGNORE keeps the first).
    """
    if message_id:
        key = message_id.strip()
    else:
        key = _hash_hex(from_addr, subject, body[:8000])
    slug = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"{_FORWARD_BASE}/{slug}"


def _html_to_text(html: str) -> str:
    """Best-effort strip tags without extra dependencies."""
    if not html:
        return ""
    # Remove script/style blocks
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def choose_body_text(text: Optional[str], html: Optional[str]) -> str:
    raw = (text or "").strip()
    if len(raw) < 40 and html:
        raw = _html_to_text(html)
    if not raw and html:
        raw = _html_to_text(html)
    if len(raw) > MAX_BODY_CHARS:
        raw = raw[: MAX_BODY_CHARS - 20] + "\n\n[truncated]"
    return raw


def sender_allowed(from_header: str, rules: List[str]) -> Tuple[bool, str]:
    """
    If rules is empty, allow everyone (use only with a shared webhook secret).
    Otherwise match bare emails (case-insensitive) or @domain suffixes.
    """
    addr = _normalize_addr(from_header)
    if not addr:
        return False, "missing sender address"
    if not rules:
        return True, "allowlist disabled"
    for rule in rules:
        r = rule.strip()
        if not r:
            continue
        if r.startswith("@"):
            if addr.endswith(r.lower()):
                return True, f"domain {r}"
        elif "@" in r:
            if addr == r.lower():
                return True, f"address {r}"
        else:
            # treat bare localpart or mistake — require @
            continue
    return False, "sender not in allowlist"


def parse_eml_bytes(data: bytes) -> Tuple[str, str, Optional[str], Optional[str], Optional[str]]:
    """
    Parse a raw .eml file into From, Subject, plain text, HTML, Message-ID.
    Skips attachment parts for body text; prefers first text/plain and first text/html.
    """
    msg = BytesParser(policy=policy.default).parsebytes(data)
    from_hdr = str(msg.get("From") or "")
    subj = msg.get("Subject")
    subject = str(subj) if subj is not None else ""
    mid_hdr = msg.get("Message-ID")
    message_id: Optional[str] = None
    if mid_hdr is not None:
        message_id = str(mid_hdr).strip().strip("<>")

    plain: Optional[str] = None
    html: Optional[str] = None

    if not msg.is_multipart():
        ctype = msg.get_content_type()
        try:
            if ctype == "text/plain":
                plain = msg.get_content()
            elif ctype == "text/html":
                html = msg.get_content()
        except Exception:
            pass
    else:
        for part in msg.walk():
            if part.is_multipart():
                continue
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp and part.get_filename():
                continue
            ctype = part.get_content_type()
            try:
                if ctype == "text/plain" and plain is None:
                    plain = part.get_content()
                elif ctype == "text/html" and html is None:
                    html = part.get_content()
            except Exception:
                continue

    return from_hdr, subject, plain, html, message_id


def ingest_forward_email(
    *,
    db_path: str,
    from_header: str,
    subject: str,
    text: Optional[str],
    html: Optional[str],
    headers_raw: Optional[str],
    allowlist: List[str],
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Parse a forwarded message and upsert one article row. Returns a small dict
    suitable for JSON logging/API responses.

    ``message_id`` may be passed explicitly (e.g. from parse_eml_bytes); otherwise
    it is parsed from ``headers_raw``.
    """
    ok, reason = sender_allowed(from_header, allowlist)
    if not ok:
        log.warning("Forward rejected: %s", reason)
        return {"ok": False, "error": reason}

    init_db(db_path)
    from_addr = _normalize_addr(from_header) or "unknown"
    msg_id = message_id or _extract_message_id(headers_raw)
    body = choose_body_text(text, html)
    if not body.strip():
        return {"ok": False, "error": "empty body"}

    pub = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    url = stable_forward_url(
        message_id=msg_id,
        from_addr=from_addr,
        subject=subject or "(no subject)",
        body=body,
    )
    display_source = f"Forward ({from_addr})"
    raw_payload = {
        "channel": "forward",
        "from": from_addr,
        "subject": subject,
        "message_id": msg_id,
        "url": url,
    }
    with connect(db_path) as conn:
        upsert_article(
            conn,
            url=url,
            source=display_source,
            title=(subject or "(no subject)")[:500],
            body=body,
            published_at=pub,
            category_hint=FORWARD_CATEGORY,
            raw_json=json.dumps(raw_payload, ensure_ascii=False),
        )
        conn.commit()

    log.info("Ingested forward from=%s url=%s", from_addr, url)
    return {"ok": True, "url": url, "from": from_addr}


def parse_allowlist_csv(value: str) -> List[str]:
    """Split comma-separated allowlist from env (e.g. a@x.com,@corp.com)."""
    if not value or not value.strip():
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def ingest_eml_file(
    *,
    eml_path: str,
    db_path: str,
    allowlist: List[str],
) -> Dict[str, Any]:
    """
    Read a ``.eml`` file from disk and insert it using the same rules as the
    inbound webhook (for local QA without Mailgun).
    """
    path = Path(eml_path)
    if not path.is_file():
        return {"ok": False, "error": f"not a file: {eml_path}"}
    data = path.read_bytes()
    from_hdr, subject, plain, html, mid = parse_eml_bytes(data)
    return ingest_forward_email(
        db_path=db_path,
        from_header=from_hdr,
        subject=subject,
        text=plain,
        html=html,
        headers_raw=None,
        allowlist=allowlist,
        message_id=mid,
    )
