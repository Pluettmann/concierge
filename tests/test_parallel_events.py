"""Several active events: assignment of mails, registering for several, archiving."""
from datetime import date, datetime

from app import classifier, db
from app.mail import imap
from app.services import events, fetching, registrations, sending

AUTUMN = {"title": "Herbstabend", "date": "2026-11-14", "newsletter_message_id": "<news-1@example.org>",
          "newsletter_subject": "Einladung: Herbstabend am 14.11.", "search_from": "2026-10-01T00:00:00"}
WINTER = {"title": "Winterfest", "date": "2026-12-12", "newsletter_message_id": "<news-2@example.org>",
          "newsletter_subject": "Einladung: Winterfest am 12.12.", "search_from": "2026-10-20T00:00:00"}


def msg(**kw):
    defaults = dict(uid="1", message_id="<m1@x.de>", from_email="anna@x.de", from_name="Anna Schmidt",
                    subject="Hallo", date=datetime(2026, 11, 1, 10, 0), text="Ich komme gerne!",
                    in_reply_to="")
    defaults.update(kw)
    return imap.Message(**defaults)


def two_events(database):
    a = events.get(events.create(AUTUMN))
    w = events.get(events.create(WINTER))
    return a, w


def assign(evs, subject="Hallo", body="Ich komme", in_reply_to="", received=date(2026, 11, 1)):
    return classifier.assign_event(subject, body, in_reply_to, "", evs, received)


def test_creating_keeps_other_events_active(database):
    a, w = two_events(database)
    assert [e["id"] for e in events.active_events()] == [a["id"], w["id"]]
    events.reactivate(a["id"])
    assert len(events.active_events()) == 2


def test_assignment_order(database):
    a, w = two_events(database)
    # 1. reply to a newsletter
    r = assign([a, w], in_reply_to="<news-2@example.org>")
    assert r.event["id"] == w["id"] and not r.uncertain
    # ... or its subject
    assert assign([a, w], subject="AW: Einladung: Winterfest am 12.12.").event["id"] == w["id"]
    # 2. title or date mentioned
    assert assign([a, w], body="Zum Winterfest komme ich").event["id"] == w["id"]
    assert assign([a, w], body="Am 14.11. bin ich dabei").event["id"] == a["id"]
    # 3. otherwise the nearest upcoming one, to be checked
    r = assign([a, w], received=date(2026, 11, 20))
    assert r.event["id"] == w["id"] and r.uncertain and r.reasons == [{"key": "reason.event_guessed"}]
    r = assign([a, w], body="Ich komme zum Herbstabend und zum Winterfest")
    assert r.event["id"] == a["id"] and r.uncertain


def test_shared_newsletter_decided_by_content(database):
    a = events.get(events.create(AUTUMN))
    w = events.get(events.create({**WINTER, "newsletter_message_id": AUTUMN["newsletter_message_id"]}))
    r = assign([a, w], body="Ich komme am 12.12.", in_reply_to="<news-1@example.org>")
    assert r.event["id"] == w["id"] and not r.uncertain
    assert assign([a, w], in_reply_to="<news-1@example.org>").uncertain


def test_fetch_assigns_mails(database, monkeypatch):
    a, w = two_events(database)
    mails = [msg(message_id="<1@x>", in_reply_to="<news-2@example.org>"),
             msg(message_id="<2@x>", from_email="bert@x.de", in_reply_to="<news-1@example.org>"),
             # before the winter newsletter went out: only autumn is possible
             msg(message_id="<3@x>", from_email="cleo@x.de", date=datetime(2026, 10, 5, 9, 0)),
             # before any event
             msg(message_id="<4@x>", from_email="dora@x.de", date=datetime(2026, 9, 1, 9, 0))]
    monkeypatch.setattr(imap, "fetch_since", lambda cfg, folder, since, known: ("7", mails))
    monkeypatch.setattr(fetching, "sync_sent", lambda account: 0)
    monkeypatch.setattr(fetching.accounts, "password", lambda account: "pw")
    assert fetching.fetch().new == 3
    by_sender = {m["email"]: m["event_id"] for m in registrations.inbox(None)}
    assert by_sender == {"anna@x.de": w["id"], "bert@x.de": a["id"], "cleo@x.de": a["id"]}
    assert events.to_review_total() == 3


def test_move_and_register_for_both(database):
    a, w = two_events(database)
    mid = fetching.store([a, w], msg(), "INBOX", "1")
    mail = db.query_one("SELECT * FROM mail WHERE id = ?", (mid,))
    assert mail["event_id"] == a["id"] and "event_guessed" in mail["reasons"]

    moved = registrations.move(mid, w["id"])
    assert moved["event_id"] == w["id"] and "event_guessed" not in moved["reasons"]

    registrations.confirm(mid, classifier.REGISTRATION, "Anna", "Schmidt", 0, [w["id"], a["id"]])
    assert [r["email"] for r in registrations.registrations(a["id"])] == ["anna@x.de"]
    assert [r["email"] for r in registrations.registrations(w["id"])] == ["anna@x.de"]
    # The reply for the other event still answers the mail.
    assert [m["id"] for m in registrations.mails_of(mail["person_id"], a["id"])] == [mid]
    assert sending.account_for(mail["person_id"], a["id"]) is not None
    assert registrations.registrations(a["id"])[0]["mails"] == 1

    registrations.reset(mid)
    assert registrations.registrations(a["id"]) == [] and registrations.registrations(w["id"]) == []


def test_archive_past_once(database):
    a, w = two_events(database)
    assert events.archive_past(date(2026, 11, 20)) == 0  # within the week after
    assert events.archive_past(date(2026, 11, 22)) == 1
    assert [e["id"] for e in events.active_events()] == [w["id"]]
    events.reactivate(a["id"])  # reactivated by hand: stays active
    assert events.archive_past(date(2026, 11, 23)) == 0
    assert len(events.active_events()) == 2


def test_default_event_is_next_upcoming(database, monkeypatch):
    _, w = two_events(database)

    class Today(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 20)
    monkeypatch.setattr(events, "date", Today)
    assert events.active()["id"] == w["id"]
