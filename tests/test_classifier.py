import pytest

from app import classifier
from app.classifier import CANCELLATION, OTHER, REGISTRATION
from app.mail.parse import strip_quotes

EVENT = {"title": "Herbstabend", "date": "2026-11-14", "newsletter_message_id": "<news-1@example.org>",
         "newsletter_subject": "Einladung: Herbstabend am 14.11."}


def run(subject, body, in_reply_to="", refs="", has_reg=False):
    return classifier.classify(subject, strip_quotes(body), in_reply_to, refs, EVENT, has_reg)


def test_reply_to_newsletter_with_quote():
    body = "Hallo,\nich komme gerne!\n\nLG Anna\n\nAm 01.10.2026 um 10:00 schrieb Pascal:\n> Ich komme nicht"
    r = run("Re: Einladung: Herbstabend am 14.11.", body, "<news-1@example.org>")
    assert r.suggestion == REGISTRATION
    assert r.score >= 80
    assert {"key": "reason.reply_to_newsletter"} in r.reasons


def test_new_mail_without_history():
    r = run("Herbstabend", "Hi, ich bin am 14.11. dabei.")
    assert r.suggestion == REGISTRATION
    assert r.score >= 60


@pytest.mark.parametrize("body,expected", [
    ("Wir kommen zu zweit!", 1),
    ("Ich komme +2", 2),
    ("Ich bin dabei und komme mit meiner Frau.", 1),
    ("Wir sind 4 Personen", 3),
    ("Ich komme mit zwei Freunden", 2),
    ("Ich komme", 0),
])
def test_companions(body, expected):
    r = run("Re: Einladung: Herbstabend am 14.11.", body, "<news-1@example.org>")
    assert r.suggestion == REGISTRATION
    assert r.companions == expected


@pytest.mark.parametrize("body", [
    "Hallo, Anna hat mir vom Herbstabend erzählt. Ich würde gerne kommen!",
    "Hiermit melde ich mich mit meiner Frau zum Herbstabend an.",
    "Wir möchten am Herbstabend teilnehmen.",
])
def test_other_phrasings(body):
    assert run("Frage", body).suggestion == REGISTRATION


def test_would_like_but_cannot():
    assert run("Re: Herbstabend", "Ich würde gerne kommen, kann aber leider nicht.").suggestion == CANCELLATION


def test_cancellation():
    r = run("Re: Herbstabend", "Ich kann leider doch nicht kommen.", has_reg=True)
    assert r.suggestion == CANCELLATION


def test_negation_beats_positive_word():
    r = run("Re: Einladung: Herbstabend am 14.11.", "Ich komme leider nicht.", "<news-1@example.org>")
    assert r.suggestion == CANCELLATION


def test_unrelated_mail():
    r = run("Rechnung Oktober", "Anbei die Rechnung für Oktober.")
    assert r.suggestion == OTHER


def test_short_reply_to_newsletter_is_weak_registration():
    r = run("Re: Einladung", "Ja!", "<news-1@example.org>")
    assert r.suggestion == REGISTRATION
    assert r.score < 50
