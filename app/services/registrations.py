"""Confirm classifications and query the registration list."""
from __future__ import annotations

import json

from .. import classifier, db
from ..classifier import CANCELLATION, OTHER, REGISTRATION
from . import people

BLOCKED = "blocked"

REGISTERED = "registered"
CANCELLED = "cancelled"

OPEN = "open"
CONFIRMATION_SENT = "confirmation_sent"
REJECTION_SENT = "rejection_sent"
PERSONAL_REPLY_SENT = "personal_reply_sent"
REPLY_STATUSES = (OPEN, CONFIRMATION_SENT, REJECTION_SENT, PERSONAL_REPLY_SENT)

# Inbox views: mails to review, done, from blocked senders.
VIEW_OPEN = "open"
VIEW_DONE = "done"
VIEW_BLOCKED = "blocked"
VIEWS = (VIEW_OPEN, VIEW_DONE, VIEW_BLOCKED)


def inbox(event_id: int | None = None, view: str = VIEW_OPEN) -> list[dict]:
    """Mails of one event, or of all active events (event_id None)."""
    where = {
        VIEW_OPEN: "m.classification IS NULL",
        VIEW_DONE: f"m.classification IN ('{REGISTRATION}', '{CANCELLATION}', '{OTHER}')",
        VIEW_BLOCKED: f"m.classification = '{BLOCKED}'",
    }[view]
    event_filter = "m.event_id = ?" if event_id is not None else "e.status = 'active'"
    return db.query(
        f"""SELECT m.*, p.email, p.first_name AS p_first_name, p.last_name AS p_last_name, p.notes,
               e.title AS event_title, e.date AS event_date,
               {people.familiarity_sql('m.event_id')} AS familiarity
            FROM mail m JOIN person p ON p.id = m.person_id JOIN event e ON e.id = m.event_id
            WHERE {event_filter} AND {where}
            ORDER BY m.date {'ASC' if view == VIEW_OPEN else 'DESC'}""",
        (event_id,) if event_id is not None else ())


def move(mail_id: int, event_id: int) -> dict:
    """Assigns a mail that is still to be reviewed to another event and classifies it anew."""
    mail = db.query_one("SELECT * FROM mail WHERE id = ?", (mail_id,))
    event = db.query_one("SELECT * FROM event WHERE id = ?", (event_id,))
    has_registration = db.query_one(
        "SELECT 1 AS x FROM registration WHERE person_id = ? AND event_id = ? AND status = 'registered'",
        (mail["person_id"], event_id)) is not None
    result = classifier.classify(mail["subject"], mail["body_clean"], mail["in_reply_to"], mail["refs"], event,
                                 has_registration)
    hidden = [r for r in json.loads(mail["reasons"] or "[]") if r == people.HIDDEN_REASON]
    db.execute("UPDATE mail SET event_id = ?, suggestion = ?, score = ?, reasons = ?, companions = ? WHERE id = ?",
               (event_id, result.suggestion, result.score, json.dumps(hidden + result.reasons, ensure_ascii=False),
                result.companions, mail_id))
    return db.query_one("SELECT * FROM mail WHERE id = ?", (mail_id,))


