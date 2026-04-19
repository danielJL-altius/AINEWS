"""
Read/write data/ingest_settings.json (keyword lists edited from the dashboard).

Runtime merge happens in config.py at import; the daily job process reloads config
each run. The dashboard re-reads this file on every request.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

SETTINGS_FILENAME = "ingest_settings.json"


def settings_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / SETTINGS_FILENAME


def load_raw() -> Dict[str, Any]:
    p = settings_path()
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_raw(data: Dict[str, Any]) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload.setdefault("version", 1)
    fd, tmp = tempfile.mkstemp(
        dir=str(p.parent),
        prefix=".ingest_settings_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
