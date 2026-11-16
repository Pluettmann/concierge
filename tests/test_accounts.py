"""Several accounts: routing replies and fetching with failures."""
from datetime import datetime

import pytest

from app import config, db
from app.mail import imap
from app.services import accounts, fetching, registrations, sending
from tests.test_services import make_msg


def test_reply_goes_out_from_receiving_account(event):
    club = accounts.save({"email": "verein@example.org", "sender_name": "Verein", "signature": "Der Vorstand"})
    fetching.store(event, make_msg(), "INBOX", "1", account_id=club)
    fetching.store(event, make_msg(message_id="<b@x>", from_email="bert@x.de"), "INBOX", "1",
                   account_id=accounts.primary()["id"])
    anna = db.query_one("SELECT * FROM person WHERE email = 'anna@x.de'")
    bert = db.query_one("SELECT * FROM person WHERE email = 'bert@x.de'")
    routes = sending.route(event, [anna["id"], bert["id"]])
    assert routes[anna["id"]]["email"] == "verein@example.org"
    assert routes[bert["id"]]["email"] == "ich@example.org"

    msg, _ = sending.build("confirmation", event, anna, 0, accounts.with_globals(routes[anna["id"]]))
    assert msg["From"] == "Verein <verein@example.org>"
    assert "Der Vorstand" in msg.get_body(("plain",)).get_content()

    # People added by hand without a mail are answered from the primary account.
    registrations.add_manual(event["id"], "tel@x.de", "Tina", "", 0)
    tina = db.query_one("SELECT * FROM person WHERE email = 'tel@x.de'")
    assert sending.account_for(tina["id"], event["id"])["email"] == "ich@example.org"

    # Once the account is removed, replies go out from the primary account.
    accounts.delete(club)
    assert sending.account_for(anna["id"], event["id"])["email"] == "ich@example.org"


def test_fetch_continues_when_one_account_fails(event, monkeypatch):
    club = accounts.save({"email": "verein@example.org", "imap_host": "broken.example.org", "inbox_folder": "INBOX"})
    monkeypatch.setattr(config, "get_password", lambda email: "pw")
    monkeypatch.setattr(imap, "sent_recipients", lambda *a, **k: set())

    def fake_fetch(cfg, folder, since, known):
        if cfg.host == "broken.example.org":
            raise ConnectionRefusedError("unreachable")
        msg = make_msg(date=datetime(2026, 10, 5, 10, 0))
        return "7", [msg, make_msg(message_id="<self@x>", from_email="verein@example.org")]
    monkeypatch.setattr(imap, "fetch_since", fake_fetch)

    result = fetching.fetch([event])

    assert result.new == 1  # own addresses (also of the second account) are ignored
    assert [e for e, _ in result.errors] == ["verein@example.org"]
    stored = db.query_one("SELECT * FROM mail")
    assert stored["account_id"] == accounts.primary()["id"]
    assert club != stored["account_id"]


def test_missing_password_is_reported_per_account(event, monkeypatch):
    accounts.save({"email": "verein@example.org"})
    monkeypatch.setattr(config, "get_password", lambda email: "pw" if email == "ich@example.org" else None)
    with pytest.raises(accounts.MissingPassword) as info:
        fetching.fetch([event])
    assert info.value.email == "verein@example.org"


def test_save_reuses_existing_address(database):
    first = accounts.primary()["id"]
    again = accounts.save({"email": "ICH@example.org", "sender_name": "Neu"})
    assert again == first and accounts.primary()["sender_name"] == "Neu"
    assert accounts.own_addresses() == {"ich@example.org"}
