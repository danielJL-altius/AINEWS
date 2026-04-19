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
    ingest_eml_file,
    parse_eml_bytes,
    sender_allowed,
    stable_forward_url,
    ingest_forward_email,
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


if __name__ == "__main__":
    unittest.main()
