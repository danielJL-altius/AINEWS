#!/usr/bin/env python3
"""
Ingest a saved ``.eml`` file into the news database as a \"User forwarded\" article.

Uses the same logic as the Mailgun inbound webhook (see inbound_server.py).

Examples::

    python ingest_eml.py path/to/saved_mail.eml
    python ingest_eml.py --skip-allowlist samples/note.eml
    python ingest_eml.py --db /tmp/test.db note.eml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from config import DB_PATH, FORWARD_ALLOWED_SENDERS
from src.forward_ingest import ingest_eml_file, parse_allowlist_csv


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest a .eml file as a forwarded news article")
    p.add_argument("eml_path", help="Path to a .eml file saved from a mail client")
    p.add_argument(
        "--db",
        default=DB_PATH,
        help=f"SQLite path (default: {DB_PATH})",
    )
    p.add_argument(
        "--skip-allowlist",
        action="store_true",
        help="Ignore FORWARD_ALLOWED_SENDERS and accept any From: address (for local testing)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.skip_allowlist:
        allowlist: list[str] = []
    else:
        allowlist = parse_allowlist_csv(FORWARD_ALLOWED_SENDERS)

    out = ingest_eml_file(eml_path=args.eml_path, db_path=args.db, allowlist=allowlist)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
