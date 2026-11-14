"""Takes over data from version 1.x, when the app was called "Anmeldungen" and used German names.

- Database: anmeldungen.db (German table, column and value names) is copied once into a new
  concierge.db. The old file stays untouched as a fallback.
- Per-system settings: lokal.json from the old config folder becomes config.json.
- Passwords: read from the old vault entry on first use (see config.get_password).

A second system (dual boot) that still runs version 1.x keeps writing to anmeldungen.db. Changes
made there after the import are not taken over; changed_since_import() detects that.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from . import config, db

log = logging.getLogger(__name__)

LEGACY_DB_NAME = "anmeldungen.db"
IMPORTED_MTIME_SETTING = "legacy_db_mtime"

SETTING_NAMES = {
    "einrichtung_fertig": "setup_done", "tb_mitglieder": "tb_member_source", "akzent": "accent_color",
    "versandpause": "send_pause", "letzter_abruf": "last_fetch",
}
# Old table -> (new table, old column -> new column). Columns missing in old databases get defaults.
TABLES = {
    "konto": ("account", {
        "id": "id", "email": "email", "login": "login", "imap_host": "imap_host", "imap_port": "imap_port",
        "imap_security": "imap_security", "smtp_host": "smtp_host", "smtp_port": "smtp_port",
        "smtp_security": "smtp_security", "ordner_eingang": "inbox_folder", "ordner_gesendet": "sent_folder",
        "absender_name": "sender_name", "signatur": "signature",
        "korrespondenz_bis": "correspondence_synced_until", "position": "position"}),
    "person": ("person", {
        "id": "id", "email": "email", "vorname": "first_name", "name": "last_name", "notizen": "notes",
        "ist_mitglied": "is_member", "mitglied_quelle": "member_source", "im_adressbuch": "in_address_book",
        "blockiert": "blocked", "ausgeblendet": "hidden", "zuerst_gesehen": "first_seen"}),
    "veranstaltung": ("event", {
        "id": "id", "titel": "title", "datum": "date", "beginn": "start_time", "ende": "end_time",
        "ort": "venue", "adresse": "address", "kartenlink": "map_link", "infos": "info",
        "newsletter_msgid": "newsletter_message_id", "newsletter_betreff": "newsletter_subject",
        "suche_ab": "search_from", "status": "status", "ics_uid": "ics_uid", "ics_sequenz": "ics_sequence",
        "erstellt": "created_at"}),
    "mail": ("mail", {
        "id": "id", "message_id": "message_id", "ordner": "folder", "uid": "uid", "uidvalidity": "uidvalidity",
        "person_id": "person_id", "konto_id": "account_id", "absender_name": "sender_name", "betreff": "subject",
        "datum": "date", "text": "body", "text_bereinigt": "body_clean", "in_reply_to": "in_reply_to",
        "refs": "refs", "veranstaltung_id": "event_id", "vorschlag": "suggestion", "punkte": "score",
        "gruende": "reasons", "begleitpersonen": "companions", "erkannter_vorname": "detected_first_name",
        "einstufung": "classification", "geprueft_am": "reviewed_at", "beantwortet": "answered"}),
    "anmeldung": ("registration", {
        "id": "id", "person_id": "person_id", "veranstaltung_id": "event_id", "status": "status",
        "begleitpersonen": "companions", "antwortstatus": "reply_status", "mail_id": "mail_id",
        "angemeldet_am": "registered_at", "beantwortet_am": "replied_at"}),
    "gesendet": ("sent_mail", {
        "id": "id", "veranstaltung_id": "event_id", "person_id": "person_id", "konto_id": "account_id",
        "email": "email", "art": "kind", "betreff": "subject", "message_id": "message_id",
        "zeitpunkt": "sent_at", "fehler": "error"}),
    "vorlage": ("template", {"art": "kind", "betreff": "subject", "html": "html"}),
    "korrespondenz": ("correspondent", {"email": "email"}),
}
_CLASSIFICATIONS = {"anmeldung": "registration", "abmeldung": "cancellation", "sonstiges": "other",
                    "blockiert": "blocked"}
_KINDS = {"zusage": "confirmation", "absage": "rejection", "persoenlich": "personal"}
VALUES = {
    ("event", "status"): {"aktiv": "active", "archiviert": "archived"},
    ("mail", "suggestion"): _CLASSIFICATIONS,
    ("mail", "classification"): _CLASSIFICATIONS,
    ("registration", "status"): {"angemeldet": "registered", "abgemeldet": "cancelled"},
    ("registration", "reply_status"): {
        "offen": "open", "zusage_gesendet": "confirmation_sent", "absage_gesendet": "rejection_sent",
        "persoenlich_beantwortet": "personal_reply_sent"},
    ("sent_mail", "kind"): _KINDS,
    ("template", "kind"): _KINDS,
    ("person", "member_source"): {"manuell": "manual"},
}
# Version 1.x stored the account in the settings (schema 1) before multiple accounts existed.
_SCHEMA1_ACCOUNT_SETTINGS = tuple(TABLES["konto"][1])[1:-1]

PLACEHOLDERS = {
    "vorname": "first_name", "name": "full_name", "titel": "title", "datum": "date", "datum_kurz": "date_short",
    "uhrzeit": "time", "ort": "venue", "adresse": "address", "kartenlink": "map_link", "infos": "info",
    "details": "details", "begleitung": "companions_text", "begleitpersonen": "companions",
}
_JINJA_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_PLACEHOLDER = re.compile(r"\b(" + "|".join(PLACEHOLDERS) + r")\b")
_HIDDEN_REASON = "Absender ausgeblendet"


def has_database(data_dir: Path) -> bool:
    return (Path(data_dir) / LEGACY_DB_NAME).is_file()


def convert_placeholders(text: str) -> str:
    """{{ vorname }} -> {{ first_name }} inside template tags; other text stays as it is."""
    return _JINJA_TAG.sub(lambda tag: _PLACEHOLDER.sub(lambda m: PLACEHOLDERS[m.group(1)], tag.group(0)), text)


def convert_reasons(text: str) -> str:
    items = []
    for part in (text or "").split(" · "):
        if part == _HIDDEN_REASON:
            items.append({"key": "reason.sender_hidden"})
        elif part:
            items.append({"text": part})
    return json.dumps(items, ensure_ascii=False)


def _convert_value(table: str, column: str, value):
    if value is None:
        return None
    if (table, column) in VALUES:
        return VALUES[(table, column)].get(value, value)
    if (table, column) == ("mail", "reasons"):
        return convert_reasons(value)
    if table == "template" and column in ("subject", "html"):
        return convert_placeholders(value)
    return value


def _copy_table(old: sqlite3.Connection, new: sqlite3.Connection, old_table: str) -> int:
    new_table, columns = TABLES[old_table]
    present = [r[1] for r in old.execute(f"PRAGMA table_info({old_table})")]
    mapping = {o: n for o, n in columns.items() if o in present}
    rows = old.execute(f"SELECT {', '.join(mapping)} FROM {old_table}").fetchall()
    sql = (f"INSERT OR REPLACE INTO {new_table} ({', '.join(mapping.values())}) "
           f"VALUES ({', '.join('?' * len(mapping))})")
    new.executemany(sql, [tuple(_convert_value(new_table, n, row[o]) for o, n in mapping.items()) for row in rows])
    return len(rows)


def import_database(data_dir: Path) -> None:
    """Copies anmeldungen.db into a new concierge.db (written to a temporary file first)."""
    data_dir = Path(data_dir)
    source = data_dir / LEGACY_DB_NAME
    target = db.db_file(data_dir)
    tmp = target.with_name(target.name + ".import")
    tmp.unlink(missing_ok=True)
    with closing(sqlite3.connect(source)) as old, db.connect(tmp) as new:
        old.row_factory = sqlite3.Row
        new.execute("PRAGMA foreign_keys = OFF")
        new.executescript(db.SCHEMA)
        tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        settings = {r["schluessel"]: r["wert"] for r in old.execute("SELECT * FROM einstellung")} \
            if "einstellung" in tables else {}
        counts = {t: _copy_table(old, new, t) for t in TABLES if t in tables}
        if not counts.get("konto") and settings.get("email"):
            _import_schema1_account(new, settings)
        for name, value in settings.items():
            if name == "schema_version" or name in _SCHEMA1_ACCOUNT_SETTINGS:
                continue
            new.execute("INSERT OR REPLACE INTO setting VALUES (?, ?)", (SETTING_NAMES.get(name, name), value))
        new.execute("INSERT OR REPLACE INTO setting VALUES (?, ?)",
                    (IMPORTED_MTIME_SETTING, str(source.stat().st_mtime)))
    os.replace(tmp, target)
    log.info("Imported %s into %s: %s", source.name, target.name, counts)


def _import_schema1_account(new: sqlite3.Connection, settings: dict[str, str]) -> None:
    columns = TABLES["konto"][1]
    values = {columns[k]: settings.get(k, "") for k in _SCHEMA1_ACCOUNT_SETTINGS}
    values["imap_port"] = int(values["imap_port"] or 993)
    values["smtp_port"] = int(values["smtp_port"] or 465)
    account_id = new.execute(f"INSERT INTO account ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                             tuple(values.values())).lastrowid
    new.execute("UPDATE mail SET account_id = ? WHERE account_id IS NULL", (account_id,))
    new.execute("UPDATE sent_mail SET account_id = ? WHERE account_id IS NULL", (account_id,))


def changed_since_import(data_dir: Path) -> bool:
    """True once after anmeldungen.db was modified (by version 1.x) since it was imported."""
    source = Path(data_dir) / LEGACY_DB_NAME
    recorded = db.get_setting(IMPORTED_MTIME_SETTING)
    if not source.is_file() or not recorded:
        return False
    mtime = source.stat().st_mtime
    if mtime <= float(recorded) + 1:
        return False
    db.set_settings(**{IMPORTED_MTIME_SETTING: str(mtime)})
    return True


def migrate_local_config() -> bool:
    """Takes over lokal.json from version 1.x once. Returns True if it did."""
    old_file = config.LEGACY_CONFIG_FILE
    if config.CONFIG_FILE.exists() or not old_file or not old_file.is_file():
        return False
    try:
        old = json.loads(old_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    config.save_local(data_dir=old.get("data_dir", ""), tb_profile=old.get("tb_profile", ""),
                      theme={"hell": "light", "dunkel": "dark"}.get(old.get("design"), "auto"),
                      local_setup_done=bool(old.get("lokal_fertig")))
    log.info("Took over settings from %s", old_file)
    return True
