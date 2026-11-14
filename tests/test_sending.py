"""Sending: folded headers from mail threads and errors for single recipients.

Bug seen in practice: when a registration arrived as a reply in a longer thread, its References
header was folded. The reply could not be built (ValueError), and sending to the selection stopped
silently: everybody stayed "open".
"""
import email
import email.utils
import socket
import time
from datetime import datetime

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult
from imap_tools import MailMessage

from app import config, db
from app.mail import imap
from app.services import accounts, fetching, registrations, sending
from tests.test_services import make_msg

FOLDED_REFS = "<newsletter-1@verein.de> <reply-1@sehr-lange-domain.example.org>\r\n <reply-2@x.de>"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_server(database, monkeypatch):
    received = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            received.append(email.message_from_bytes(envelope.original_content or envelope.content))
            return "250 OK"

    port = _free_port()
    ctrl = Controller(Handler(), hostname="127.0.0.1", port=port, auth_require_tls=False,
                      authenticator=lambda *a: AuthResult(success=True))
    ctrl.start()
    accounts.save({"smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none", "login": "",
                   "imap_host": "127.0.0.1", "imap_port": _free_port(), "imap_security": "none",
                   "sent_folder": ""}, accounts.primary()["id"])
    monkeypatch.setattr(config, "get_password", lambda e: "")
    monkeypatch.setattr(accounts, "password", lambda a: "pw")
    yield received
    ctrl.stop()


def _wait_for(received, count):
    for _ in range(50):  # the SMTP server runs in its own thread
        if len(received) >= count:
            return
        time.sleep(0.05)


def test_imap_header_is_unfolded():
    raw = (b"From: p@x.de\r\nSubject: Re: X\r\nMessage-ID: <r3@x.de>\r\n"
           b"References: <newsletter-1@verein.de> <reply-1@sehr-lange-domain.example.org>\r\n <reply-2@x.de>\r\n"
           b"Date: Thu, 12 Nov 2026 10:00:00 +0100\r\n\r\nIch komme.\r\n")
    m = imap._convert(MailMessage.from_bytes(raw))
    assert "\r" not in m.references and "\n" not in m.references
    assert m.references.endswith("<reply-2@x.de>")


def test_build_with_folded_references_from_existing_database(event):
    """Mails fetched before the fix still have the folded header in the database."""
    mid = fetching.store(event, make_msg(references=FOLDED_REFS), "INBOX", "1")
    registrations.confirm(mid, "registration")
    person = db.query_one("SELECT * FROM person WHERE email = 'anna@x.de'")
    msg, _ = sending.build("confirmation", event, person, 0, accounts.with_globals(accounts.primary()))
    assert msg["References"].endswith("<reply-2@x.de> <m1@x.de>")
    assert "\n" not in msg["References"]
    msg.as_bytes()  # can be serialized


def test_batch_continues_when_one_recipient_fails(event, smtp_server, monkeypatch):
    for i, addr in enumerate(["a@x.de", "b@x.de", "c@x.de"]):
        mid = fetching.store(event, make_msg(message_id=f"<m{i}@x>", from_email=addr,
                                             date=datetime(2026, 10, 5, 10, i)), "INBOX", "1")
        registrations.confirm(mid, "registration")
    ids = [r["person_id"] for r in registrations.registrations(event["id"])]

    real_build = sending.build

    def flaky_build(kind, ev, person, *args, **kwargs):
        if person["email"] == "b@x.de":
            raise ValueError("broken header")
        return real_build(kind, ev, person, *args, **kwargs)
    monkeypatch.setattr(sending, "build", flaky_build)

    sender = sending.Sender(event, ids)
    results = {pid: sender.send("confirmation", pid) for pid in ids}
    sender.close()

    errors = [e for e in results.values() if e]
    assert len(errors) == 1 and errors[0].startswith("b@x.de:")
    _wait_for(smtp_server, 2)
    assert sorted(email.utils.parseaddr(m["To"])[1] for m in smtp_server) == ["a@x.de", "c@x.de"]
    status = {r["email"]: r["reply_status"] for r in registrations.registrations(event["id"])}
    assert status == {"a@x.de": "confirmation_sent", "b@x.de": "open", "c@x.de": "confirmation_sent"}


def test_reply_with_folded_references_is_sent(event, smtp_server):
    mid = fetching.store(event, make_msg(references=FOLDED_REFS), "INBOX", "1")
    registrations.confirm(mid, "registration")
    pid = db.query_one("SELECT id FROM person WHERE email = 'anna@x.de'")["id"]
    sender = sending.Sender(event, [pid])
    assert sender.send("confirmation", pid) is None
    sender.close()
    _wait_for(smtp_server, 1)
    assert smtp_server and smtp_server[0]["In-Reply-To"] == "<m1@x.de>"
    attachment = next(p for p in smtp_server[0].walk() if p.get_content_type() == "text/calendar")
    assert attachment.get_filename() == "termin.ics"
