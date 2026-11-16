"""Create, change and archive events; key figures.

Several events can be active at the same time (e.g. next month and the month after). An event is
archived when it is closed by hand, or automatically a while after its date.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from .. import db

FIELDS = ("title", "date", "start_time", "end_time", "venue", "address", "map_link", "info",
          "newsletter_message_id", "newsletter_subject", "search_from")
# Changes to these fields turn the calendar entry into a new version.
ICS_FIELDS = ("title", "date", "start_time", "end_time", "venue", "address", "info")

ACTIVE = "active"
ARCHIVED = "archived"
# Late mails (thanks, photos, "next time") still arrive in the days after an event.
AUTO_ARCHIVE_AFTER = timedelta(days=7)


def active_events() -> list[dict]:
    """All active events, the earliest first."""
    return db.query("SELECT * FROM event WHERE status = 'active' ORDER BY date, id")


def active() -> dict | None:
    """The event shown when nothing else is chosen: the next upcoming one, otherwise the latest."""
    events = active_events()
    today = date.today().isoformat()
    return next((e for e in events if e["date"] >= today), events[-1] if events else None)


def archive_past(today: date | None = None) -> int:
    """Archives active events whose date is more than AUTO_ARCHIVE_AFTER ago.

    Each event is archived automatically only once, when it passes that limit: an event that was
    reactivated by hand afterwards stays active.
    """
    limit = ((today or date.today()) - AUTO_ARCHIVE_AFTER).isoformat()
    previous = db.get_setting("auto_archived_before")
    if previous >= limit:
        return 0
    with db.connect() as con:
        count = con.execute("UPDATE event SET status = 'archived' WHERE status = 'active' AND date < ? "
                            "AND date >= ?", (limit, previous)).rowcount
    db.set_settings(auto_archived_before=limit)
    return count


def latest() -> dict | None:
    """The event created last (its place and time are suggested for the next one)."""
    return db.query_one("SELECT * FROM event ORDER BY created_at DESC, id DESC LIMIT 1")


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


def create(data: dict, archive_others: bool = False) -> int:
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
    db.execute("UPDATE event SET status = 'active' WHERE id = ?", (event_id,))


def to_review_total() -> int:
    """Mails still to review across all active events (the inbox shows them together)."""
    return db.query_one(
        "SELECT COUNT(*) AS n FROM mail m JOIN event e ON e.id = m.event_id "
        "WHERE e.status = 'active' AND m.classification IS NULL")["n"]


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
