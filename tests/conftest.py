import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Never touch the real per-user configuration while testing.
os.environ.setdefault("CONCIERGE_CONFIG_DIR", str(ROOT / ".pytest_cache" / "config"))

from app import db, i18n  # noqa: E402

i18n.set_language("de")


@pytest.fixture
def database(tmp_path):
    from app.services import accounts
    db.open_db(tmp_path)
    accounts.save({"email": "ich@example.org", "sender_name": "Pascal Beispiel",
                   "signature": "Viele Grüße\nPascal", "inbox_folder": "INBOX", "sent_folder": "Sent"})
    yield tmp_path


@pytest.fixture
def event(database):
    from app.services import events
    eid = events.create({
        "title": "Herbstabend", "date": "2026-11-14", "start_time": "19:00", "end_time": "22:00",
        "venue": "Gemeindehaus", "address": "Hauptstr. 1, 12345 Musterstadt",
        "map_link": "https://maps.example/x", "info": "Bitte etwas zu trinken mitbringen.",
        "newsletter_message_id": "<news-1@example.org>", "newsletter_subject": "Einladung: Herbstabend am 14.11.",
        "search_from": "2026-10-01T00:00:00",
    })
    return events.get(eid)
