"""Suggest server settings for an e-mail address.

Uses the same sources as Thunderbird: autoconfig.<domain>, the Mozilla ISPDB and, as a fallback,
common server names.
"""
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass

TIMEOUT = 6

SOURCE_PROVIDER = "provider"
SOURCE_ISPDB = "ispdb"
SOURCE_GUESS = "guess"


@dataclass
class ServerConfig:
    imap_host: str = ""
    imap_port: int = 993
    imap_security: str = "ssl"       # ssl | starttls | none
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    username: str = ""               # login (usually the e-mail address)
    source: str = ""                 # where the suggestion came from (SOURCE_*)

    def as_dict(self) -> dict:
        return asdict(self)


_SECURITY = {"SSL": "ssl", "STARTTLS": "starttls", "plain": "none"}


def _parse(xml: bytes, email: str) -> ServerConfig | None:
    root = ET.fromstring(xml)
    provider = root.find("emailProvider")
    if provider is None:
        return None
    local, domain = email.split("@", 1)
    subst = {"%EMAILADDRESS%": email, "%EMAILLOCALPART%": local, "%EMAILDOMAIN%": domain}

    def fill(value: str) -> str:
        for key, repl in subst.items():
            value = value.replace(key, repl)
        return value

    cfg = ServerConfig()
    imap = next((s for s in provider.findall("incomingServer") if s.get("type") == "imap"), None)
    smtp = next((s for s in provider.findall("outgoingServer") if s.get("type") == "smtp"), None)
    if imap is None or smtp is None:
        return None
    cfg.imap_host = fill(imap.findtext("hostname", ""))
    cfg.imap_port = int(imap.findtext("port", "993"))
    cfg.imap_security = _SECURITY.get(imap.findtext("socketType", "SSL"), "ssl")
    cfg.username = fill(imap.findtext("username", email))
    cfg.smtp_host = fill(smtp.findtext("hostname", ""))
    cfg.smtp_port = int(smtp.findtext("port", "465"))
    cfg.smtp_security = _SECURITY.get(smtp.findtext("socketType", "SSL"), "ssl")
    return cfg


def _fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Concierge/2"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except Exception:
        return None


def discover(email: str) -> ServerConfig:
    email = email.strip()
    domain = email.split("@", 1)[1].lower()
    sources = [
        (f"https://autoconfig.{domain}/mail/config-v1.1.xml?emailaddress={email}", SOURCE_PROVIDER),
        (f"https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml", SOURCE_PROVIDER),
        (f"https://autoconfig.thunderbird.net/v1.1/{domain}", SOURCE_ISPDB),
    ]
    # Ask all sources in parallel; wait at most TIMEOUT seconds in total.
    pool = ThreadPoolExecutor(max_workers=len(sources))
    futures = [pool.submit(_fetch, url) for url, _ in sources]
    wait(futures, timeout=TIMEOUT)
    pool.shutdown(wait=False, cancel_futures=True)
    for future, (_, source) in zip(futures, sources, strict=True):
        data = future.result() if future.done() else None
        if not data:
            continue
        try:
            cfg = _parse(data, email)
        except ET.ParseError:
            cfg = None
        if cfg:
            cfg.source = source
            return cfg
    return ServerConfig(imap_host=f"imap.{domain}", smtp_host=f"smtp.{domain}", username=email,
                        source=SOURCE_GUESS)
