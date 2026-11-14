"""Build and send replies: confirmation (with ICS), rejection, personal reply.

Each reply goes out from the account in which the person's last mail arrived (otherwise from the
primary account). That keeps the thread intact for the recipient and puts the reply into the right
sent folder.
"""
from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .. import db, ics, templates
from ..i18n import format_date, t
from ..mail import imap, smtp
from . import accounts
from .registrations import CONFIRMATION_SENT, PERSONAL_REPLY_SENT, REJECTION_SENT

log = logging.getLogger(__name__)

CONFIRMATION, REJECTION, PERSONAL = templates.CONFIRMATION, templates.REJECTION, "personal"
REPLY_STATUS = {CONFIRMATION: CONFIRMATION_SENT, REJECTION: REJECTION_SENT, PERSONAL: PERSONAL_REPLY_SENT}


def _last_mail(person_id: int, event_id: int) -> dict | None:
    return db.query_one("SELECT * FROM mail WHERE person_id = ? AND event_id = ? "
                        "AND classification IS NOT 'blocked' ORDER BY date DESC LIMIT 1", (person_id, event_id))


def account_for(person_id: int, event_id: int) -> dict | None:
    """The account the reply to this person is sent from."""
    original = _last_mail(person_id, event_id)
    return accounts.get_or_primary(original["account_id"] if original else None)


def route(event: dict, person_ids: list[int]) -> dict[int, dict]:
    """Assigns each person the account to send from."""
    return {pid: account_for(pid, event["id"]) for pid in person_ids}


def _one_line(value: str) -> str:
    """Removes line breaks from header values. Older fetched mails may still contain folded
    References/In-Reply-To headers; EmailMessage rejects those with ValueError."""
    return " ".join((value or "").split())


def _quote(mail: dict) -> str:
    body = html.escape(mail.get("body_clean") or mail.get("body") or "").replace("\n", "<br>")
    header = t("mail.quote_header", when=format_date(mail["date"], "datetime"), who=mail.get("sender_name") or "")
    return (f'<p style="color:#6b7280;margin-top:24px;">{html.escape(header)}</p>'
            f'<blockquote style="margin:0;padding-left:12px;border-left:3px solid #d1d5db;color:#6b7280;">'
            f'{body}</blockquote>')


def build(kind: str, event: dict, person: dict, companions: int, settings: dict,
          subject: str | None = None, content_html: str | None = None) -> tuple[EmailMessage, dict | None]:
    """Creates the mail for one person. `settings` is the sending account plus shared settings
    (see accounts.with_globals). Returns (message, original mail or None)."""
    original = _last_mail(person["id"], event["id"])
    if kind == PERSONAL:
        body = content_html or ""
        if original:
            body += _quote(original)
        full_html, text = templates.render_personal(body, settings)
        subject = subject or (("Re: " + original["subject"]) if original else event["title"])
    else:
        subject, full_html, text = templates.render(kind, event, person, companions, settings)

    sender = settings["email"]
    msg = EmailMessage()
    msg["From"] = formataddr((settings.get("sender_name", ""), sender))
    full_name = " ".join(p for p in (person.get("first_name"), person.get("last_name")) if p)
    msg["To"] = formataddr((full_name, person["email"]))
    msg["Subject"] = _one_line(subject)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    if original:
        msg["In-Reply-To"] = _one_line(original["message_id"])
        msg["References"] = _one_line(" ".join(p for p in (original.get("refs", ""), original["message_id"]) if p))
    msg.set_content(text)
    msg.add_alternative(full_html, subtype="html")
    if kind == CONFIRMATION:
        msg.add_attachment(ics.build(event, sender, settings.get("sender_name", "")),
                           maintype="text", subtype="calendar", filename=t("mail.ics_filename"),
                           params={"method": "PUBLISH"})
    return msg, original


class _Connection:
    """SMTP and IMAP connection to one account."""

    def __init__(self, account: dict, password: str):
        self.account = account
        self.settings = accounts.with_globals(account)
        self.smtp_cfg = smtp.Settings.from_db(account, password)
        self.server = smtp.connect(self.smtp_cfg)
        try:
            self.imap = imap.Session(imap.Settings.from_db(account, password))
        except Exception as exc:
            log.warning("IMAP for the sent folder of %s not available: %s", account["email"], exc)
            self.imap = None

    def send(self, msg: EmailMessage) -> None:
        try:
            smtp.send(self.server, msg)
        except smtplib.SMTPServerDisconnected:
            self.server = smtp.connect(self.smtp_cfg)
            smtp.send(self.server, msg)

    def close(self) -> None:
        try:
            self.server.quit()
        except Exception:
            pass
        if self.imap:
            self.imap.close()


