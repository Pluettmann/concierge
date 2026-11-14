"""Confirm classifications and query the registration list."""
from __future__ import annotations

from .. import db
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


def inbox(event_id: int, view: str = VIEW_OPEN) -> list[dict]:
    where = {
        VIEW_OPEN: "m.classification IS NULL",
        VIEW_DONE: f"m.classification IN ('{REGISTRATION}', '{CANCELLATION}', '{OTHER}')",
        VIEW_BLOCKED: f"m.classification = '{BLOCKED}'",
    }[view]
    return db.query(
        f"""SELECT m.*, p.email, p.first_name AS p_first_name, p.last_name AS p_last_name, p.notes,
               {people.familiarity_sql('m.event_id')} AS familiarity
            FROM mail m JOIN person p ON p.id = m.person_id
            WHERE m.event_id = ? AND {where}
            ORDER BY m.date {'ASC' if view == VIEW_OPEN else 'DESC'}""", (event_id,))


def confirm(mail_id: int, classification: str, first_name: str | None = None, last_name: str | None = None,
            companions: int = 0) -> None:
    mail = db.query_one("SELECT * FROM mail WHERE id = ?", (mail_id,))
    pid, eid = mail["person_id"], mail["event_id"]
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
                   ON CONFLICT(person_id, event_id) DO UPDATE SET status = 'cancelled', mail_id = excluded.mail_id""",
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
               (SELECT COUNT(*) FROM mail m WHERE m.person_id = p.id AND m.event_id = r.event_id) AS mails
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


def mails_of(person_id: int, event_id: int) -> list[dict]:
    return db.query("SELECT * FROM mail WHERE person_id = ? AND event_id = ? ORDER BY date DESC",
                    (person_id, event_id))
