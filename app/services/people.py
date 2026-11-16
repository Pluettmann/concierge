"""People, familiarity status, Thunderbird and newsletter import."""
from __future__ import annotations

import json
from pathlib import Path

from .. import db, thunderbird
from ..classifier import OTHER, reason
from ..mail.parse import guess_name, split_name

MEMBER = "member"
KNOWN = "known"
UNKNOWN = "unknown"
FAMILIARITY = (MEMBER, KNOWN, UNKNOWN)

SOURCE_THUNDERBIRD = "thunderbird"
SOURCE_NEWSLETTER = "newsletter"
SOURCE_MANUAL = "manual"

HIDDEN_REASON = reason("reason.sender_hidden")

def familiarity_sql(event_param: str | None = "?") -> str:
    """SQL expression for the familiarity status of a person `p`.

    A registration for an *earlier* event makes a person known. Several events can be active at
    once, so a registration for another upcoming event does not count. Outside of an event
    (event_param None), registrations for archived or past events count.
    """
    if event_param is None:
        earlier = "(e2.status = 'archived' OR e2.date < date('now', 'localtime'))"
    else:
        earlier = f"r2.event_id <> {event_param} AND e2.date < (SELECT date FROM event WHERE id = {event_param})"
    return f"""CASE
        WHEN p.is_member = 1 THEN '{MEMBER}'
        WHEN p.in_address_book = 1
          OR EXISTS (SELECT 1 FROM correspondent c WHERE c.email = p.email)
          OR EXISTS (SELECT 1 FROM registration r2 JOIN event e2 ON e2.id = r2.event_id
                     WHERE r2.person_id = p.id AND r2.status = 'registered' AND {earlier})
        THEN '{KNOWN}' ELSE '{UNKNOWN}' END"""


def upsert(email: str, display_name: str = "", body: str = "") -> dict:
    """Creates a person if missing and fills in missing names."""
    email = email.strip().lower()
    person = db.query_one("SELECT * FROM person WHERE email = ?", (email,))
    first, last = guess_name(display_name, email, body)
    if person is None:
        pid = db.execute("INSERT INTO person (email, first_name, last_name, first_seen) VALUES (?, ?, ?, ?)",
                         (email, first, last, db.now()))
        return db.query_one("SELECT * FROM person WHERE id = ?", (pid,))
    # Fill in missing names, e.g. when the person was only known from the newsletter recipients.
    same_first = person["first_name"].casefold() == first.casefold()
    if (not person["first_name"] and first) or (same_first and not person["last_name"] and last):
        db.execute("UPDATE person SET first_name = ?, last_name = ? WHERE id = ?", (first, last, person["id"]))
        person.update(first_name=first, last_name=last)
    return person


def get(person_id: int) -> dict | None:
    return db.query_one("SELECT * FROM person WHERE id = ?", (person_id,))


