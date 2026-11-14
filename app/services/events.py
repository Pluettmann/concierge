"""Create, change and archive events; key figures."""
from __future__ import annotations

import uuid

from .. import db

FIELDS = ("title", "date", "start_time", "end_time", "venue", "address", "map_link", "info",
          "newsletter_message_id", "newsletter_subject", "search_from")
# Changes to these fields turn the calendar entry into a new version.
ICS_FIELDS = ("title", "date", "start_time", "end_time", "venue", "address", "info")

ACTIVE = "active"
ARCHIVED = "archived"


def active() -> dict | None:
    return db.query_one("SELECT * FROM event WHERE status = 'active' ORDER BY date DESC LIMIT 1")


def get(event_id: int) -> dict | None:
    return db.query_one("SELECT * FROM event WHERE id = ?", (event_id,))


def all_events() -> list[dict]:
    return db.query(
        """SELECT e.*,
             (SELECT COUNT(*) FROM registration r WHERE r.event_id = e.id AND r.status = 'registered')
               AS registrations,
             (SELECT COALESCE(SUM(1 + r.companions), 0) FROM registration r
                WHERE r.event_id = e.id AND r.status = 'registered') AS people
           FROM event e ORDER BY e.date DESC""")


def create(data: dict, archive_others: bool = True) -> int:
    values = {k: data.get(k, "") or "" for k in FIELDS}
    if not values["search_from"]:
        values["search_from"] = db.now()
    with db.connect() as con:
        if archive_others:
            con.execute("UPDATE event SET status = 'archived' WHERE status = 'active'")
        cur = con.execute(
            f"INSERT INTO event ({', '.join(FIELDS)}, status, ics_uid, created_at) "
            f"VALUES ({', '.join('?' * len(FIELDS))}, 'active', ?, ?)",
            (*values.values(), f"{uuid.uuid4()}@concierge", db.now()))
        return cur.lastrowid


def update(event_id: int, data: dict) -> None:
    old = get(event_id)
    changes = {k: data[k] or "" for k in FIELDS if k in data}
    if not changes:
        return
    bump = any(str(old.get(k, "")) != str(changes.get(k, old.get(k, ""))) for k in ICS_FIELDS)
    sets = ", ".join(f"{k} = ?" for k in changes)
    db.execute(f"UPDATE event SET {sets}{', ics_sequence = ics_sequence + 1' if bump else ''} WHERE id = ?",
               (*changes.values(), event_id))


def archive(event_id: int) -> None:
    db.execute("UPDATE event SET status = 'archived' WHERE id = ?", (event_id,))


def reactivate(event_id: int) -> None:
    with db.connect() as con:
        con.execute("UPDATE event SET status = 'archived' WHERE status = 'active'")
        con.execute("UPDATE event SET status = 'active' WHERE id = ?", (event_id,))


def stats(event_id: int) -> dict:
    row = db.query_one(
        """SELECT
             COALESCE(SUM(r.status = 'registered'), 0) AS registered,
             COALESCE(SUM(CASE WHEN r.status = 'registered' THEN 1 + r.companions END), 0) AS people,
             COALESCE(SUM(r.status = 'cancelled'), 0) AS cancelled,
             COALESCE(SUM(r.status = 'registered' AND r.reply_status = 'open'), 0) AS open,
             COALESCE(SUM(r.reply_status <> 'open'), 0) AS replied
           FROM registration r WHERE r.event_id = ?""", (event_id,))
    row["to_review"] = db.query_one(
        "SELECT COUNT(*) AS n FROM mail WHERE event_id = ? AND classification IS NULL", (event_id,))["n"]
    return row
