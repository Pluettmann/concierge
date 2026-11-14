"""Prepare mail texts: remove quotes, detect names, convert HTML to text.

The patterns cover German and English mail clients, because replies arrive in both languages.
"""
from __future__ import annotations

import html
import re

# Lines that introduce a quoted mail in common mail clients.
_QUOTE_HEADERS = [
    r"^\s*Am .{3,120}(schrieb|wrote).{0,80}:\s*$",
    r"^\s*On .{3,120}wrote:\s*$",
    r"^\s*-{2,}\s*(Ursprüngliche Nachricht|Original Message|Weitergeleitete Nachricht|Forwarded message)\s*-{2,}",
    r"^\s*(Von|From):\s.+$",
    r"^\s*_{10,}\s*$",
]
_QUOTE_RE = re.compile("|".join(_QUOTE_HEADERS), re.IGNORECASE | re.MULTILINE)

_GREETINGS = (
    r"(viele|liebe|beste|herzliche|freundliche|sonnige|schöne)\s+grüße|"
    r"(lg|vg|mfg|bg)|grüße|gruß|liebe grüße|bis dann|bis bald|cheers|best|tschüss|ciao"
)
_GREETING_RE = re.compile(rf"^\s*({_GREETINGS})\b[\s,!.]*(?P<rest>.*)$", re.IGNORECASE)
_NAME_RE = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]+(?:[- ][A-ZÄÖÜ][a-zäöüß]+){0,2}$")


def strip_quotes(text: str) -> str:
    """Cuts off the quoted part of a reply and removes '>' lines."""
    if not text:
        return ""
    match = _QUOTE_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    out = []
    for ln in lines:
        if ln.rstrip() == "--":  # signature separator "-- "
            break
        out.append(ln)
    return "\n".join(out).strip()


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style|head).*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|table)>", "\n", value)
    value = re.sub(r"(?i)<li[^>]*>", "• ", value)
    value = re.sub(r"(?i)<a [^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
                   lambda m: m.group(2) if m.group(1) in m.group(2) else f"{m.group(2)} ({m.group(1)})",
                   value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    return "\n".join(ln.strip() for ln in value.splitlines()).strip()


def name_from_greeting(text: str) -> str:
    """Finds the name after a closing ('Viele Grüße, Anna' or on the next line)."""
    lines = [ln.strip() for ln in text.splitlines()]
    for i in range(len(lines) - 1, -1, -1):
        m = _GREETING_RE.match(lines[i])
        if not m:
            continue
        rest = m.group("rest").strip(" ,!.")
        candidates = [rest] if rest else []
        candidates += [ln.strip(" ,!.") for ln in lines[i + 1:i + 3] if ln]
        for cand in candidates:
            if _NAME_RE.match(cand):
                return cand
        return ""
    return ""


def split_name(full: str) -> tuple[str, str]:
    """Splits 'Anna Muster' or 'Muster, Anna' into (first name, last name)."""
    full = re.sub(r"\s+", " ", (full or "").strip().strip('"\''))
    if not full or "@" in full:
        return "", ""
    if "," in full:
        last, first = [p.strip() for p in full.split(",", 1)]
        return first, last
    parts = full.split(" ")
    return parts[0], " ".join(parts[1:])


def name_from_address(email: str) -> str:
    local = email.split("@", 1)[0]
    parts = [p for p in re.split(r"[._\-]+", local) if p.isalpha() and len(p) > 1]
    return " ".join(p.capitalize() for p in parts[:2])


def guess_name(display_name: str, email: str, body: str) -> tuple[str, str]:
    """Determines (first name, last name) from the sender name, the closing or the address."""
    first, last = split_name(display_name)
    if first:
        return first, last
    greeting = name_from_greeting(body)
    if greeting:
        return split_name(greeting)
    return split_name(name_from_address(email))
