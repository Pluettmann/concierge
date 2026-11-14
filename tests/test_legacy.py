"""Taking over data from version 1.x ("Anmeldungen" with German names)."""
import json
import os
import sqlite3

from app import config, db, i18n, legacy, templates
from app.services import accounts, events, people, registrations

# Schema of anmeldungen.db as written by version 1.3.
LEGACY_SCHEMA = """
CREATE TABLE einstellung (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE konto (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, login TEXT NOT NULL DEFAULT '',
    imap_host TEXT NOT NULL DEFAULT '', imap_port INTEGER NOT NULL DEFAULT 993,
    imap_security TEXT NOT NULL DEFAULT 'ssl', smtp_host TEXT NOT NULL DEFAULT '',
    smtp_port INTEGER NOT NULL DEFAULT 465, smtp_security TEXT NOT NULL DEFAULT 'ssl',
    ordner_eingang TEXT NOT NULL DEFAULT 'INBOX', ordner_gesendet TEXT NOT NULL DEFAULT '',
    absender_name TEXT NOT NULL DEFAULT '', signatur TEXT NOT NULL DEFAULT '',
    korrespondenz_bis TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE person (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
    vorname TEXT NOT NULL DEFAULT '', notizen TEXT NOT NULL DEFAULT '', ist_mitglied INTEGER NOT NULL DEFAULT 0,
    mitglied_quelle TEXT NOT NULL DEFAULT '', im_adressbuch INTEGER NOT NULL DEFAULT 0,
    blockiert INTEGER NOT NULL DEFAULT 0, zuerst_gesehen TEXT, ausgeblendet INTEGER NOT NULL DEFAULT 0);
CREATE TABLE veranstaltung (id INTEGER PRIMARY KEY, titel TEXT NOT NULL, datum TEXT NOT NULL,
    beginn TEXT NOT NULL DEFAULT '', ende TEXT NOT NULL DEFAULT '', ort TEXT NOT NULL DEFAULT '',
    adresse TEXT NOT NULL DEFAULT '', kartenlink TEXT NOT NULL DEFAULT '', infos TEXT NOT NULL DEFAULT '',
    newsletter_msgid TEXT NOT NULL DEFAULT '', newsletter_betreff TEXT NOT NULL DEFAULT '',
    suche_ab TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'aktiv', ics_uid TEXT NOT NULL,
    ics_sequenz INTEGER NOT NULL DEFAULT 0, erstellt TEXT NOT NULL);
CREATE TABLE mail (id INTEGER PRIMARY KEY, message_id TEXT NOT NULL UNIQUE, ordner TEXT NOT NULL,
    uid TEXT NOT NULL DEFAULT '', uidvalidity TEXT NOT NULL DEFAULT '', person_id INTEGER,
    absender_name TEXT NOT NULL DEFAULT '', betreff TEXT NOT NULL DEFAULT '', datum TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '', text_bereinigt TEXT NOT NULL DEFAULT '', in_reply_to TEXT NOT NULL DEFAULT '',
    refs TEXT NOT NULL DEFAULT '', veranstaltung_id INTEGER, vorschlag TEXT NOT NULL DEFAULT 'sonstiges',
    punkte INTEGER NOT NULL DEFAULT 0, gruende TEXT NOT NULL DEFAULT '', begleitpersonen INTEGER NOT NULL DEFAULT 0,
    erkannter_vorname TEXT NOT NULL DEFAULT '', einstufung TEXT, geprueft_am TEXT,
    beantwortet INTEGER NOT NULL DEFAULT 0, konto_id INTEGER);
CREATE TABLE anmeldung (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL, veranstaltung_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'angemeldet', begleitpersonen INTEGER NOT NULL DEFAULT 0,
    antwortstatus TEXT NOT NULL DEFAULT 'offen', mail_id INTEGER, angemeldet_am TEXT NOT NULL,
    beantwortet_am TEXT, UNIQUE (person_id, veranstaltung_id));
CREATE TABLE gesendet (id INTEGER PRIMARY KEY, veranstaltung_id INTEGER, person_id INTEGER, email TEXT NOT NULL,
    art TEXT NOT NULL, betreff TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
    zeitpunkt TEXT NOT NULL, fehler TEXT NOT NULL DEFAULT '', konto_id INTEGER);
CREATE TABLE vorlage (art TEXT PRIMARY KEY, betreff TEXT NOT NULL, html TEXT NOT NULL);
CREATE TABLE korrespondenz (email TEXT PRIMARY KEY);
"""

