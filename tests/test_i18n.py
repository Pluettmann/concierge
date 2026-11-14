"""Translations: every key used in the code exists, and the language file is consistent."""
import re
import string
from pathlib import Path

import pytest

from app import i18n

APP_DIR = Path(i18n.__file__).resolve().parent
# t("key"), t(f"prefix.{...}") and keys passed to helpers that call t() themselves.
LITERAL = re.compile(r"""\bt(?:_list)?\(\s*"([a-z0-9_.]+)"|reason\(\s*"([a-z0-9_.]+)"|badge\(\s*"([a-z0-9_.]+)\"""")
DYNAMIC = re.compile(r"""\bt\(\s*f"([a-z0-9_.]*)\{""")


def _sources() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py"))


@pytest.mark.parametrize("language", i18n.available_languages())
def test_all_used_keys_exist(language):
    catalog = i18n.catalog(language)
    source = _sources()
    used = {next(g for g in m if g) for m in LITERAL.findall(source)}
    missing = sorted(k for k in used if k not in catalog)
    assert not missing, f"missing in {language}.json: {missing}"
    prefixes = set(DYNAMIC.findall(source))
    missing_prefixes = sorted(p for p in prefixes if not any(k.startswith(p) for k in catalog))
    assert not missing_prefixes, f"no keys in {language}.json for: {missing_prefixes}"


def test_languages_have_the_same_keys():
    reference = set(i18n.catalog(i18n.DEFAULT_LANGUAGE))
    for language in i18n.available_languages():
        assert set(i18n.catalog(language)) == reference, language


def test_placeholders_are_valid():
    for value in i18n.catalog(i18n.DEFAULT_LANGUAGE).values():
        texts = value.values() if isinstance(value, dict) else [] if isinstance(value, list) else [value]
        for text in texts:
            if "{{" in text:  # mail templates use Jinja placeholders, not str.format
                continue
            list(string.Formatter().parse(text))  # raises on broken braces


def test_plural_and_formatting():
    assert i18n.t("overview.fetched", count=1) == "1 neue Mail abgerufen."
    assert i18n.t("overview.fetched", count=3) == "3 neue Mails abgerufen."
    assert i18n.format_date("2026-11-14", "date_long") == "Samstag, 14. November 2026"
    assert i18n.format_date("2026-11-14T19:05:00", "day_time") == "14.11. 19:05"


def test_missing_key_falls_back_to_key():
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_reasons_text():
    assert i18n.reasons_text('[{"key": "reason.positive", "args": {"phrase": "ich komme"}}, {"text": "alt"}]') \
        == "Zusage: „ich komme“ · alt"
    assert i18n.reasons_text("kein JSON") == "kein JSON"
