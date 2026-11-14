from app.mail import parse


def test_strip_quotes_german():
    text = "Bin dabei!\n\nAm Mo., 12. Okt. 2026 um 09:00 Uhr schrieb Pascal <p@x.de>:\n> Einladung"
    assert parse.strip_quotes(text) == "Bin dabei!"


def test_strip_outlook_header():
    text = "Komme gerne.\n\nVon: Pascal\nGesendet: Montag\nBetreff: Einladung"
    assert parse.strip_quotes(text) == "Komme gerne."


def test_name_from_greeting():
    assert parse.name_from_greeting("Ich komme.\n\nViele Grüße\nAnna") == "Anna"
    assert parse.name_from_greeting("Ich komme.\nLG, Max Mustermann") == "Max Mustermann"


def test_guess_name_priorities():
    assert parse.guess_name("Anna Schmidt", "a@x.de", "") == ("Anna", "Schmidt")
    assert parse.guess_name("Schmidt, Anna", "a@x.de", "") == ("Anna", "Schmidt")
    assert parse.guess_name("", "a@x.de", "Hi\nGruß\nBerta") == ("Berta", "")
    assert parse.guess_name("", "max.mueller@x.de", "") == ("Max", "Mueller")


def test_html_to_text():
    assert parse.html_to_text("<p>Hallo&nbsp;Welt</p><p>Zeile<br>2</p>") == "Hallo Welt\nZeile\n2"