LEGACY_DATA = """
INSERT INTO einstellung VALUES ('schema_version', '2'), ('einrichtung_fertig', '1'), ('akzent', '#123456'),
    ('versandpause', '5'), ('tb_mitglieder', 'list:abook.sqlite:Newsletter');
INSERT INTO konto (id, email, ordner_gesendet, absender_name, signatur)
    VALUES (1, 'ich@example.org', 'Sent', 'Pascal', 'Gruß');
INSERT INTO person (id, email, vorname, name, ist_mitglied, mitglied_quelle, ausgeblendet)
    VALUES (1, 'anna@x.de', 'Anna', 'Schmidt', 1, 'manuell', 0), (2, 'shop@firma.de', '', '', 0, '', 1);
INSERT INTO veranstaltung (id, titel, datum, beginn, ort, suche_ab, status, ics_uid, ics_sequenz, erstellt)
    VALUES (1, 'Herbstabend', '2026-11-14', '19:00', 'Gemeindehaus', '2026-10-01T00:00:00', 'aktiv',
            'abc@anmeldungen', 2, '2026-10-01T09:00:00'),
           (2, 'Sommerfest', '2026-07-01', '', '', '2026-06-01T00:00:00', 'archiviert', 'def@anmeldungen', 0,
            '2026-06-01T09:00:00');
INSERT INTO mail (id, message_id, ordner, person_id, betreff, datum, text, veranstaltung_id, vorschlag, punkte,
                  gruende, einstufung, konto_id)
    VALUES (1, '<a@x>', 'INBOX', 1, 'Re: Einladung', '2026-10-05T10:00:00', 'Ich komme!', 1, 'anmeldung', 90,
            'Antwort auf den Newsletter · Zusage: „ich komme“', 'anmeldung', 1),
           (2, '<b@x>', 'INBOX', 2, 'Angebot', '2026-10-06T10:00:00', 'Rabatt', 1, 'sonstiges', 50,
            'Absender ausgeblendet · kein Bezug zur Veranstaltung erkannt', 'sonstiges', 1);
INSERT INTO anmeldung (person_id, veranstaltung_id, status, begleitpersonen, antwortstatus, mail_id, angemeldet_am)
    VALUES (1, 1, 'angemeldet', 1, 'zusage_gesendet', 1, '2026-10-05T10:00:00');
INSERT INTO gesendet (veranstaltung_id, person_id, email, art, betreff, zeitpunkt, konto_id)
    VALUES (1, 1, 'anna@x.de', 'zusage', 'Deine Anmeldung', '2026-10-06T08:00:00', 1);
INSERT INTO vorlage VALUES ('zusage', 'Hallo {{ vorname }}: {{ titel }}',
    '<p>Hallo {{ vorname }}, Name im Text bleibt Name{{ begleitung }}</p>{% if infos %}{{ infos }}{% endif %}');
INSERT INTO korrespondenz VALUES ('bert@x.de');
"""


def _legacy_db(path, schema=LEGACY_SCHEMA, data=LEGACY_DATA):
    con = sqlite3.connect(path / legacy.LEGACY_DB_NAME)
    con.executescript(schema + data)
    con.commit()
    con.close()