def confirm(mail_id: int, classification: str, first_name: str | None = None, last_name: str | None = None,
            companions: int = 0, event_ids: list[int] | None = None) -> None:
    """Confirms the classification of a mail. With event_ids, a registration or cancellation applies
    to each of these events ("I'll come to both"); the mail itself belongs to the first one."""
    mail = db.query_one("SELECT * FROM mail WHERE id = ?", (mail_id,))
    pid = mail["person_id"]
    eids = list(dict.fromkeys(event_ids or [mail["event_id"]]))
    if eids[0] != mail["event_id"]:
        db.execute("UPDATE mail SET event_id = ? WHERE id = ?", (eids[0], mail_id))
    if first_name is not None or last_name is not None:
        p = people.get(pid)
        people.update(pid, first_name=(first_name if first_name is not None else p["first_name"]).strip(),
                      last_name=(last_name if last_name is not None else p["last_name"]).strip())
    if classification == BLOCKED:
        db.execute("UPDATE mail SET classification = ?, reviewed_at = ? WHERE id = ?", (BLOCKED, db.now(), mail_id))
        people.block(pid, True)
        return
    with db.connect() as con:
        con.execute("UPDATE mail SET classification = ?, reviewed_at = ?, companions = ? WHERE id = ?",
                    (classification, db.now(), int(companions or 0), mail_id))
        for eid in eids:
            if classification == REGISTRATION:
                con.execute(
                    """INSERT INTO registration (person_id, event_id, status, companions, mail_id, registered_at)
                       VALUES (?, ?, 'registered', ?, ?, ?)
                       ON CONFLICT(person_id, event_id) DO UPDATE SET
                         status = 'registered', companions = excluded.companions, mail_id = excluded.mail_id""",
                    (pid, eid, int(companions or 0), mail_id, mail["date"]))
            elif classification == CANCELLATION:
                con.execute(
                    """INSERT INTO registration (person_id, event_id, status, mail_id, registered_at)
                       VALUES (?, ?, 'cancelled', ?, ?)
                       ON CONFLICT(person_id, event_id) DO UPDATE SET status = 'cancelled',
                         mail_id = excluded.mail_id""",
                    (pid, eid, mail_id, mail["date"]))


def reset(mail_id: int) -> None:
    """Puts a mail back to "to review". A registration created from it is removed if not replied to yet."""
    mail = db.query_one("SELECT * FROM mail WHERE id = ?", (mail_id,))
    with db.connect() as con:
        if mail["classification"] == BLOCKED:
            con.execute("UPDATE person SET blocked = 0 WHERE id = ?", (mail["person_id"],))
        con.execute("DELETE FROM registration WHERE mail_id = ? AND reply_status = 'open'", (mail_id,))
        con.execute("UPDATE mail SET classification = NULL, reviewed_at = NULL WHERE id = ?", (mail_id,))


def add_manual(event_id: int, email: str, first_name: str, last_name: str, companions: int = 0) -> None:
    """Adds a registration without a mail (e.g. by phone)."""
    p = people.upsert(email)
    people.update(p["id"], first_name=first_name or p["first_name"], last_name=last_name or p["last_name"])
    db.execute(
        """INSERT INTO registration (person_id, event_id, status, companions, registered_at)
           VALUES (?, ?, 'registered', ?, ?)
           ON CONFLICT(person_id, event_id) DO UPDATE SET status = 'registered',
             companions = excluded.companions""",
        (p["id"], event_id, int(companions or 0), db.now()))


def registrations(event_id: int) -> list[dict]:
    return db.query(
        f"""SELECT r.*, p.email, p.first_name, p.last_name, p.notes, p.blocked,
               {people.familiarity_sql('r.event_id')} AS familiarity,
               (SELECT COUNT(*) FROM mail m WHERE m.person_id = p.id
                  AND (m.event_id = r.event_id OR m.id = r.mail_id)) AS mails
            FROM registration r JOIN person p ON p.id = r.person_id
            WHERE r.event_id = ?
            ORDER BY r.registered_at""", (event_id,))


def update(registration_id: int, **fields) -> None:
    allowed = {"status", "companions", "reply_status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE registration SET {sets} WHERE id = ?", (*fields.values(), registration_id))


def delete(registration_id: int) -> None:
    db.execute("DELETE FROM registration WHERE id = ?", (registration_id,))


# Mails of a person about an event, including the one a registration came from when that mail
# belongs to another event ("I'll come to both").
MAILS_OF_SQL = """person_id = :person AND (event_id = :event OR id IN (
    SELECT mail_id FROM registration WHERE person_id = :person AND event_id = :event))"""


def mails_of(person_id: int, event_id: int) -> list[dict]:
    return db.query(f"SELECT * FROM mail WHERE {MAILS_OF_SQL} ORDER BY date DESC",
                    {"person": person_id, "event": event_id})
