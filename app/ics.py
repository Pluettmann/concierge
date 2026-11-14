"""ICS calendar file for an event."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

TZ = ZoneInfo("Europe/Berlin")
DEFAULT_DURATION = timedelta(hours=2)


def _times(event: dict) -> tuple[datetime | date, datetime | date]:
    day = date.fromisoformat(event["date"])
    if not event.get("start_time"):
        return day, day + timedelta(days=1)
    start = datetime.combine(day, time.fromisoformat(event["start_time"]), TZ)
    if event.get("end_time"):
        end = datetime.combine(day, time.fromisoformat(event["end_time"]), TZ)
        if end <= start:
            end += timedelta(days=1)
    else:
        end = start + DEFAULT_DURATION
    return start, end


def build(event: dict, organizer_email: str = "", organizer_name: str = "") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Concierge//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")
    ev = Event()
    start, end = _times(event)
    # Times in UTC work in every calendar without embedded time zone data.
    if isinstance(start, datetime):
        start, end = start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC"))
    ev.add("uid", event["ics_uid"])
    ev.add("sequence", int(event.get("ics_sequence", 0)))
    ev.add("dtstamp", datetime.now(ZoneInfo("UTC")))
    ev.add("dtstart", start)
    ev.add("dtend", end)
    ev.add("summary", event["title"])
    location = ", ".join(p for p in (event.get("venue", ""), event.get("address", "")) if p)
    if location:
        ev.add("location", location)
    description = "\n\n".join(p for p in (event.get("info", ""), event.get("map_link", "")) if p)
    if description:
        ev.add("description", description)
    if event.get("map_link"):
        ev.add("url", event["map_link"])
    if organizer_email:
        ev.add("organizer", f"mailto:{organizer_email}",
               parameters={"CN": organizer_name} if organizer_name else None)
    cal.add_component(ev)
    return cal.to_ical()
