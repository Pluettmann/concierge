"""Per-system settings and the password vault.

Shared settings live in the database (see db.py). This module only holds what differs between
Windows and Linux: the data folder, the Thunderbird profile, theme, language and the mail
passwords (in the operating system's vault).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

APP_ID = "concierge"
APP_NAME = "Concierge"
KEYRING_SERVICE = "concierge-mail"

# Until version 1.x the app was called "Anmeldungen". These names are only read once to take
# over existing settings and passwords (see legacy.py).
LEGACY_APP_ID = "anmeldungen"
LEGACY_KEYRING_SERVICE = "anmeldungen-mail"

log = logging.getLogger(__name__)

# CONCIERGE_CONFIG_DIR allows a separate configuration, e.g. for tests.
_CONFIG_OVERRIDE = os.environ.get("CONCIERGE_CONFIG_DIR")
CONFIG_DIR = Path(_CONFIG_OVERRIDE or user_config_dir(APP_ID, appauthor=False))
CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_CONFIG_FILE: Path | None = (
    None if _CONFIG_OVERRIDE else Path(user_config_dir(LEGACY_APP_ID, appauthor=False)) / "lokal.json")
LOG_DIR = Path(user_log_dir(APP_ID, appauthor=False))

THEMES = ("auto", "light", "dark")

# Password kept in memory if the system vault is not available (only for this session).
_session_password: dict[str, str] = {}


def load_local() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_local(**changes) -> dict:
    data = load_local()
    data.update(changes)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def data_dir() -> Path | None:
    value = load_local().get("data_dir")
    return Path(value) if value else None


def thunderbird_profile() -> Path | None:
    value = load_local().get("tb_profile")
    return Path(value) if value else None


def language() -> str:
    return load_local().get("language", "")


def theme() -> str:
    """"auto" follows the operating system."""
    value = load_local().get("theme", "auto")
    return value if value in THEMES else "auto"


def dark_value(value: str) -> bool | None:
    """Value for Quasar/NiceGUI: None means automatic."""
    return {"light": False, "dark": True}.get(value)


def get_password(account: str) -> str | None:
    if account in _session_password:
        return _session_password[account]
    try:
        import keyring
        password = keyring.get_password(KEYRING_SERVICE, account)
        if password is None:
            password = keyring.get_password(LEGACY_KEYRING_SERVICE, account)
            if password is not None:
                keyring.set_password(KEYRING_SERVICE, account, password)
        return password
    except Exception as exc:  # no vault backend available
        log.warning("Password vault not available: %s", exc)
        return None


def set_password(account: str, password: str) -> bool:
    """Stores the password in the system vault. Returns False if that is not possible; the
    password is then only kept in memory for this session."""
    _session_password[account] = password
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, account, password)
        return True
    except Exception as exc:
        log.warning("Could not store password in the vault: %s", exc)
        return False


def remember_for_session(account: str, password: str) -> None:
    """Keeps a password for this session only (the user chose not to store it)."""
    _session_password[account] = password