def update(person_id: int, **fields) -> None:
    allowed = {"first_name", "last_name", "notes", "is_member", "member_source", "blocked"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE person SET {sets} WHERE id = ?", (*fields.values(), person_id))


def set_member(person_id: int, value: bool) -> None:
    update(person_id, is_member=int(value), member_source=SOURCE_MANUAL if value else "")


def block(person_id: int, value: bool = True) -> None:
    with db.connect() as con:
        con.execute("UPDATE person SET blocked = ? WHERE id = ?", (int(value), person_id))
        if value:
            con.execute("UPDATE mail SET classification = 'blocked', reviewed_at = ? "
                        "WHERE person_id = ? AND classification IS NULL", (db.now(), person_id))
        else:
            con.execute("UPDATE mail SET classification = NULL, reviewed_at = NULL "
                        "WHERE person_id = ? AND classification = 'blocked'", (person_id,))


def hide(person_ids: list[int], value: bool = True) -> None:
    """Hides senders (or shows them again). Unlike blocking, their mails are not marked as blocked
    but quietly filed as "other"; when shown again they are open for review again."""
    with db.connect() as con:
        for pid in person_ids:
            con.execute("UPDATE person SET hidden = ? WHERE id = ?", (int(value), pid))
            if value:
                rows = con.execute("SELECT id, reasons FROM mail WHERE person_id = ? AND classification IS NULL",
                                   (pid,)).fetchall()
                for row in rows:
                    reasons = [HIDDEN_REASON, *json.loads(row["reasons"] or "[]")]
                    con.execute("UPDATE mail SET classification = ?, reviewed_at = ?, reasons = ? WHERE id = ?",
                                (OTHER, db.now(), json.dumps(reasons, ensure_ascii=False), row["id"]))
            else:
                rows = con.execute("SELECT id, reasons FROM mail WHERE person_id = ? AND classification = ?",
                                   (pid, OTHER)).fetchall()
                for row in rows:
                    reasons = json.loads(row["reasons"] or "[]")
                    if reasons[:1] == [HIDDEN_REASON]:
                        con.execute("UPDATE mail SET classification = NULL, reviewed_at = NULL, reasons = ? "
                                    "WHERE id = ?", (json.dumps(reasons[1:], ensure_ascii=False), row["id"]))


def all_with_status(search: str = "", hidden: bool = False) -> list[dict]:
    """People with familiarity status; hidden people only with hidden=True (then only those)."""
    like = f"%{search.lower()}%"
    return db.query(
        f"""SELECT p.*, {familiarity_sql(None)} AS familiarity,
               (SELECT COUNT(*) FROM registration r WHERE r.person_id = p.id AND r.status = 'registered')
                 AS registrations
            FROM person p
            WHERE p.hidden = ?
              AND (? = '%%' OR lower(p.email) LIKE ? OR lower(p.first_name || ' ' || p.last_name) LIKE ?)
            ORDER BY lower(p.first_name), lower(p.last_name), p.email""",
        (int(hidden), like, like, like))


def history(person_id: int) -> list[dict]:
    return db.query(
        """SELECT e.title, e.date, r.status, r.companions, r.reply_status
           FROM registration r JOIN event e ON e.id = r.event_id
           WHERE r.person_id = ? ORDER BY e.date DESC""", (person_id,))


# --- Members from the newsletter --------------------------------------------------------

def mark_members(emails: list[str], source: str) -> int:
    count = 0
    for email in {e.strip().lower() for e in emails if e and "@" in e}:
        person = upsert(email)
        if not person["is_member"]:
            update(person["id"], is_member=1, member_source=source)
            count += 1
    return count


# --- Thunderbird ------------------------------------------------------------------------

def thunderbird_import(profile: Path, members_source: str) -> dict:
    """Takes over all contacts as "known" and the chosen source as members.

    members_source: "" (none), "book:<file>" or "list:<file>:<list name>".
    Returns {"contacts": count, "members": count}.
    """
    books = thunderbird.read_address_books(profile)
    member_emails: set[str] = set()
    kind, _, rest = members_source.partition(":")
    for book in books:
        if kind == "book" and book.file == rest:
            member_emails.update(c.email for c in book.contacts)
        if kind == "list":
            file, _, list_name = rest.partition(":")
            if book.file == file:
                member_emails.update(book.lists.get(list_name, []))

    contacts = {c.email: c for book in books for c in book.contacts}
    with db.connect() as con:
        existing = {r["email"]: dict(r) for r in con.execute("SELECT * FROM person")}
        con.execute("UPDATE person SET in_address_book = 0")
        for email, c in contacts.items():
            first, last = c.first_name, c.last_name
            if not first:
                first, last = split_name(c.display_name)
            if email in existing:
                con.execute(
                    "UPDATE person SET in_address_book = 1, "
                    "first_name = CASE WHEN first_name = '' THEN ? ELSE first_name END, "
                    "last_name = CASE WHEN last_name = '' THEN ? ELSE last_name END WHERE id = ?",
                    (first, last, existing[email]["id"]))
            else:
                con.execute("INSERT INTO person (email, first_name, last_name, in_address_book, first_seen) "
                            "VALUES (?, ?, ?, 1, ?)", (email, first, last, db.now()))
        # Reset members from Thunderbird; manual and newsletter members stay.
        con.execute("UPDATE person SET is_member = 0, member_source = '' WHERE member_source = ?",
                    (SOURCE_THUNDERBIRD,))
        for email in member_emails:
            con.execute("INSERT OR IGNORE INTO person (email, first_seen) VALUES (?, ?)", (email, db.now()))
            con.execute("UPDATE person SET is_member = 1, member_source = ? WHERE email = ? AND is_member = 0",
                        (SOURCE_THUNDERBIRD, email))
    return {"contacts": len(contacts), "members": len(member_emails)}


def add_correspondents(emails: set[str]) -> None:
    with db.connect() as con:
        con.executemany("INSERT OR IGNORE INTO correspondent VALUES (?)", [(e,) for e in emails])
