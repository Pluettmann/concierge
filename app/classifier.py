"""Rule-based classification of incoming mails.

The result is only a suggestion: every mail is confirmed or corrected in the UI. The score (0-100)
says how confident the suggestion is. The patterns recognise German (and a little English) replies,
because that is the language the invitations are answered in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

REGISTRATION = "registration"
CANCELLATION = "cancellation"
OTHER = "other"

_POSITIVE = [
    r"\bich komme\b", r"\bkomme gerne?\b", r"\bkomme (auch|mit|vorbei)\b", r"\bwir kommen\b",
    r"\b(bin|sind)\b[^!?\n]{0,30}\bdabei\b", r"\bbin (gerne|gern) dabei\b", r"\bgerne? dabei\b",
    r"\bmelde (mich|uns) (hiermit )?(gerne? )?an\b", r"\bmelden? (ich |wir )?(mich|uns)\b[^!?\n]{0,60}\ban\b",
    r"\b(würde|möchte|will|wollen|würden|möchten)\b[^!?\n]{0,25}\b(kommen|teilnehmen|dabei sein|vorbeikommen)\b",
    r"\bgerne? (kommen|teilnehmen|vorbeikommen)\b", r"\banmeld(en|ung)\b", r"\bzusage\b",
    r"\bsage zu\b", r"\bich bin da\b", r"\bwir sind da\b", r"\bbin am start\b",
    r"\bich nehme teil\b", r"\bteilnehmen\b", r"\bdabei sein\b", r"\bkomme\b", r"\bkommen gerne?\b",
    r"\bcount me in\b", r"\bi'?ll (be there|come)\b", r"\bwe'?ll (be there|come)\b",
]
_NEGATIVE = [
    r"\b(kann|können|könnte) (leider )?(doch )?nicht\b", r"\bleider nicht\b", r"\bkomme? (leider )?(doch )?nicht\b",
    r"\bkommen (leider )?(doch )?nicht\b", r"\babsagen?\b", r"\bsage ab\b",
    r"\bmelde (mich|uns) (leider )?(wieder )?ab\b",
    r"\babmeld(en|ung)\b", r"\bschaffe es (leider )?nicht\b", r"\bverhindert\b", r"\bklappt (leider )?nicht\b",
    r"\bbin raus\b", r"\bmuss (leider )?passen\b", r"\bnicht dabei\b", r"\bcan'?t (make it|come)\b",
    r"\bwird (leider )?nichts\b",
]
_POS_RE = [re.compile(p, re.IGNORECASE) for p in _POSITIVE]
_NEG_RE = [re.compile(p, re.IGNORECASE) for p in _NEGATIVE]

_NUMBERS = {
    "ein": 1, "eine": 1, "einer": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "acht": 8,
}
_GROUP_WORDS = {"zweit": 2, "dritt": 3, "viert": 4, "fünft": 5, "sechst": 6}
_COMPANIONS = (
    r"frau|mann|partner(in)?|freund(in)?|begleitung|tochter|sohn|kind|"
    r"kollegin|kollegen?|schwester|bruder|mutter|vater|mama|papa|nachbar(in)?|ehefrau|ehemann|"
    r"lebensgefährt(e|in)|verlobte(n)?"
)
_COMPANION_RE = re.compile(
    rf"\bmit (meiner|meinem|meinen|unserer|unserem|einer|einem|\w+)\s+(\w+\s+)?({_COMPANIONS})\b",
    re.IGNORECASE,
)
_MONTHS = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
           "september", "oktober", "november", "dezember"]
_SUBJECT_PREFIX = re.compile(r"^\s*((re|aw|wg|fw|fwd|antw)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)


def reason(key: str, **args) -> dict:
    """A reason for a suggestion: a translation key plus its arguments (see i18n.reasons_text)."""
    return {"key": key, "args": args} if args else {"key": key}


@dataclass
class Result:
    suggestion: str
    score: int
    companions: int = 0
    reasons: list[dict] = field(default_factory=list)


def normalize_subject(subject: str) -> str:
    return _SUBJECT_PREFIX.sub("", subject or "").strip().casefold()


def _ids(value: str) -> set[str]:
    return {m.strip("<>").lower() for m in re.findall(r"<[^>]+>", value or "")} | (
        {value.strip("<> ").lower()} if value and "<" not in value else set()
    )


def _mentions_date(text: str, day: date) -> bool:
    patterns = [
        rf"\b0?{day.day}\.\s?0?{day.month}\.",
        rf"\b0?{day.day}\.\s?{_MONTHS[day.month - 1]}",
        rf"\b0?{day.day}\.\s?{_MONTHS[day.month - 1][:3]}\b",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _mentions_title(text: str, title: str) -> bool:
    title = title.casefold().strip()
    if not title:
        return False
    if title in text:
        return True
    words = re.findall(r"\w{4,}", title)
    return bool(words) and sum(w in text for w in words) >= max(1, (len(words) + 1) // 2)


def count_companions(text: str) -> tuple[int, dict | None]:
    """Detects the number of companions; returns (count, reason or None)."""
    t = text.casefold()
    m = re.search(r"\+\s?(\d)\b", t)
    if m:
        return int(m.group(1)), reason("reason.companions_quote", phrase=f"+{m.group(1)}")
    m = re.search(r"\bzu (" + "|".join(_GROUP_WORDS) + r")\b", t)
    if m:
        return _GROUP_WORDS[m.group(1)] - 1, reason("reason.companions_quote", phrase=f"zu {m.group(1)}")
    m = re.search(r"\b(?:wir sind|wir kommen zu|zu|insgesamt|mit)\s+(\d|" + "|".join(_NUMBERS)
                  + r")\s*(personen|leuten|leute|erwachsenen)\b", t)
    if not m:
        m = re.search(r"\bwir kommen zu (\d)\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMBERS[m.group(1)]
        return max(n - 1, 0), reason("reason.companions_people", count=n)
    m = re.search(r"\bmit (\d|zwei|drei|vier|fünf)\s+(freunden|freundinnen|kollegen|kolleginnen|leuten|personen"
                  r"|kindern|gästen|begleitpersonen)\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMBERS[m.group(1)]
        return n, reason("reason.companions_quote", phrase=f"mit {m.group(1)} {m.group(2)}")
    if _COMPANION_RE.findall(t):
        return len(_COMPANION_RE.findall(t)), reason("reason.companions_mentioned")
    if re.search(r"\b(wir kommen|wir sind dabei|kommen wir|melden uns an)\b", t):
        return 1, reason("reason.companions_we")
    return 0, None


def classify(subject: str, body: str, in_reply_to: str, refs: str, event: dict,
             has_registration: bool = False) -> Result:
    text = f"{subject}\n{body}".casefold()
    body_cf = (body or "").casefold()
    reasons: list[dict] = []
    relevance = 0

    newsletter_id = (event.get("newsletter_message_id") or "").strip("<> ").lower()
    if newsletter_id and newsletter_id in (_ids(in_reply_to) | _ids(refs)):
        relevance += 50
        reasons.append(reason("reason.reply_to_newsletter"))
    elif event.get("newsletter_subject") and normalize_subject(subject) == normalize_subject(
            event["newsletter_subject"]):
        relevance += 35
        reasons.append(reason("reason.newsletter_subject"))
    if _mentions_title(text, event.get("title", "")):
        relevance += 25
        reasons.append(reason("reason.event_mentioned"))
    try:
        if _mentions_date(text, date.fromisoformat(event.get("date", ""))):
            relevance += 15
            reasons.append(reason("reason.date_mentioned"))
    except ValueError:
        pass

    neg = [r.search(body_cf) or r.search(normalize_subject(subject)) for r in _NEG_RE]
    neg = [m.group(0) for m in neg if m]
    pos = [r.search(body_cf) or r.search(normalize_subject(subject)) for r in _POS_RE]
    pos = [m.group(0) for m in pos if m]

    companions, companions_reason = count_companions(body_cf)

    if neg and (relevance or has_registration):
        reasons.append(reason("reason.negative", phrase=neg[0]))
        return Result(CANCELLATION, min(100, 40 + relevance), 0, reasons)

    if pos and not neg:
        reasons.append(reason("reason.positive", phrase=pos[0]))
        if companions_reason:
            reasons.append(companions_reason)
        score = min(100, relevance + 30 + 10 * min(len(pos) - 1, 2))
        if relevance == 0:
            score = min(score, 45)
            reasons.append(reason("reason.no_clear_relation"))
        return Result(REGISTRATION, score, companions, reasons)

    if relevance >= 50 and len(body_cf.strip()) < 300 and not neg:
        reasons.append(reason("reason.short_reply"))
        return Result(REGISTRATION, 35, companions, reasons)

    if neg:
        reasons.append(reason("reason.negative_unrelated", phrase=neg[0]))
    return Result(OTHER, max(0, 50 - relevance), 0, reasons or [reason("reason.no_relation")])


# --- Which event a mail belongs to -----------------------------------------------------------

@dataclass
class Assignment:
    event: dict | None
    reasons: list[dict] = field(default_factory=list)
    uncertain: bool = False


def _replies_to(event: dict, in_reply_to: str, refs: str) -> bool:
    newsletter_id = (event.get("newsletter_message_id") or "").strip("<> ").lower()
    return bool(newsletter_id) and newsletter_id in (_ids(in_reply_to) | _ids(refs))


def _same_subject(event: dict, subject: str) -> bool:
    return bool(event.get("newsletter_subject")) and normalize_subject(subject) == normalize_subject(
        event["newsletter_subject"])


def _mentions(event: dict, text: str) -> bool:
    if _mentions_title(text, event.get("title", "")):
        return True
    try:
        return _mentions_date(text, date.fromisoformat(event.get("date", "")))
    except ValueError:
        return False


def nearest(events: list[dict], day: date) -> dict | None:
    """The event closest to a day, preferring events that have not taken place yet."""
    if not events:
        return None
    upcoming = [e for e in events if e["date"] >= day.isoformat()]
    if upcoming:
        return min(upcoming, key=lambda e: e["date"])
    return max(events, key=lambda e: e["date"])


def assign_event(subject: str, body: str, in_reply_to: str, refs: str, events: list[dict],
                 received: date) -> Assignment:
    """Picks the event among the active ones that a mail most likely refers to.

    1. A reply to an event's newsletter (In-Reply-To/References, else the same subject).
    2. Title or date of an event in the subject or body.
    3. Otherwise the nearest event, marked as uncertain.
    One newsletter can belong to several events; then step 2 decides between them.
    """
    if not events:
        return Assignment(None)
    if len(events) == 1:
        return Assignment(events[0])
    candidates = [e for e in events if _replies_to(e, in_reply_to, refs)] or \
        [e for e in events if _same_subject(e, subject)] or events
    if len(candidates) == 1:
        return Assignment(candidates[0])
    text = f"{subject}\n{body}".casefold()
    mentioned = [e for e in candidates if _mentions(e, text)]
    if len(mentioned) == 1:
        return Assignment(mentioned[0])
    guess = nearest(mentioned or candidates, received)
    return Assignment(guess, [reason("reason.event_guessed")], uncertain=True)