class Sender:
    """Keeps one connection per account open for several mails. Use in background threads.

    Checks the passwords of all accounts needed up front (MissingPassword) and connects, so that
    sending does not stop half-way.
    """

    def __init__(self, event: dict, person_ids: list[int]):
        self.event = event
        self.routes = route(event, person_ids)
        needed = {a["id"]: a for a in self.routes.values() if a}
        passwords = {aid: accounts.password(a) for aid, a in needed.items()}
        self.connections: dict[int, _Connection] = {}
        try:
            for aid, account in needed.items():
                self.connections[aid] = _Connection(account, passwords[aid])
        except Exception:
            self.close()
            raise

    def send(self, kind: str, person_id: int, subject: str | None = None,
             content_html: str | None = None) -> str | None:
        """Sends one mail. Returns None on success, otherwise the error message."""
        person = db.query_one("SELECT * FROM person WHERE id = ?", (person_id,))
        account = self.routes.get(person_id) or account_for(person_id, self.event["id"])
        if not account or account["id"] not in self.connections:
            return t("sending.no_account", email=person["email"])
        conn = self.connections[account["id"]]
        reg = db.query_one("SELECT * FROM registration WHERE person_id = ? AND event_id = ?",
                           (person_id, self.event["id"]))
        companions = reg["companions"] if reg else 0
        try:
            msg, original = build(kind, self.event, person, companions, conn.settings, subject, content_html)
        except templates.RenderError as exc:
            log.warning("Mail to %s not created: %s", person["email"], exc)
            return f"{person['email']}: {exc}"
        except Exception as exc:  # must never silently stop sending to the others
            log.exception("Mail to %s could not be created", person["email"])
            return t("sending.build_failed", email=person["email"], error=f"{type(exc).__name__}: {exc}")
        try:
            conn.send(msg)
        except Exception as exc:
            log.warning("Sending to %s via %s failed: %s", person["email"], account["email"], exc)
            self._log(person, account, kind, msg, str(exc))
            return f"{person['email']}: {exc}"

        self._log(person, account, kind, msg, "")
        with db.connect() as con:
            con.execute("UPDATE registration SET reply_status = ?, replied_at = ? WHERE person_id = ? "
                        "AND event_id = ?", (REPLY_STATUS[kind], db.now(), person_id, self.event["id"]))
            if original:
                con.execute("UPDATE mail SET answered = 1 WHERE id = ?", (original["id"],))
        if conn.imap:
            try:
                if account.get("sent_folder"):
                    conn.imap.append_sent(account["sent_folder"], msg.as_bytes())
                if original and original.get("account_id") == account["id"]:
                    conn.imap.mark_answered(original["folder"], original["uid"], original["uidvalidity"])
            except Exception as exc:  # the mail has been sent anyway
                log.warning("Storing in sent folder / flagging failed: %s", exc)
        return None

    def _log(self, person: dict, account: dict, kind: str, msg: EmailMessage, error: str) -> None:
        db.execute("INSERT INTO sent_mail (event_id, person_id, email, kind, subject, message_id, sent_at, "
                   "error, account_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (self.event["id"], person["id"], person["email"], kind, msg["Subject"],
                    "" if error else msg["Message-ID"], db.now(), error, account["id"]))

    def close(self) -> None:
        for conn in self.connections.values():
            conn.close()


def send_test(kind: str, event: dict, account_id: int | None = None) -> str:
    """Sends a test mail of the template to the own address. Returns that address."""
    account = accounts.get_or_primary(account_id)
    password = accounts.password(account)
    settings = accounts.with_globals(account)
    me = {"id": -1, "email": account["email"], "first_name": account.get("sender_name", "").split(" ")[0],
          "last_name": ""}
    msg, _ = build(kind, event, me, 1, settings)
    msg.replace_header("Subject", t("sending.test_prefix") + msg["Subject"])
    server = smtp.connect(smtp.Settings.from_db(account, password))
    try:
        smtp.send(server, msg)
    finally:
        server.quit()
    return account["email"]
