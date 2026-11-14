"""IMAP access to the mailbox (the same account as in Thunderbird)."""
from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from datetime import date, datetime

from imap_tools import AND, MailBox, MailBoxStartTls, MailBoxUnencrypted, MailMessageFlags

from .parse import html_to_text

TIMEOUT = 30


@dataclass
class Settings:
    host: str
    port: int
    security: str
    username: str
    password: str

    @classmethod
    def from_db(cls, s: dict, password: str) -> Settings:
        return cls(s.get("imap_host", ""), int(s.get("imap_port") or 993),
                   s.get("imap_security", "ssl"), s.get("login") or s.get("email", ""), password)


@dataclass
class Message:
    uid: str
    message_id: str
    from_email: str
    from_name: str
    subject: str
    date: datetime
    text: str
    in_reply_to: str = ""
    references: str = ""
    recipients: list[str] = field(default_factory=list)
    auto_submitted: bool = False
    account: str = ""  # mailbox the mail came from (set by the caller)


def connect(cfg: Settings, folder: str | None = "INBOX"):
    ctx = ssl.create_default_context()
    if cfg.security == "ssl":
        box = MailBox(cfg.host, cfg.port, timeout=TIMEOUT, ssl_context=ctx)
    elif cfg.security == "starttls":
        box = MailBoxStartTls(cfg.host, cfg.port, timeout=TIMEOUT, ssl_context=ctx)
    else:
        box = MailBoxUnencrypted(cfg.host, cfg.port, timeout=TIMEOUT)
    if (cfg.username + cfg.password).isascii():
        return box.login(cfg.username, cfg.password, initial_folder=folder)
    # IMAP LOGIN only allows ASCII (e.g. no "§" in the password) -> AUTHENTICATE PLAIN with UTF-8
    return box.login_utf8(cfg.username, cfg.password, initial_folder=folder)


def list_folders(cfg: Settings) -> list[dict]:
    with connect(cfg, None) as box:
        return [{"name": f.name, "flags": [fl.lower() for fl in f.flags]} for f in box.folder.list()]


def suggest_folders(folders: list[dict]) -> tuple[str, str]:
    names = [f["name"] for f in folders]
    inbox = next((n for n in names if n.upper() == "INBOX"), names[0] if names else "INBOX")
    sent = next((f["name"] for f in folders if "\\sent" in f["flags"]), None)
    if not sent:
        # Common names of the sent folder, also localized ones.
        candidates = ("sent", "gesendet", "sent items", "gesendete objekte", "sent messages",
                      "inbox.sent", "inbox.gesendet")
        sent = next((n for n in names if n.lower().split("/")[-1].split(".")[-1] in candidates
                     or n.lower() in candidates), "Sent")
    return inbox, sent


def _header(msg, name: str) -> str:
    """Header value on one line. Long headers (e.g. References) are often folded over several
    lines; with line breaks they could not be copied into a reply."""
    values = msg.headers.get(name.lower(), ())
    return " ".join(" ".join(v.split()) for v in values)


def _convert(msg, with_body: bool = True) -> Message:
    text = ""
    if with_body:
        text = msg.text or (html_to_text(msg.html) if msg.html else "")
    from_value = msg.from_values
    recipients = [a.email.lower() for a in (*msg.to_values, *msg.cc_values, *msg.bcc_values) if a.email]
    auto = _header(msg, "auto-submitted").lower()
    return Message(
        uid=msg.uid or "",
        message_id=_header(msg, "message-id").strip() or f"<uid-{msg.uid}@local>",
        from_email=(from_value.email if from_value else msg.from_).lower(),
        from_name=from_value.name if from_value else "",
        subject=msg.subject or "",
        date=msg.date.replace(tzinfo=None) if msg.date else datetime.now(),
        text=text,
        in_reply_to=_header(msg, "in-reply-to"),
        references=_header(msg, "references"),
        recipients=recipients,
        auto_submitted=bool(auto and auto != "no") or bool(_header(msg, "list-unsubscribe")),
    )


def uidvalidity(box, folder: str) -> str:
    return str(box.folder.status(folder, ["UIDVALIDITY"]).get("UIDVALIDITY", ""))


def fetch_since(cfg: Settings, folder: str, since: date, known_ids: set[str]) -> tuple[str, list[Message]]:
    """Fetches all unknown mails since a date (without marking them as read)."""
    with connect(cfg, folder) as box:
        validity = uidvalidity(box, folder)
        headers = list(box.fetch(AND(date_gte=since), headers_only=True, mark_seen=False, bulk=200))
        new_uids = [m.uid for m in headers if _header(m, "message-id").strip() not in known_ids]
        if not new_uids:
            return validity, []
        msgs = [_convert(m) for m in box.fetch(uid_list=new_uids, mark_seen=False, bulk=50)]
    return validity, msgs


def recent_sent(cfg: Settings, folder: str, limit: int = 30) -> list[Message]:
    """The most recently sent mails (to pick the newsletter)."""
    with connect(cfg, folder) as box:
        msgs = box.fetch(limit=limit, reverse=True, headers_only=True, mark_seen=False, bulk=True)
        return sorted((_convert(m, with_body=False) for m in msgs), key=lambda m: m.date, reverse=True)


def sent_recipients(cfg: Settings, folder: str, since: date | None = None,
                    skip_ids: set[str] = frozenset()) -> set[str]:
    """All recipient addresses in the sent folder (for the status "known")."""
    criteria = AND(date_gte=since) if since else "ALL"
    found: set[str] = set()
    with connect(cfg, folder) as box:
        for m in box.fetch(criteria, headers_only=True, mark_seen=False, bulk=500):
            if _header(m, "message-id").strip() in skip_ids:
                continue
            found.update(a.email.lower() for a in (*m.to_values, *m.cc_values, *m.bcc_values) if a.email)
    return found


class Session:
    """One open IMAP connection for several actions (e.g. when sending to many people)."""

    def __init__(self, cfg: Settings):
        self.box = connect(cfg, None)

    def append_sent(self, folder: str, raw: bytes) -> None:
        self.box.append(raw, folder, dt=None, flag_set=[MailMessageFlags.SEEN])

    def mark_answered(self, folder: str, uid: str, validity: str) -> bool:
        """Sets \\Answered on the original mail if its UID is still valid."""
        if not uid:
            return False
        self.box.folder.set(folder)
        if validity and uidvalidity(self.box, folder) != validity:
            return False
        self.box.flag(uid, MailMessageFlags.ANSWERED, True)
        return True

    def close(self) -> None:
        try:
            self.box.logout()
        except Exception:
            pass
