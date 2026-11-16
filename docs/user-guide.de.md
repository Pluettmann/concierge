# Concierge – Anleitung

Concierge verwaltet Anmeldungen zu Veranstaltungen per Mail. Die App liest das Postfach, in dem die
Antworten auf den Einladungs-Newsletter ankommen, schlägt für jede Mail eine Einstufung vor
(Anmeldung, Abmeldung, Sonstiges) und verschickt persönliche Zusagen mit Kalenderdatei, Absagen oder
eigene Antworten.

## Starten

- **Windows:** Doppelklick auf `start.bat` (oder eine Verknüpfung dazu auf dem Desktop).
- **Linux:** `start.sh` ausführen oder `concierge.desktop` einrichten: den Pfad bei `Exec=` anpassen,
  die Datei nach `~/.local/share/applications/` kopieren und `chmod +x start.sh` ausführen.

Beim ersten Start wird automatisch eine Python-Umgebung angelegt. Danach öffnet sich der Browser
unter <http://127.0.0.1:8737>. Die App ist nur vom eigenen Rechner aus erreichbar. Beendet wird sie
über den Ein/Aus-Knopf oben rechts.

**Voraussetzungen:** Windows: Python 3.11 oder neuer von python.org, bei der Installation
„Add python.exe to PATH“ anhaken. Linux: `sudo apt install python3-venv`.

## Ablauf

1. **Neue Veranstaltung anlegen:** Titel, Datum, Uhrzeit und Ort eingeben und den gesendeten
   Newsletter auswählen. Antworten auf den Newsletter werden dann sicher erkannt; die Empfänger
   können als Mitglieder übernommen werden.
2. **Mails abrufen** (Übersicht oder Eingang).
3. **Eingang prüfen:** Vorschlag bestätigen oder korrigieren, bei Bedarf Name und Zahl der
   Begleitpersonen anpassen. Unerwünschte Absender blockieren oder ausblenden.
4. **Anmeldeliste:** nach Bekanntheit (Mitglied, bekannt, unbekannt) und Antwortstatus filtern,
   Personen auswählen und dann eine **Zusage** (mit Kalenderdatei), eine **Absage** oder eine
   **persönliche** Antwort schicken.
5. Nach der Veranstaltung: *Veranstaltungen → Abschließen*. Eine Woche nach dem Datum passiert das
   auch automatisch.

## Mehrere Veranstaltungen gleichzeitig

Es können mehrere Veranstaltungen aktiv sein, z. B. die im nächsten und die im übernächsten Monat.
Eine neue Veranstaltung archiviert die bisherigen nicht mehr.

- **Zuordnung beim Abrufen:** Eine Antwort auf den Newsletter einer Veranstaltung landet sicher bei
  ihr. Sonst entscheiden Titel oder Datum in der Mail. Ist auch das nicht eindeutig, wird die
  nächste Veranstaltung vorgeschlagen und die Mail mit „Bitte prüfen“ markiert.
- **Eingang:** Er zeigt die neuen Mails aller Veranstaltungen zusammen. Auf jeder Karte steht die
  Veranstaltung und lässt sich mit einem Klick ändern. Bei „Ich komme zu beiden“ einfach beide
  wählen und dann *Anmeldung* klicken.
- **Übersicht und Anmeldeliste:** Oben wählst du, welche Veranstaltung du siehst. Zusage-Mail und
  Kalenderdatei enthalten die Daten dieser Veranstaltung.
- **Ein Newsletter für mehrere Termine:** Er kann bei mehreren Veranstaltungen gewählt werden. Dann
  entscheiden Titel und Datum in der Antwort.

Jede Antwort wird im Gesendet-Ordner abgelegt, und die Originalmail wird als „beantwortet“
markiert. In Thunderbird ist dadurch alles wie gewohnt sichtbar.

**Bekanntheit**

- *Mitglied*: steht in der gewählten Thunderbird-Liste, war Empfänger des Newsletters oder wurde
  von Hand markiert.
- *Bekannt*: steht im Thunderbird-Adressbuch, hat schon einmal eine Mail von dir bekommen oder war
  bei einer früheren (zeitlich davor liegenden) Veranstaltung angemeldet.
- *Unbekannt*: alle anderen.

## Mehrere Postfächer

Unter *Einstellungen → Postfach hinzufügen* lassen sich weitere Postfächer einrichten. „Mails
abrufen“ holt aus allen. Antworten gehen von dem Postfach aus, in dem die letzte Mail der Person
ankam; wer ohne Mail eingetragen wurde, bekommt die Antwort vom Hauptpostfach (dem ersten in der
Liste). Wird ein Postfach entfernt, bleiben bereits abgerufene Mails und Anmeldungen erhalten.

## Updates

Beim Start sieht die App still nach, ob es eine neue Version gibt. Gibt es eine, steht neben
*Einstellungen* der Hinweis „Update“. Unter *Einstellungen → Version & Update* auf „Herunterladen &
installieren“ klicken. Die App prüft das Update (Signatur des Entwicklers), zeigt die Änderungen an,
sichert die bisherige Version nach `.updates/` und startet neu. Daten, Einstellungen und Passwörter
bleiben unverändert.

Ohne Internet geht es mit der Datei `concierge-update-<version>.zip`, z. B. vom USB-Stick, unter
*Ohne Internet: Update-Datei einspielen*.

## Auf einem anderen PC einrichten

1. Den Programmordner kopieren, z. B. per USB-Stick. Den Ordner `.venv-windows` kannst du weglassen.
2. Auf dem anderen PC muss Python 3.11 oder neuer installiert sein. Beim ersten Start ist eine
   Internetverbindung nötig.
3. `start.bat` doppelklicken. Dort startet der Einrichtungsassistent mit einem eigenen Datenordner.

## Dual-Boot: gleiche Daten unter Windows und Linux

1. Im Einrichtungsassistenten einen **Datenordner auf einer gemeinsamen Partition** wählen
   (NTFS oder exFAT, z. B. `D:\Concierge` bzw. `/media/<name>/Daten/Concierge`).
2. Unter dem anderen System denselben Ordner wählen. Die App erkennt die vorhandenen Daten und
   fragt nur noch die Passwörter und das Thunderbird-Profil ab.
3. Den **Schnellstart von Windows abschalten**. Ist er aktiv, bindet Linux die Partition nur lesend
   ein: *Systemsteuerung → Energieoptionen → Auswählen, was beim Drücken des Netzschalters geschehen
   soll → Schnellstart aktivieren* (Haken entfernen).
4. Updates immer auf **beiden** Systemen einspielen.

## Umstieg von „Anmeldungen“ (Version 1.x)

Beim ersten Start von Concierge werden Datenbank, Einstellungen und Passwörter automatisch
übernommen. Die alte Datei `anmeldungen.db` bleibt als Sicherung unverändert liegen. Wird sie danach
noch von einer alten Version geändert (z. B. auf dem anderen System), zeigt Concierge einen Hinweis:
diese Änderungen kommen nicht an. Deshalb beide Systeme zeitnah aktualisieren.
