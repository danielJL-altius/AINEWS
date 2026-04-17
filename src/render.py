"""
Render the EmailContent object into HTML + plain text using Jinja templates.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Tuple

from jinja2 import Environment, FileSystemLoader

from src.generate import EmailContent


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _select_autoescape(template_name: str | None) -> bool:
    """Autoescape HTML templates; leave plain-text templates alone."""
    if template_name is None:
        return False
    return template_name.endswith(".html.j2") or template_name.endswith(".html")


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=_select_autoescape,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def render_email(
    content: EmailContent,
    *,
    today_str: str,
    generated_at: str | None = None,
    ingest_stats: dict | None = None,
) -> Tuple[str, str]:
    """
    Return (html, plain_text).

    ingest_stats — optional dict with keys:
      sources_count   : int  — number of sources monitored
      articles_scanned: int  — total articles pulled from NewsAPI
      articles_to_llm : int  — articles sent to the LLM after DB filter
      articles_surfaced: int — bullets that appeared in the final email
    """
    env = _env()
    html_tmpl = env.get_template("email.html.j2")
    txt_tmpl = env.get_template("email.txt.j2")

    ctx = {
        "subject": content.subject,
        "hero": content.hero,
        "categories": content.categories,
        "today_str": today_str,
        "generated_at": generated_at or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "ingest_stats": ingest_stats or {},
    }

    html = html_tmpl.render(**ctx)
    plain = txt_tmpl.render(**ctx)
    return html, plain
