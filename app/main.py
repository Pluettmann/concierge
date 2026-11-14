"""Entry point: start the local server and open the browser.

Usage: python -m app.main  (or start.bat / start.sh)
"""
from __future__ import annotations

import os
import sys

from . import config

# Under pythonw.exe (start without a console window) there is no stdout/stderr. The web server
# writes there and would otherwise exit silently, so redirect to a file before importing it.
if sys.stdout is None or sys.stderr is None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _console = open(config.LOG_DIR / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or _console
    sys.stderr = sys.stderr or _console

import logging
import socket
import time
import webbrowser
from logging.handlers import RotatingFileHandler

from nicegui import app, run, ui

from . import db, i18n, legacy, lock, updater, version
from .i18n import t
from .ui import layout

HOST = "127.0.0.1"
PORT = 8737
URL = f"http://{HOST}:{PORT}"
# Set by version 1.x when it restarts into this version after installing the update.
LEGACY_RESTART_ENV = "ANMELDUNGEN_NEUSTART"
LEGACY_NO_BROWSER_ENV = "ANMELDUNGEN_NO_BROWSER"


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(config.LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handlers: list[logging.Handler] = [handler]
    if sys.stdout and sys.stdout.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def already_running() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def open_data() -> None:
    data_dir = config.data_dir()
    if not data_dir or not db.exists_in(data_dir):
        return
    try:
        db.backup(data_dir)
    except Exception as exc:
        logging.warning("Backup failed: %s", exc)
    db.open_db(data_dir)
    warnings = [w for w in (lock.acquire(),) if w]
    if legacy.changed_since_import(data_dir):
        warnings.append(t("startup.legacy_db_changed"))
    layout.startup_warning = " ".join(warnings) or None


def wait_for_previous(timeout: float = 60) -> None:
    """After an update the old process keeps running briefly; only then is the port free."""
    deadline = time.monotonic() + timeout
    while already_running() and time.monotonic() < deadline:
        time.sleep(0.5)


async def check_for_update() -> None:
    """Quietly checks for an update at startup; without internet nothing happens."""
    try:
        info = await run.io_bound(updater.find_latest)
    except Exception as exc:  # must never disturb the start
        logging.info("Update check skipped: %s", exc)
        return
    if info:
        logging.info("Update available: %s", info.version)


def main() -> None:
    setup_logging()
    logging.info("%s %s starting (%s)", config.APP_NAME, version.VERSION, sys.platform)
    legacy.migrate_local_config()
    i18n.set_language(config.language() or i18n.DEFAULT_LANGUAGE)
    restarted = os.environ.pop(updater.RESTART_ENV, None) or os.environ.pop(LEGACY_RESTART_ENV, None)
    if restarted:
        wait_for_previous()
    if already_running():
        webbrowser.open(URL)
        return
    open_data()
    # Register the pages.
    from .ui import events, inbox, overview, people, registration_list, settings, setup, templates  # noqa: F401
    app.on_shutdown(lock.release)
    app.on_startup(check_for_update)
    show_browser = not (os.environ.get(updater.NO_BROWSER_ENV) or os.environ.get(LEGACY_NO_BROWSER_ENV))
    ui.run(host=HOST, port=PORT, title=config.APP_NAME, language=t("quasar.language"),
           dark=config.dark_value(config.theme()), reload=False, show=show_browser, favicon="🛎️",
           show_welcome_message=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
