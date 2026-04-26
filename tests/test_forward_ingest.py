"""
Tests for user-forward email ingestion (no network).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import init_db
from src.forward_ingest import (
    extract_forward_content_from_rfc822_bytes,
    ingest_eml_file,
    ingest_forward_email,
    parse_eml_bytes,
    refill_forward_from_request_attachments,
    sender_allowed,
    stable_forward_url,
)


class TestForwardIngest(unittest.TestCase):
    def test_stable_url_same_message_id(self):
        u1 = stable_forward_url(
            message_id="<abc@mail>",
            from_addr="a@b.com",
            subject="S",
            body="body",
        )
        u2 = stable_forward_url(
            message_id="<abc@mail>",
            from_addr="other@b.com",
            subject="other",
            body="x",
        )
        self.assertEqual(u1, u2)

    def test_sender_allowlist(self):
        ok, _ = sender_allowed("Alice <a@firm.com>", ["a@firm.com"])
        self.assertTrue(ok)
        ok, _ = sender_allowed("Alice <a@firm.com>", ["@firm.com"])
        self.assertTrue(ok)
        ok, _ = sender_allowed("Bob <b@evil.com>", ["@firm.com"])
        self.assertFalse(ok)

    def test_ingest_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "t.db")
            init_db(db)
            r = ingest_forward_email(
                db_path=db,
                from_header="Tester <t@example.com>",
                subject="Note",
                text="Something important about QSR comps.",
                html=None,
                headers_raw="Message-ID: <mid@example.com>\n",
                allowlist=["@example.com"],
            )
            self.assertTrue(r.get("ok"))
            conn = sqlite3.connect(db)
            cur = conn.execute("SELECT count(*) FROM articles WHERE url LIKE 'https://forward.invalid/%'")
            self.assertEqual(cur.fetchone()[0], 1)
            conn.close()

    def test_extract_inner_eml_attachment_bytes(self):
        raw = (
            b"From: inner@paper.com\r\n"
            b"To: a@b.com\r\n"
            b"Subject: Headline for pipeline\r\n"
            b"Message-ID: <inner-id@message>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"This is the real article text for the daily brief, well over forty chars required.\r\n"
        )
        r = extract_forward_content_from_rfc822_bytes(
            raw, filename="receipt.eml", content_type="application/octet-stream", strict=True
        )
        self.assertIsNotNone(r)
        plain, _html, subj, mid = r
        self.assertIn("real article", (plain or ""))
        self.assertEqual(subj, "Headline for pipeline")
        self.assertEqual(mid, "inner-id@message")

    def test_refill_from_attachment_fills_empty_stripped(self):
        eml = (
            b"From: x@y.com\r\n"
            b"Subject: Inner only\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"The attached message is the only content and should be used as the body. "
            b"Padding padding padding padding.\r\n"
        )

        class _F:
            filename = "forward.eml"
            content_type = "message/rfc822"
            stream = None

            def read(self):
                return eml

            def seek(self, _n: int) -> None:
                return None

        class _Files:
            def __init__(self) -> None:
                self._m = {"attachment-1": _F()}

            def keys(self):
                return self._m.keys()

            def get(self, name: str):
                return self._m.get(name)

            def getlist(self, name: str):
                v = self.get(name)
                return [v] if v else []

        class _Req:
            files = _Files()

        t, h, subj, mid = refill_forward_from_request_attachments(
            _Req(), None, None, "Fwd: empty"
        )
        self.assertIn("only content", (t or ""))
        self.assertEqual(subj, "Inner only")
        self.assertIsNone(mid)


if __name__ == "__main__":
    unittest.main()
