"""Translations: every user-facing text lives in app/locales/<language>.json.

Keys are flat dotted names such as "inbox.title". A value is either
- a string with str.format placeholders ("{count} new mails"),
- an object with "one"/"other" forms, picked by the `count` argument, or
- a list (e.g. month names), read with t_list().
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "de"

log = logging.getLogger(__name__)
_language = DEFAULT_LANGUAGE


def available_languages() -> list[str]:
    return sorted(p.stem for p in LOCALES_DIR.glob("*.json"))


@cache
def catalog(language: str) -> dict[str, Any]:
    return json.loads((LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8"))


def set_language(language: str) -> None:
    global _language
    _language = language if language in available_languages() else DEFAULT_LANGUAGE


def current_language() -> str:
    return _language


def _lookup(key: str) -> Any:
    value = catalog(_language).get(key)
    if value is None and _language != DEFAULT_LANGUAGE:
        value = catalog(DEFAULT_LANGUAGE).get(key)
    return value


def t(key: str, /, **kwargs: Any) -> str:
    """Translated text for `key`, with `kwargs` filled into its placeholders."""
    value = _lookup(key)
    if value is None:
        log.warning("Missing translation: %s", key)
        return key
    if isinstance(value, dict):
        value = value["one"] if kwargs.get("count") == 1 and "one" in value else value["other"]
    if not isinstance(value, str):
        raise TypeError(f"Translation {key!r} is not a text; use t_list()")
    return value.format(**kwargs) if kwargs else value


def t_list(key: str) -> list[str]:
    value = _lookup(key)
    if not isinstance(value, list):
        raise TypeError(f"Translation {key!r} is not a list")
    return value


def format_date(value: date | datetime | str, style: str = "date") -> str:
    """Formats a date or ISO string. Styles: date, date_short, datetime, day_time, time, date_long."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if style == "date_long":
        return t("format.date_long", weekday=t_list("format.weekdays")[value.weekday()], day=value.day,
                 month=t_list("format.months")[value.month - 1], year=value.year)
    return value.strftime(t(f"format.{style}"))


def reasons_text(raw: str) -> str:
    """Renders the classification reasons stored with a mail (a JSON list, see classifier.Reason)."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return raw
    parts = []
    for item in items:
        if "text" in item:  # free text imported from an old database
            parts.append(item["text"])
        else:
            parts.append(t(item["key"], **item.get("args", {})))
    return " · ".join(parts)
