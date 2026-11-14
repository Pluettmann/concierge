"""Sending mail via SMTP."""
from __future__ import annotations

import base64
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

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
        return cls(s.get("smtp_host", ""), int(s.get("smtp_port") or 465),
                   s.get("smtp_security", "ssl"), s.get("login") or s.get("email", ""), password)


def connect(cfg: Settings) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if cfg.security == "ssl":
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=TIMEOUT, context=ctx)
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=TIMEOUT)
        server.ehlo()
        if cfg.security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
    if cfg.username and cfg.password:
        if (cfg.username + cfg.password).isascii():
            server.login(cfg.username, cfg.password)
        else:
            _login_utf8(server, cfg.username, cfg.password)
    return server


def _login_utf8(server: smtplib.SMTP, username: str, password: str) -> None:
    """AUTH PLAIN with UTF-8. smtplib.login encodes credentials as ASCII only and fails,
    for example, on a "§" in the password."""
    server.ehlo_or_helo_if_needed()
    token = base64.b64encode(b"\0" + username.encode("utf-8") + b"\0" + password.encode("utf-8"))
    code, resp = server.docmd("AUTH", "PLAIN " + token.decode("ascii"))
    if code == 334:  # the server wants the response only after an empty challenge
        code, resp = server.docmd(token.decode("ascii"))
    if code not in (235, 503):
        raise smtplib.SMTPAuthenticationError(code, resp)


def test(cfg: Settings) -> None:
    server = connect(cfg)
    server.quit()


def send(server: smtplib.SMTP, msg: EmailMessage) -> None:
    server.send_message(msg)
