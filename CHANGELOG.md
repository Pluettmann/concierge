# Changelog

Newest version first. The section of a version is shown to users when they install the update,
so it is written in their language (German) and in plain words.

## 2.0.1

- Behoben: Beim Anlegen einer Veranstaltung schrieb der Schritt „Einladungs-Newsletter“ eine
  unnötige Fehlermeldung ins Protokoll. Die App funktionierte dabei aber normal.

## 2.0.0

- Neuer Name: Die App heißt jetzt **Concierge**.
- Neu: Updates kommen aus dem Internet. Beim Start sieht die App still nach, ob es eine neue Version
  gibt, und zeigt dann neben *Einstellungen* den Hinweis „Update“. Unter *Einstellungen → Version &
  Update* genügt ein Klick auf „Herunterladen & installieren“.
- Updates sind jetzt signiert. Die App spielt nur Updates ein, die nachweislich vom Entwickler
  stammen, egal ob aus dem Internet oder vom USB-Stick.
- Deine Daten werden beim ersten Start einmalig übernommen: Die Datenbank heißt jetzt
  `concierge.db`, die bisherige `anmeldungen.db` bleibt als Sicherung unverändert liegen.
  Einstellungen und gespeicherte Passwörter werden ebenfalls übernommen.
- **Dual-Boot:** Bitte auf beiden Systemen aktualisieren, bevor du weiterarbeitest. Was danach noch
  mit der alten Version geändert wird, kommt in Concierge nicht an. Die App weist darauf hin.
- Platzhalter in den Mailvorlagen heißen jetzt z. B. `{{ first_name }}` statt `{{ vorname }}`.
  Eigene Vorlagen werden automatisch angepasst.
- Der Neustart nach diesem Update dauert einmalig etwas länger, weil ein Programmteil nachinstalliert
  wird. Dafür ist eine Internetverbindung nötig.

## 1.3.1

- Behoben: Im dunklen Design (Einstellung „Automatisch“) waren Bedienelemente des Browsers wie die
  Pfeile in Zahlenfeldern hell statt dunkel.

## 1.3.0

- Neu: Unter *Einstellungen → Darstellung* lässt sich zwischen hellem und dunklem Design wählen.
  „Automatisch“ richtet sich wie bisher nach dem Betriebssystem. Die Wahl gilt für den jeweiligen PC.

## 1.2.0

- Neu: Absender ausblenden – für Firmen, Newsletter und andere Adressen, die keine Personen sind.
  Ausgeblendete tauchen nicht mehr in der Personenliste auf, und ihre Mails werden nie als Anmeldung
  vorgeschlagen (sie landen still unter *Erledigt*). Ausblenden geht unter *Personen* (mehrere auf
  einmal per Häkchen), im Personen-Dialog und direkt im Eingang. Unter *Personen → Ausgeblendet* lässt
  sich das rückgängig machen.
- Behoben: Postfächer mit Sonderzeichen wie „§“ im Passwort ließen sich nicht einrichten.
- Behoben: Im dunklen Design blitzte beim Seitenwechsel kurz ein weißer Hintergrund auf.

## 1.1.0

- Behoben: Antworten an Personen, die innerhalb eines längeren Mail-Verlaufs geantwortet hatten,
  wurden nicht verschickt, und der Versand brach ohne Meldung ab.
- Fehler beim Versand werden jetzt in einem Fenster angezeigt, das stehen bleibt; die übrigen
  Empfänger bekommen ihre Mail trotzdem.
- Neu: Updates lassen sich unter *Einstellungen → Version & Update* aus einer Update-Datei einspielen.
- Mehrere Postfächer: Abruf aus allen, Antwort aus dem Postfach, in dem die Mail ankam.
- Start auf einem anderen PC: eine mitkopierte Python-Umgebung wird automatisch neu eingerichtet.

## 1.0.0

- Erste Version.