def test_import_maps_tables_columns_and_values(tmp_path):
    _legacy_db(tmp_path)
    legacy_bytes = (tmp_path / legacy.LEGACY_DB_NAME).read_bytes()

    assert db.exists_in(tmp_path)
    db.open_db(tmp_path)

    assert (tmp_path / db.DB_NAME).is_file()
    assert (tmp_path / legacy.LEGACY_DB_NAME).read_bytes() == legacy_bytes  # old file stays untouched
    [account] = accounts.all_accounts()
    assert (account["email"], account["sent_folder"], account["sender_name"], account["signature"]) == \
        ("ich@example.org", "Sent", "Pascal", "Gruß")

    active = events.active()
    assert (active["title"], active["start_time"], active["venue"], active["ics_sequence"]) == \
        ("Herbstabend", "19:00", "Gemeindehaus", 2)
    assert events.get(2)["status"] == events.ARCHIVED

    anna = people.get(1)
    assert (anna["first_name"], anna["last_name"], anna["is_member"], anna["member_source"]) == \
        ("Anna", "Schmidt", 1, people.SOURCE_MANUAL)
    assert people.get(2)["hidden"] == 1

    [reg] = registrations.registrations(1)
    assert (reg["status"], reg["reply_status"], reg["companions"]) == ("registered", "confirmation_sent", 1)
    sent = db.query_one("SELECT * FROM sent_mail")
    assert (sent["kind"], sent["account_id"]) == ("confirmation", 1)

    mail = db.query_one("SELECT * FROM mail WHERE id = 1")
    assert (mail["suggestion"], mail["classification"], mail["account_id"]) == ("registration", "registration", 1)
    assert i18n.reasons_text(mail["reasons"]) == "Antwort auf den Newsletter · Zusage: „ich komme“"

    # Hidden senders can be shown again after the import (their reason was recognised).
    people.hide([2], False)
    assert db.query_one("SELECT classification FROM mail WHERE id = 2")["classification"] is None

    template = templates.get("confirmation")
    assert template["subject"] == "Hallo {{ first_name }}: {{ title }}"
    assert template["html"] == ("<p>Hallo {{ first_name }}, Name im Text bleibt Name{{ companions_text }}</p>"
                                "{% if info %}{{ info }}{% endif %}")
    assert templates.get("rejection")["subject"] == "Re: {{ title }}"  # default added

    settings = db.get_settings()
    assert settings["setup_done"] == "1" and settings["accent_color"] == "#123456"
    assert settings["send_pause"] == "5" and settings["tb_member_source"] == "list:abook.sqlite:Newsletter"
    assert db.query_one("SELECT * FROM correspondent")["email"] == "bert@x.de"

    db.open_db(tmp_path)  # opening again changes nothing
    assert len(accounts.all_accounts()) == 1


def test_import_of_single_account_schema(tmp_path):
    """Version 1.0 kept the only account in the settings (schema 1)."""
    schema1 = """
        CREATE TABLE einstellung (schluessel TEXT PRIMARY KEY, wert TEXT);
        CREATE TABLE mail (id INTEGER PRIMARY KEY, message_id TEXT NOT NULL UNIQUE, ordner TEXT NOT NULL,
            datum TEXT NOT NULL, gruende TEXT NOT NULL DEFAULT '');
    """
    data1 = """
        INSERT INTO einstellung VALUES ('schema_version', '1'), ('email', 'alt@example.org'),
            ('imap_host', 'imap.example.org'), ('imap_port', '993'), ('ordner_gesendet', 'Sent'),
            ('absender_name', 'Pascal'), ('signatur', 'Gruß'), ('akzent', '#123456'), ('einrichtung_fertig', '1');
        INSERT INTO mail (message_id, ordner, datum) VALUES ('<a@x>', 'INBOX', '2026-10-01T10:00:00');
    """
    _legacy_db(tmp_path, schema1, data1)

    db.open_db(tmp_path)

    [account] = accounts.all_accounts()
    assert (account["email"], account["sender_name"], account["sent_folder"], account["imap_port"]) == \
        ("alt@example.org", "Pascal", "Sent", 993)
    assert db.query_one("SELECT account_id FROM mail")["account_id"] == account["id"]
    settings = db.get_settings()
    assert settings["accent_color"] == "#123456" and settings["setup_done"] == "1"
    assert "email" not in settings and "imap_host" not in settings


def test_changes_to_old_database_are_reported_once(tmp_path):
    _legacy_db(tmp_path)
    db.open_db(tmp_path)
    assert not legacy.changed_since_import(tmp_path)
    old = tmp_path / legacy.LEGACY_DB_NAME
    later = old.stat().st_mtime + 60
    os.utime(old, (later, later))  # version 1.x on the other system wrote to it
    assert legacy.changed_since_import(tmp_path)
    assert not legacy.changed_since_import(tmp_path)


def test_local_config_is_taken_over(tmp_path, monkeypatch):
    old_file = tmp_path / "old" / "lokal.json"
    old_file.parent.mkdir()
    old_file.write_text(json.dumps({"data_dir": "D:/Daten", "tb_profile": "C:/tb", "design": "dunkel",
                                    "lokal_fertig": True}), encoding="utf-8")
    monkeypatch.setattr(config, "LEGACY_CONFIG_FILE", old_file)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "new")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "new" / "config.json")

    assert legacy.migrate_local_config()
    assert config.load_local() == {"data_dir": "D:/Daten", "tb_profile": "C:/tb", "theme": "dark",
                                   "local_setup_done": True}
    assert not legacy.migrate_local_config()  # only once
