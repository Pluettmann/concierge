"""Mail templates with placeholders, wrapped in a modern HTML layout."""
from __future__ import annotations

import re
from datetime import date

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

from . import db
from .i18n import current_language, format_date, t
from .mail.parse import html_to_text

CONFIRMATION = "confirmation"
REJECTION = "rejection"
KINDS = (CONFIRMATION, REJECTION)

# Placeholders usable in templates; their descriptions are translated ("placeholder.<name>").
PLACEHOLDERS = ("first_name", "full_name", "title", "date", "date_short", "time", "venue", "address", "map_link",
                "info", "details", "companions_text", "companions")
DEFAULT_ACCENT = "#4f46e5"

_env = SandboxedEnvironment(autoescape=True)

LAYOUT = """<!doctype html>
<html lang="{{ lang }}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
<tr><td style="background:{{ accent }};padding:28px 32px;color:#ffffff;">
<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85;">{{ header }}</div>
<div style="font-size:24px;line-height:1.3;font-weight:700;margin-top:6px;">{{ title }}</div>
</td></tr>
<tr><td style="padding:28px 32px 8px;font-size:16px;line-height:1.6;">{{ content }}</td></tr>
<tr><td style="padding:8px 32px 30px;font-size:15px;line-height:1.6;color:#4b5563;">{{ signature }}</td></tr>
</table>
</td></tr></table>
</body></html>"""

PERSONAL_LAYOUT = """<!doctype html>
<html lang="{{ lang }}"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#1f2937;">
{{ content }}
<div style="margin-top:18px;color:#4b5563;">{{ signature }}</div>
</body></html>"""

DETAILS = """<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;border-radius:12px;margin:18px 0;">
<tr><td style="padding:18px 22px;font-size:15px;line-height:1.6;">
<div style="margin-bottom:12px;"><div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b7280;">{{ labels.when }}</div>
<div style="font-weight:600;">{{ date }}</div>{% if time %}<div>{{ time }}</div>{% endif %}</div>
{% if venue or address %}<div style="margin-bottom:12px;"><div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b7280;">{{ labels.where }}</div>
{% if venue %}<div style="font-weight:600;">{{ venue }}</div>{% endif %}{% if address %}<div>{{ address }}</div>{% endif %}</div>{% endif %}
{% if info %}<div style="margin-bottom:12px;"><div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#6b7280;">{{ labels.info }}</div>
<div>{{ info }}</div></div>{% endif %}
{% if map_link %}<div style="margin-top:6px;"><a href="{{ map_link }}" style="display:inline-block;background:{{ accent }};color:#ffffff;text-decoration:none;font-weight:600;padding:10px 18px;border-radius:999px;">{{ labels.route }}</a></div>{% endif %}
</td></tr></table>"""


class RenderError(Exception):
    pass


def default(kind: str) -> tuple[str, str]:
    """(subject, html) of the built-in template in the current language."""
    return t(f"template.{kind}.subject"), t(f"template.{kind}.html")


def time_range(event: dict) -> str:
    if event.get("start_time") and event.get("end_time"):
        return t("format.time_range", start=event["start_time"], end=event["end_time"])
    if event.get("start_time"):
        return t("format.time_from", start=event["start_time"])
    return ""


def companions_text(count: int) -> str:
    return t("template.companions_text", count=count) if count > 0 else ""


def _nl2br(text: str) -> Markup:
    return Markup("<br>").join(escape(text or "").splitlines())


def ensure_defaults() -> None:
    with db.connect() as con:
        for kind in KINDS:
            con.execute("INSERT OR IGNORE INTO template VALUES (?, ?, ?)", (kind, *default(kind)))


def get(kind: str) -> dict:
    stored = db.query_one("SELECT * FROM template WHERE kind = ?", (kind,))
    if stored:
        return stored
    subject, html = default(kind)
    return {"kind": kind, "subject": subject, "html": html}


def save(kind: str, subject: str, html: str) -> None:
    db.execute("INSERT INTO template VALUES (?, ?, ?) ON CONFLICT(kind) DO UPDATE SET "
               "subject = excluded.subject, html = excluded.html", (kind, subject, html))


def context(event: dict, person: dict | None, companions: int, settings: dict) -> dict:
    accent = settings.get("accent_color") or DEFAULT_ACCENT
    first_name = (person or {}).get("first_name", "")
    ctx = {
        "first_name": first_name,
        "full_name": " ".join(p for p in (first_name, (person or {}).get("last_name", "")) if p),
        "title": event.get("title", ""),
        "date": format_date(date.fromisoformat(event["date"]), "date_long") if event.get("date") else "",
        "date_short": format_date(date.fromisoformat(event["date"])) if event.get("date") else "",
        "time": time_range(event),
        "venue": event.get("venue", ""),
        "address": event.get("address", ""),
        "map_link": event.get("map_link", ""),
        "info": _nl2br(event.get("info", "")),
        "companions": companions,
        "companions_text": companions_text(companions),
        "accent": accent,
    }
    labels = {k: t(f"template.details.{k}") for k in ("when", "where", "info", "route")}
    ctx["details"] = Markup(_env.from_string(DETAILS).render(**ctx, labels=labels))
    return ctx


def _tidy(html: str) -> str:
    # "Hallo ," when the first name is missing -> "Hallo,"
    return re.sub(r"\s+([,!.])", r"\1", html)


def render(kind: str, event: dict, person: dict | None, companions: int, settings: dict,
           subject: str | None = None, html: str | None = None) -> tuple[str, str, str]:
    """Returns (subject, html, text). Optionally with a different template text (preview)."""
    template = get(kind)
    subject = template["subject"] if subject is None else subject
    html = template["html"] if html is None else html
    ctx = context(event, person, companions, settings)
    try:
        rendered_subject = _tidy(_env.from_string(subject).render(**ctx)).replace("\n", " ").strip()
        inner = _tidy(_env.from_string(html).render(**ctx))
    except TemplateError as exc:
        raise RenderError(t("template.error", error=exc)) from exc
    signature = _nl2br(settings.get("signature", ""))
    full = _env.from_string(LAYOUT).render(
        lang=current_language(), accent=ctx["accent"], title=event.get("title", ""), content=Markup(inner),
        header=t("template.header.confirmation" if kind == CONFIRMATION else "template.header.other"),
        signature=signature)
    text = html_to_text(inner) + ("\n\n" + settings["signature"] if settings.get("signature") else "")
    return rendered_subject, full, text


def render_personal(inner_html: str, settings: dict) -> tuple[str, str]:
    """Personal reply: plain HTML like a normal mail. Returns (html, text)."""
    signature = _nl2br(settings.get("signature", ""))
    full = _env.from_string(PERSONAL_LAYOUT).render(lang=current_language(), content=Markup(inner_html),
                                                    signature=signature)
    text = html_to_text(inner_html) + ("\n\n" + settings["signature"] if settings.get("signature") else "")
    return full, text
