"""Lock file in the data folder: records who opened the data last.

If the file is still there after a crash, the next start shows a hint.
"""
from __future__ import annotations

import json
import platform
from datetime import datetime
from pathlib import Path

from . import config
from .i18n import format_date, t

LOCK_NAME = "concierge.lock"


def _file() -> Path | None:
    data_dir = config.data_dir()
    return data_dir / LOCK_NAME if data_dir else None


def acquire() -> str | None:
    """Creates the lock file. Returns a warning if an old lock was still there."""
    lock = _file()
    if not lock:
        return None
    warning = None
    if lock.is_file():
        try:
            other = json.loads(lock.read_text(encoding="utf-8"))
            warning = t("lock.not_closed", system=other.get("system"), host=other.get("host"),
                        when=format_date(other["since"], "day_time"))
        except (OSError, ValueError, KeyError):
            pass
    me = {"host": platform.node(), "system": platform.system(), "since": datetime.now().isoformat(timespec="seconds")}
    try:
        lock.write_text(json.dumps(me), encoding="utf-8")
    except OSError as exc:
        return t("lock.not_writable", error=exc)
    return warning


def release() -> None:
    lock = _file()
    if lock:
        lock.unlink(missing_ok=True)
