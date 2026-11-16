"""Fetch mails from all accounts, classify them and update who is known."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import classifier, config, db
from ..mail import imap
from ..mail.parse import strip_quotes
from . import accounts, people

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    new: int = 0
    errors: list[tuple[str, Exception]] = field(default_factory=list)  # (account address, error)
    thunderbird: dict | None = None


def imap_settings(account: dict) -> imap.Settings:
    return imap.Settings.from_db(account, accounts.password(account))


def sync_thunderbird() -> dict | None:
    profile = config.thunderbird_profile()
    if not profile or not profile.is_dir():
        return None
    try:
        return people.thunderbird_import(profile, db.get_setting("tb_member_source"))
    except Exception as exc:
        log.warning("Thunderbird import failed: %s", exc)
        return None


def sync_sent(account: dict) -> int:
    """Reads the recipients from an account's sent folder (completely the first time, afterwards
    since the last sync)."""
    folder = account.get("sent_folder")
    if not folder:
        return 0
    last = account.get("correspondence_synced_until")
    since = date.fromisoformat(last) if last else None
    # Mails sent by this app do not make anyone "known".
    app_ids = {r["message_id"] for r in db.query("SELECT message_id FROM sent_mail WHERE message_id <> ''")}
    found = imap.sent_recipients(imap_settings(account), folder, since, skip_ids=app_ids)
    found -= accounts.own_addresses()
    people.add_correspondents(found)
    accounts.save({"correspondence_synced_until": date.today().isoformat()}, account["id"])
    return len(found)


def sync_all_sent() -> None:
    for account in accounts.all_accounts():
        sync_sent(account)


def recent_sent_all(limit: int = 25) -> list[imap.Message]:
    """The most recently sent mails of all accounts, newest first (to pick the newsletter).
    An unreachable account is skipped as long as at least one answers."""
    check_passwords()
    found, errors = [], []
    for account in accounts.all_accounts():
        if not account.get("sent_folder"):
            continue
        try:
            msgs = imap.recent_sent(imap_settings(account), account["sent_folder"], limit)
        except Exception as exc:
            log.warning("Cannot read sent folder of %s: %s", account["email"], exc)
            errors.append(exc)
            continue
        for m in msgs:
            m.account = account["email"]
        found.extend(msgs)
    if errors and not found:
        raise errors[0]
    return sorted(found, key=lambda m: m.date, reverse=True)[:limit]


def check_passwords() -> None:
    """Raises MissingPassword for the first account without a stored password."""
    for account in accounts.all_accounts():
        accounts.password(account)


def fetch(events: list[dict] | None = None) -> FetchResult:
    """Fetches new mails from all accounts for all active events, assigns each mail to an event and
    creates suggestions. Runs in a background thread.

    An error in one account does not stop the others.
    """
    from . import events as events_service
    events = events_service.active_events() if events is None else events
    check_passwords()
    result = FetchResult(thunderbird=sync_thunderbird())
    if not events:
        return result
    own = accounts.own_addresses()
    since = min(_search_from(e) for e in events)
    for account in accounts.all_accounts():
        try:
            try:
                sync_sent(account)
            except Exception as exc:
                log.warning("Syncing the sent folder of %s failed: %s", account["email"], exc)
            folder = account.get("inbox_folder") or "INBOX"
            known = {r["message_id"] for r in db.query("SELECT message_id FROM mail")}
            validity, msgs = imap.fetch_since(imap_settings(account), folder, since.date(), known)
        except Exception as exc:
            log.warning("Fetching %s failed: %s", account["email"], exc)
            result.errors.append((account["email"], exc))
            continue
        for m in sorted(msgs, key=lambda m: m.date):
            if m.from_email in own:
                continue
            # Only events whose newsletter had already gone out when the mail arrived.
            open_events = [e for e in events if m.date >= _search_from(e)]
            if not open_events:
                continue
            store(open_events, m, folder, validity, account["id"])
            result.new += 1
    return result


def _search_from(event: dict) -> datetime:
    return datetime.fromisoformat(event["search_from"]).replace(hour=0, minute=0, second=0)


def store(events: dict | list[dict], m: imap.Message, folder: str, validity: str,
          account_id: int | None = None) -> int:
    """Stores a mail for the event it most likely belongs to (see classifier.assign_event)."""
    if isinstance(events, dict):
        events = [events]
    body = strip_quotes(m.text)
    assignment = classifier.assign_event(m.subject, body, m.in_reply_to, m.references, events, m.date.date())
    event = assignment.event
    person = people.upsert(m.from_email, m.from_name, body)
    has_registration = db.query_one(
        "SELECT 1 AS x FROM registration WHERE person_id = ? AND event_id = ? AND status = 'registered'",
        (person["id"], event["id"])) is not None
    if person["blocked"]:
        result = classifier.Result(classifier.OTHER, 100, 0, [classifier.reason("reason.sender_blocked")])
        classification, reviewed = "blocked", db.now()
    else:
        result = classifier.classify(m.subject, body, m.in_reply_to, m.references, event, has_registration)
        if m.auto_submitted and result.suggestion == classifier.OTHER:
            result.reasons.insert(0, classifier.reason("reason.auto_submitted"))
        classification, reviewed = None, None
        if person.get("hidden"):
            # Hidden senders never count as registrations; the mail goes straight to "done".
            result.reasons.insert(0, people.HIDDEN_REASON)
            classification, reviewed = classifier.OTHER, db.now()
    result.reasons += assignment.reasons  # after: the hidden reason must stay first (see people.hide)
    return db.execute(
        """INSERT OR IGNORE INTO mail (message_id, folder, uid, uidvalidity, person_id, sender_name, subject, date,
             body, body_clean, in_reply_to, refs, event_id, suggestion, score, reasons,
             companions, detected_first_name, classification, reviewed_at, account_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (m.message_id, folder, m.uid, validity, person["id"], m.from_name, m.subject,
         m.date.isoformat(timespec="seconds"), m.text, body, m.in_reply_to, m.references, event["id"],
         result.suggestion, result.score, json.dumps(result.reasons, ensure_ascii=False), result.companions,
         person["first_name"], classification, reviewed, account_id))
