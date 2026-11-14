"""SQLite database in the shared data folder.

Every operation opens its own short-lived connection. That is thread-safe (fetching and sending run
in background threads) and leaves the file in a clean state when switching operating systems.
Deliberately no WAL mode: WAL creates extra files that can cause trouble on NTFS/exFAT after a
hard reboot.
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

DB_NAME = "concierge.db"
BACKUP_DIR = "backups"
BACKUP_PREFIX = "concierge-"
BACKUPS_KEEP = 10
SCHEMA_VERSION = 1

ACCOUNT_FIELDS = ("email", "login", "imap_host", "imap_port", "imap_security", "smtp_host", "smtp_port",
                  "smtp_security", "inbox_folder", "sent_folder", "sender_name", "signature",
                  "correspondence_synced_until")

_db_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting (
    name TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    login TEXT NOT NULL DEFAULT '',
    imap_host TEXT NOT NULL DEFAULT '',
    imap_port INTEGER NOT NULL DEFAULT 993,
    imap_security TEXT NOT NULL DEFAULT 'ssl',
    smtp_host TEXT NOT NULL DEFAULT '',
    smtp_port INTEGER NOT NULL DEFAULT 465,
    smtp_security TEXT NOT NULL DEFAULT 'ssl',
    inbox_folder TEXT NOT NULL DEFAULT 'INBOX',
    sent_folder TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    correspondence_synced_until TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    is_member INTEGER NOT NULL DEFAULT 0,
    member_source TEXT NOT NULL DEFAULT '',
    in_address_book INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT
);

CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL DEFAULT '',
    end_time TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    map_link TEXT NOT NULL DEFAULT '',
    info TEXT NOT NULL DEFAULT '',
    newsletter_message_id TEXT NOT NULL DEFAULT '',
    newsletter_subject TEXT NOT NULL DEFAULT '',
    search_from TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    ics_uid TEXT NOT NULL,
    ics_sequence INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    folder TEXT NOT NULL,
    uid TEXT NOT NULL DEFAULT '',
    uidvalidity TEXT NOT NULL DEFAULT '',
    person_id INTEGER REFERENCES person(id),
    account_id INTEGER REFERENCES account(id),
    sender_name TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    body_clean TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    refs TEXT NOT NULL DEFAULT '',
    event_id INTEGER REFERENCES event(id),
    suggestion TEXT NOT NULL DEFAULT 'other',
    score INTEGER NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]',
    companions INTEGER NOT NULL DEFAULT 0,
    detected_first_name TEXT NOT NULL DEFAULT '',
    classification TEXT,
    reviewed_at TEXT,
    answered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS registration (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES person(id),
    event_id INTEGER NOT NULL REFERENCES event(id),
    status TEXT NOT NULL DEFAULT 'registered',
    companions INTEGER NOT NULL DEFAULT 0,
    reply_status TEXT NOT NULL DEFAULT 'open',
    mail_id INTEGER REFERENCES mail(id),
    registered_at TEXT NOT NULL,
    replied_at TEXT,
    UNIQUE (person_id, event_id)
);

CREATE TABLE IF NOT EXISTS sent_mail (
    id INTEGER PRIMARY KEY,
    event_id INTEGER REFERENCES event(id),
    person_id INTEGER REFERENCES person(id),
    account_id INTEGER REFERENCES account(id),
    email TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS template (
    kind TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    html TEXT NOT NULL
);

-- Every address the mailboxes have ever written to.
CREATE TABLE IF NOT EXISTS correspondent (
    email TEXT PRIMARY KEY
);

CREATE INDEX IF NOT EXISTS mail_event ON mail(event_id, classification);
CREATE INDEX IF NOT EXISTS mail_person ON mail(person_id);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db_file(data_dir: Path) -> Path:
    return Path(data_dir) / DB_NAME


def exists_in(data_dir: Path) -> bool:
    from . import legacy
    return db_file(data_dir).is_file() or legacy.has_database(data_dir)


def is_open() -> bool:
    return _db_path is not None


def open_db(data_dir: Path) -> None:
    """Creates or updates the database; takes over an old anmeldungen.db on first use."""
    global _db_path
    from . import legacy, templates
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if not db_file(data_dir).is_file() and legacy.has_database(data_dir):
        legacy.import_database(data_dir)
    _db_path = db_file(data_dir)
    with connect() as con:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO setting VALUES ('schema_version', ?) "
                    "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (str(SCHEMA_VERSION),))
    templates.ensure_defaults()


@contextmanager
def connect(path: Path | None = None):
    path = path or _db_path
    if path is None:
        raise RuntimeError("Database is not open yet")
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = DELETE")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def query(sql: str, params=()) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def query_one(sql: str, params=()) -> dict | None:
    with connect() as con:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None


def execute(sql: str, params=()) -> int:
    """Runs a change and returns the id of the new row."""
    with connect() as con:
        return con.execute(sql, params).lastrowid


# --- Shared settings --------------------------------------------------------------------

def get_setting(name: str, default: str = "") -> str:
    row = query_one("SELECT value FROM setting WHERE name = ?", (name,))
    return row["value"] if row and row["value"] is not None else default


def get_settings() -> dict[str, str]:
    return {r["name"]: r["value"] for r in query("SELECT * FROM setting")}


def set_settings(**values) -> None:
    with connect() as con:
        con.executemany(
            "INSERT INTO setting VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            [(k, "" if v is None else str(v)) for k, v in values.items()],
        )


# --- Backups ------------------------------------------------------------------------------

def backup(data_dir: Path) -> Path | None:
    src = db_file(data_dir)
    if not src.is_file():
        return None
    target_dir = Path(data_dir) / BACKUP_DIR
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f"{BACKUP_PREFIX}{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(src)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
    for old in backups(data_dir)[BACKUPS_KEEP:]:
        old.unlink(missing_ok=True)
    return target


def backups(data_dir: Path) -> list[Path]:
    """Backups of this database, newest first."""
    return sorted((Path(data_dir) / BACKUP_DIR).glob(f"{BACKUP_PREFIX}*.db"), reverse=True)


def backup_time(path: Path) -> datetime:
    return datetime.strptime(path.stem.removeprefix(BACKUP_PREFIX), "%Y%m%d-%H%M%S")


def restore_backup(data_dir: Path, backup_file: Path) -> None:
    shutil.copy2(backup_file, db_file(data_dir))
