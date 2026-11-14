# Concierge

A local web app that manages event registrations by e-mail. It reads the mailbox in which the
replies to an invitation newsletter arrive, suggests a classification for every mail
(registration, cancellation, other) and sends personal confirmations with a calendar file,
rejections or individual replies.

It runs on **Windows** and **Linux** and can share the same data between both (dual boot).
The user interface is translatable; German is included (`app/locales/de.json`).

**User guide (German):** [docs/user-guide.de.md](docs/user-guide.de.md)

## Running

| System  | Start |
|---------|-------|
| Windows | Double-click `start.bat` |
| Linux   | Run `start.sh`, or set up `concierge.desktop` (adjust `Exec=` and copy it to `~/.local/share/applications/`) |

The first start creates a Python environment (`.venv-windows` or `.venv-linux`) and installs the
dependencies. The browser then opens <http://127.0.0.1:8737>. The app only listens on the local
machine.

Requirements: Python 3.11 or newer (Windows: tick "Add python.exe to PATH" when installing;
Linux: `sudo apt install python3-venv`).

## Where data lives

- **Data folder** (chosen in the setup wizard, can be shared between systems): `concierge.db`,
  `backups/` (one per start, the last 10 are kept), `concierge.lock`.
- **Per system:** paths to the data folder and Thunderbird profile, theme and language
  (Windows `%APPDATA%\concierge\config.json`, Linux `~/.config/concierge/config.json`), the mail
  passwords in the system vault (Windows Credential Manager or KWallet/Secret Service) and the log
  (`%LOCALAPPDATA%\concierge\Logs` or `~/.local/state/concierge/log`).

Until version 1.x the app was called "Anmeldungen" and used German names for its data. On the
first start, version 2 copies `anmeldungen.db` into `concierge.db` (the old file stays untouched),
takes over the old settings file and reads passwords from the old vault entry. See `app/legacy.py`.

## Development

```
python -m venv .venv-windows
.venv-windows\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv-windows\Scripts\python -m pytest
.venv-windows\Scripts\python -m ruff check .
```

`CONCIERGE_CONFIG_DIR=<folder>` uses a separate configuration, `CONCIERGE_NO_BROWSER=1` starts the
app without opening the browser.

### Project layout

```
app/
  main.py            entry point (python -m app.main)
  config.py          per-system settings, password vault
  db.py              SQLite schema and helpers
  legacy.py          one-time import of data from version 1.x
  i18n.py            translations (t("key")), date formatting
  locales/de.json    German texts
  classifier.py      rule-based classification of replies
  templates.py       mail templates (Jinja, sandboxed)
  updater.py         signed updates from GitHub Releases or a file
  mail/              IMAP, SMTP, autoconfig, text parsing
  services/          accounts, events, people, registrations, fetching, sending
  ui/                NiceGUI pages
tests/               pytest
tools/               release tooling (signing keys, update files)
```

### Translations

All user-facing text lives in `app/locales/<language>.json` as flat keys (`"inbox.title": "..."`).
Code uses `t("inbox.title")`, `t("overview.fetched", count=n)` (with `"one"`/`"other"` plural forms)
and `format_date(...)`. To add a language, copy `de.json`, translate the values and set
`"language.name"`; a language selector appears in the settings as soon as there is more than one
file. `tests/test_i18n.py` checks that every key used in the code exists in every language.

The classifier and mail parser recognise German (and some English) phrases on purpose, because
that is the language replies arrive in.

## Releases and updates

Installed apps check `https://github.com/Pluettmann/concierge/releases/latest/download/latest.json`
(`UPDATE_REPO` in `app/updater.py`), download the update file and install it after checking its
**Ed25519 signature** against `PUBLIC_KEY`. Update files from a USB stick are checked the same way.

1. Bump `VERSION` in `app/version.py`.
2. Add a section `## <version>` to `CHANGELOG.md` (shown to users when they install the update).
3. `.venv-windows\Scripts\python tools\make_update.py` builds `dist/concierge-update-<version>.zip`
   and `dist/latest.json`.
4. Create a GitHub release with the tag `v<version>` and upload both files, or let
   `make_update.py --publish` do it with the GitHub CLI (`gh`).

The private signing key is created once with `tools/make_keys.py` and stored outside the project
(`~/.concierge/update-signing-key.txt`, or `CONCIERGE_SIGNING_KEY`). It must never be committed;
if it is lost, installed apps accept no further updates until a version with a new public key is
installed by hand.

**Compatibility with 1.x:** version 1.x only accepts update files whose manifest has
`"app": "anmeldungen"` and that contain nothing but `app/` and a fixed set of top-level files.
`MANIFEST_APP_ID` and `ROOT_FILES` in `app/updater.py` keep to that, so 2.x updates can still be
installed from 1.x.

## License

[MIT](LICENSE) © Pascal Lüttmann
