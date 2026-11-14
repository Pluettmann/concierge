"""Find Thunderbird profiles and read their address books (read-only).

Since version 78, Thunderbird stores address books as SQLite files (abook.sqlite,
history.sqlite, abook-N.sqlite). Thunderbird may keep them open, so a copy is read.
"""
from __future__ import annotations

import configparser
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import t


@dataclass
class Profile:
    name: str
    path: Path
    is_default: bool = False


@dataclass
class Contact:
    email: str
    display_name: str = ""
    first_name: str = ""
    last_name: str = ""


@dataclass
class AddressBook:
    file: str          # file name in the profile, e.g. "abook.sqlite"
    name: str          # display name, e.g. "Personal Address Book"
    contacts: list[Contact] = field(default_factory=list)
    lists: dict[str, list[str]] = field(default_factory=dict)  # list name -> e-mail addresses


def base_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "Thunderbird"] if appdata else []
    return [
        home / ".thunderbird",
        home / "snap" / "thunderbird" / "common" / ".thunderbird",
        home / ".var" / "app" / "org.mozilla.Thunderbird" / ".thunderbird",
    ]


def find_profiles() -> list[Profile]:
    profiles: list[Profile] = []
    for base in base_dirs():
        ini = base / "profiles.ini"
        if not ini.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(ini, encoding="utf-8")
        install_defaults = {
            parser[s].get("Default") for s in parser.sections() if s.startswith("Install")
        }
        for section in parser.sections():
            if not section.startswith("Profile"):
                continue
            sec = parser[section]
            raw = sec.get("Path", "")
            path = base / raw if sec.get("IsRelative", "1") == "1" else Path(raw)
            if path.is_dir():
                profiles.append(Profile(
                    name=sec.get("Name", path.name),
                    path=path,
                    is_default=raw in install_defaults or sec.get("Default") == "1",
                ))
    profiles.sort(key=lambda p: not p.is_default)
    return profiles


def _book_names(profile: Path) -> dict[str, str]:
    """Reads the display names of the address books from prefs.js."""
    names = {"abook.sqlite": t("thunderbird.personal_book"), "history.sqlite": t("thunderbird.collected_addresses")}
    prefs = profile / "prefs.js"
    if not prefs.is_file():
        return names
    text = prefs.read_text(encoding="utf-8", errors="replace")
    pref = dict(re.findall(r'user_pref\("([^"]+)",\s*"((?:[^"\\]|\\.)*)"\);', text))
    for key, value in pref.items():
        m = re.fullmatch(r"ldap_2\.servers\.([^.]+)\.filename", key)
        if m:
            desc = pref.get(f"ldap_2.servers.{m.group(1)}.description")
            if desc:
                names[value] = desc
    return names


def _read_book(file: Path) -> tuple[list[Contact], dict[str, list[str]]]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / file.name
        shutil.copy2(file, copy)
        con = sqlite3.connect(copy)
        try:
            cards: dict[str, dict[str, str]] = {}
            for card, name, value in con.execute("SELECT card, name, value FROM properties"):
                cards.setdefault(card, {})[name] = value or ""
            contacts = []
            for props in cards.values():
                for key in ("PrimaryEmail", "SecondEmail"):
                    email = props.get(key, "").strip().lower()
                    if email:
                        contacts.append(Contact(
                            email=email,
                            display_name=props.get("DisplayName", ""),
                            first_name=props.get("FirstName", ""),
                            last_name=props.get("LastName", ""),
                        ))
            lists: dict[str, list[str]] = {}
            try:
                rows = con.execute(
                    "SELECT l.name, lc.card FROM lists l JOIN list_cards lc ON lc.list = l.uid"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for list_name, card in rows:
                email = cards.get(card, {}).get("PrimaryEmail", "").strip().lower()
                if email:
                    lists.setdefault(list_name, []).append(email)
            return contacts, lists
        finally:
            con.close()


def read_address_books(profile: Path) -> list[AddressBook]:
    names = _book_names(profile)
    books = []
    for file in sorted(profile.glob("*.sqlite")):
        if not (file.name.startswith("abook") or file.name == "history.sqlite"):
            continue
        try:
            contacts, lists = _read_book(file)
        except (sqlite3.DatabaseError, OSError):
            continue
        books.append(AddressBook(file=file.name, name=names.get(file.name, file.stem),
                                 contacts=contacts, lists=lists))
    return books
