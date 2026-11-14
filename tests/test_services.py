import email
import json
import sqlite3
from datetime import datetime

from icalendar import Calendar

from app import db, i18n, ics, templates
from app.mail import imap
from app.services import accounts, events, fetching, people, registrations, sending


def make_msg(**kw):
    defaults = dict(uid="1", message_id="<m1@x.de>", from_email="anna@x.de", from_name="Anna Schmidt",
                    subject="Re: Einladung: Herbstabend am 14.11.", date=datetime(2026, 10, 5, 10, 0),
                    text="Ich komme zu zweit!\n\nAm 1.10. schrieb Pascal:\n> Einladung",
                    in_reply_to="<news-1@example.org>")
    defaults.update(kw)
    return imap.Message(**defaults)


def test_store_confirm_and_list(event):
    mid = fetching.store(event, make_msg(), "INBOX", "42")
    open_mails = registrations.inbox(event["id"], registrations.VIEW_OPEN)
    assert len(open_mails) == 1 and open_mails[0]["suggestion"] == "registration"
    assert open_mails[0]["companions"] == 1
    assert open_mails[0]["familiarity"] == "unknown"
    assert "Antwort auf den Newsletter" in i18n.reasons_text(open_mails[0]["reasons"])

    registrations.confirm(mid, "registration", "Anna", "Schmidt", 1)
    regs = registrations.registrations(event["id"])
    assert len(regs) == 1 and regs[0]["companions"] == 1 and regs[0]["reply_status"] == "open"

    registrations.reset(mid)
    assert registrations.registrations(event["id"]) == []


def test_familiarity(event):
    people.mark_members(["mitglied@x.de"], people.SOURCE_NEWSLETTER)
    people.add_correspondents({"bekannt@x.de"})
    for i, addr in enumerate(["mitglied@x.de", "bekannt@x.de", "fremd@x.de"]):
        mid = fetching.store(event, make_msg(message_id=f"<m{i}@x>", from_email=addr), "INBOX", "1")
        registrations.confirm(mid, "registration")
    status = {r["email"]: r["familiarity"] for r in registrations.registrations(event["id"])}
    assert status == {"mitglied@x.de": "member", "bekannt@x.de": "known", "fremd@x.de": "unknown"}
    # On the people page, the current registration does not count as attending before ...
    assert {p["email"]: p["familiarity"] for p in people.all_with_status()}["fremd@x.de"] == "unknown"
    # ... but after archiving it does (for the next event).
    events.create({"title": "Nächstes Mal", "date": "2027-01-10"})
    assert {p["email"]: p["familiarity"] for p in people.all_with_status()}["fremd@x.de"] == "known"


def test_blocked_sender_skips_inbox(event):
    p = people.upsert("spam@x.de")
    people.block(p["id"])
    fetching.store(event, make_msg(from_email="spam@x.de"), "INBOX", "1")
    assert registrations.inbox(event["id"], registrations.VIEW_OPEN) == []
    assert len(registrations.inbox(event["id"], registrations.VIEW_BLOCKED)) == 1


def test_hidden_sender(event):
    # A mail that is already open is filed quietly when hiding, not blocked.
    mid = fetching.store(event, make_msg(from_email="shop@firma.de"), "INBOX", "1")
    pid = db.query_one("SELECT person_id FROM mail WHERE id = ?", (mid,))["person_id"]
    people.hide([pid])
    assert registrations.inbox(event["id"], registrations.VIEW_OPEN) == []
    assert registrations.inbox(event["id"], registrations.VIEW_BLOCKED) == []
    done = registrations.inbox(event["id"], registrations.VIEW_DONE)
    assert len(done) == 1 and done[0]["classification"] == "other"
    assert "shop@firma.de" not in {p["email"] for p in people.all_with_status()}
    assert [p["email"] for p in people.all_with_status(hidden=True)] == ["shop@firma.de"]

    # New mails are never suggested as registrations; the person stays hidden.
    fetching.store(event, make_msg(message_id="<m2@x>", from_email="shop@firma.de"), "INBOX", "1")
    assert registrations.inbox(event["id"], registrations.VIEW_OPEN) == []
    assert people.get(pid)["hidden"] == 1 and people.get(pid)["blocked"] == 0

    # Showing again: both mails are open for review again, without the hidden reason.
    people.hide([pid], False)
    open_mails = registrations.inbox(event["id"], registrations.VIEW_OPEN)
    assert len(open_mails) == 2
    assert all(people.HIDDEN_REASON not in json.loads(m["reasons"]) for m in open_mails)
    assert "shop@firma.de" in {p["email"] for p in people.all_with_status()}


def test_render_templates(event, database):
    s = accounts.with_globals(accounts.primary())
    subject, html, text = templates.render("confirmation", event, {"first_name": "Anna", "last_name": "S"}, 1, s)
    assert subject == "Deine Anmeldung: Herbstabend am 14.11.2026"
    assert "Hallo Anna," in html and "Samstag, 14. November 2026" in html and "19:00 - 22:00 Uhr" in html
    assert "mit 1 Begleitperson" in text
    assert '<html lang="de">' in html
    _, html, _ = templates.render("rejection", event, {"first_name": ""}, 0, s)
    assert "Hallo," in html


def test_ics(event):
    cal = Calendar.from_ical(ics.build(event, "ich@example.org"))
    ev = cal.walk("VEVENT")[0]
    assert str(ev["summary"]) == "Herbstabend"
    assert ev.decoded("dtstart").hour == 18  # 19:00 CET = 18:00 UTC
    assert str(ev["uid"]) == event["ics_uid"]


def test_build_reply_headers(event):
    mid = fetching.store(event, make_msg(), "INBOX", "42")
    registrations.confirm(mid, "registration", "Anna", "Schmidt", 1)
    person = db.query_one("SELECT * FROM person WHERE email = 'anna@x.de'")
    msg, _ = sending.build("confirmation", event, person, 1, accounts.with_globals(accounts.primary()))
    assert msg["In-Reply-To"] == "<m1@x.de>"
    assert msg["To"] == "Anna Schmidt <anna@x.de>"
    parsed = email.message_from_bytes(msg.as_bytes())
    types = [p.get_content_type() for p in parsed.walk()]
    assert "text/calendar" in types and "text/html" in types and "text/plain" in types


def test_thunderbird_import(database, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    con = sqlite3.connect(profile / "abook.sqlite")
    con.executescript("""
        CREATE TABLE properties (card TEXT, name TEXT, value TEXT);
        CREATE TABLE lists (uid TEXT, name TEXT, nickName TEXT, description TEXT);
        CREATE TABLE list_cards (list TEXT, card TEXT);
        INSERT INTO properties VALUES ('c1','PrimaryEmail','Anna@X.de'),('c1','FirstName','Anna'),
               ('c2','PrimaryEmail','bert@x.de'),('c2','DisplayName','Bert Brot');
        INSERT INTO lists VALUES ('l1','Newsletter','','');
        INSERT INTO list_cards VALUES ('l1','c1');
    """)
    con.commit()
    con.close()
    result = people.thunderbird_import(profile, "list:abook.sqlite:Newsletter")
    assert result == {"contacts": 2, "members": 1}
    status = {p["email"]: p["familiarity"] for p in people.all_with_status()}
    assert status == {"anna@x.de": "member", "bert@x.de": "known"}
