"""Install updates, online from GitHub Releases or from an update file (.zip).

An update file contains the program files and a manifest.json with version, release notes,
SHA-256 checksums and an Ed25519 signature. The checksums detect damaged files; the signature makes
sure the update comes from the developer: only whoever holds the private key (tools/make_keys.py)
can build an update the app accepts, wherever the file comes from.

Online: every GitHub release contains the update file and latest.json (version, file name,
checksum). The app reads latest.json via the stable link .../releases/latest/download/latest.json.

Data (database, settings, passwords) lives outside the program folder and is never touched.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import version
from .i18n import t

log = logging.getLogger(__name__)

# Identifier in the manifest. Version 1.x ("Anmeldungen") only accepts update files carrying this
# value, so it stays until no installation older than 2.0 is left.
MANIFEST_APP_ID = "anmeldungen"
MANIFEST = "manifest.json"
LATEST = "latest.json"
FILE_PREFIX = "concierge-update-"
# Public GitHub repository whose releases contain the update files.
UPDATE_REPO = "Pluettmann/concierge"
# Public Ed25519 key (Base64). Only the developer has the private key.
PUBLIC_KEY = "BZ/9EA2N32skaAi6yDlybw1UUg5PQzA48j+PRqQmL3g="
SIGNATURE_FIELD = "signature"
TIMEOUT = 20

# Files outside PROGRAM_DIR that belong to the program. Version 1.x only accepts these names (and
# files below app/), so new top-level files must not be added to updates before 2.x is everywhere.
ROOT_FILES = ("requirements.txt", "start.bat", "start.sh", "README.md", "CHANGELOG.md")
PROGRAM_DIR = "app"
BACKUP_DIR = ".updates"
BACKUPS_KEEP = 5
MAX_SIZE = 20 * 1024 * 1024
# Set when restarting after an update: the new process waits until the old one frees the port.
RESTART_ENV = "CONCIERGE_RESTART"
NO_BROWSER_ENV = "CONCIERGE_NO_BROWSER"

ROOT = Path(__file__).resolve().parent.parent


class UpdateError(Exception):
    """The update file is invalid or could not be installed (message is shown to the user)."""


@dataclass
class Update:
    version: str
    notes: str
    created: str
    files: dict[str, bytes] = field(repr=False)

    @property
    def is_reinstall(self) -> bool:
        return version.parse(self.version) == version.parse(version.VERSION)


# --- Which files belong to the program? ---------------------------------------------------

def is_program_path(rel: str) -> bool:
    """An update may only write these paths, nothing outside the program folder."""
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel:
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if "__pycache__" in parts or rel.endswith(".pyc"):
        return False
    return rel in ROOT_FILES or (parts[0] == PROGRAM_DIR and len(parts) > 1)


def program_files(root: Path = ROOT) -> list[str]:
    files = [name for name in ROOT_FILES if (root / name).is_file()]
    for path in sorted((root / PROGRAM_DIR).rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and is_program_path(rel):
            files.append(rel)
    return files


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Signature ----------------------------------------------------------------------------

def key_file() -> Path:
    """Location of the developer's private key, deliberately outside the project."""
    env = os.environ.get("CONCIERGE_SIGNING_KEY")
    return Path(env) if env else Path.home() / ".concierge" / "update-signing-key.txt"


def load_private_key(path: Path | None = None) -> bytes:
    path = path or key_file()
    if not path.is_file():
        raise UpdateError(f"No signing key at {path}. Run tools/make_keys.py once "
                          f"(or point CONCIERGE_SIGNING_KEY to the key file).")
    return base64.b64decode(path.read_text(encoding="ascii").strip())


def public_key_of(private_key: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    pub = Ed25519PrivateKey.from_private_bytes(private_key).public_key()
    return base64.b64encode(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _signed_bytes(manifest: dict) -> bytes:
    """Canonical bytes of the manifest without the signature itself."""
    body = {k: v for k, v in manifest.items() if k != SIGNATURE_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(manifest: dict, private_key: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(private_key)
    return base64.b64encode(key.sign(_signed_bytes(manifest))).decode()


def verify(manifest: dict) -> None:
    sig = manifest.get(SIGNATURE_FIELD)
    if not sig or not isinstance(sig, str):
        raise UpdateError(t("update.error.unsigned"))
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise UpdateError(t("update.error.no_crypto")) from None
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY, validate=True))
        key.verify(base64.b64decode(sig, validate=True), _signed_bytes(manifest))
    except (InvalidSignature, ValueError):
        raise UpdateError(t("update.error.bad_signature")) from None


# --- Build an update file (developer) -------------------------------------------------------

def changelog_section(text: str, ver: str) -> str:
    """The section '## <ver>' of CHANGELOG.md."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == ver
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()


def build(root: Path = ROOT, out_dir: Path | None = None, private_key: bytes | None = None) -> Path:
    """Builds the signed update file and latest.json next to it (both go into the GitHub release)."""
    ver = version.VERSION
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8") if (root / "CHANGELOG.md").is_file() else ""
    notes = changelog_section(changelog, ver)
    if not notes:
        raise UpdateError(f"CHANGELOG.md has no section '## {ver}'.")
    private_key = private_key if private_key is not None else load_private_key()
    contents = {rel: (root / rel).read_bytes() for rel in program_files(root)}
    # Field names are part of the file format that version 1.x reads, so they stay as they are.
    manifest = {"app": MANIFEST_APP_ID, "version": ver, "erstellt": datetime.now().isoformat(timespec="seconds"),
                "notizen": notes, "dateien": {rel: _sha256(data) for rel, data in contents.items()}}
    manifest[SIGNATURE_FIELD] = sign(manifest, private_key)
    out_dir = out_dir or root / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{FILE_PREFIX}{ver}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        for rel, data in contents.items():
            zf.writestr(rel, data)
    latest = {"app": MANIFEST_APP_ID, "version": ver, "file": target.name, "sha256": _sha256(target.read_bytes()),
              "created": manifest["erstellt"]}
    (out_dir / LATEST).write_text(json.dumps(latest, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


# --- Check an update file -------------------------------------------------------------------

def check(data: bytes) -> Update:
    """Checks an update file completely before anything is written."""
    if len(data) > MAX_SIZE:
        raise UpdateError(t("update.error.too_large"))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError(t("update.error.not_zip")) from None
    with zf:
        try:
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
        except KeyError:
            raise UpdateError(t("update.error.no_manifest")) from None
        except (ValueError, UnicodeDecodeError):
            raise UpdateError(t("update.error.bad_manifest")) from None
        if not isinstance(manifest, dict) or manifest.get("app") != MANIFEST_APP_ID:
            raise UpdateError(t("update.error.wrong_app"))
        verify(manifest)
        try:
            new = version.parse(str(manifest.get("version", "")))
        except ValueError:
            raise UpdateError(t("update.error.bad_version")) from None
        if new < version.parse(version.VERSION):
            raise UpdateError(t("update.error.older", version=manifest["version"], installed=version.VERSION))
        expected: dict[str, str] = manifest.get("dateien") or {}
        for rel in expected:
            if not is_program_path(rel):
                raise UpdateError(t("update.error.bad_path", path=rel))
        for required in (f"{PROGRAM_DIR}/main.py", f"{PROGRAM_DIR}/version.py", "requirements.txt"):
            if required not in expected:
                raise UpdateError(t("update.error.incomplete", path=required))
        unknown = set(zf.namelist()) - set(expected) - {MANIFEST}
        if unknown:
            raise UpdateError(t("update.error.unexpected", files=", ".join(sorted(unknown)[:3])))
        files = {}
        for rel, digest in expected.items():
            try:
                content = zf.read(rel)
            except KeyError:
                raise UpdateError(t("update.error.incomplete", path=rel)) from None
            if _sha256(content) != digest:
                raise UpdateError(t("update.error.corrupt", path=rel))
            files[rel] = content
    return Update(version=manifest["version"], notes=str(manifest.get("notizen", "")),
                  created=str(manifest.get("erstellt", "")), files=files)


# --- Online (GitHub Releases) ---------------------------------------------------------------

@dataclass
class AvailableUpdate:
    version: str
    url: str
    sha256: str


# Result of the last online check (for the hint in the navigation).
available: AvailableUpdate | None = None
_FILE_NAME = re.compile(rf"{re.escape(FILE_PREFIX)}\d+(\.\d+)*\.zip")


def _get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"concierge/{version.VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(t("update.error.not_found")) from None
        raise UpdateError(t("update.error.server", code=exc.code)) from None
    except (urllib.error.URLError, OSError):
        raise UpdateError(t("update.error.offline")) from None
    if len(data) > limit:
        raise UpdateError(t("update.error.too_large"))
    return data


def find_latest() -> AvailableUpdate | None:
    """Asks GitHub for the newest version. None if the installed one is up to date."""
    global available
    data = _get(f"https://github.com/{UPDATE_REPO}/releases/latest/download/{LATEST}", 64 * 1024)
    try:
        info = json.loads(data.decode("utf-8"))
        ver, name, digest = str(info["version"]), str(info["file"]), str(info["sha256"]).lower()
        new = version.parse(ver)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
        raise UpdateError(t("update.error.bad_info")) from None
    if info.get("app") != MANIFEST_APP_ID or not _FILE_NAME.fullmatch(name) \
            or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError(t("update.error.bad_info"))
    if new <= version.parse(version.VERSION):
        available = None
        return None
    available = AvailableUpdate(version=ver, sha256=digest,
                                url=f"https://github.com/{UPDATE_REPO}/releases/download/v{ver}/{name}")
    return available


def download(info: AvailableUpdate) -> Update:
    """Downloads the update and checks it completely (checksum and signature)."""
    data = _get(info.url, MAX_SIZE)
    if _sha256(data) != info.sha256:
        raise UpdateError(t("update.error.download_corrupt"))
    update = check(data)
    if update.version != info.version:
        raise UpdateError(t("update.error.version_mismatch"))
    return update


# --- Install --------------------------------------------------------------------------------

def backup(root: Path = ROOT) -> Path:
    """Saves all program files to .updates/ (the last BACKUPS_KEEP are kept)."""
    target_dir = root / BACKUP_DIR
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f"before-{version.VERSION}-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in program_files(root):
            zf.write(root / rel, rel)
    old_backups = [*target_dir.glob("before-*.zip"), *target_dir.glob("vorher-*.zip")]
    for old in sorted(old_backups, key=lambda p: p.stat().st_mtime)[:-BACKUPS_KEEP]:
        old.unlink(missing_ok=True)
    return target


def _restore(root: Path, backup_file: Path) -> None:
    with zipfile.ZipFile(backup_file) as zf:
        for rel in zf.namelist():
            if is_program_path(rel):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(zf.read(rel))


def install(update: Update, root: Path = ROOT) -> Path:
    """Replaces the program files and returns the path of the backup.

    Steps: backup -> write all new files as *.new -> rename -> remove obsolete program files.
    If anything fails, the backup is restored.
    """
    saved = backup(root)
    staged: list[Path] = []
    try:
        for rel, content in update.files.items():
            tmp = root / (rel + ".new")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(content)
            staged.append(tmp)
    except OSError as exc:
        for tmp in staged:
            tmp.unlink(missing_ok=True)
        raise UpdateError(t("update.error.write_failed", error=exc)) from exc
    try:
        for rel in update.files:
            os.replace(root / (rel + ".new"), root / rel)
        for rel in program_files(root):
            if rel.startswith(PROGRAM_DIR + "/") and rel not in update.files:
                (root / rel).unlink()
    except OSError as exc:
        log.exception("Update failed, restoring backup")
        _restore(root, saved)
        for tmp in staged:
            tmp.unlink(missing_ok=True)
        raise UpdateError(t("update.error.install_failed", error=exc)) from exc
    log.info("Update from %s to %s installed (backup: %s)", version.VERSION, update.version, saved.name)
    return saved


# --- Restart --------------------------------------------------------------------------------

def restart(root: Path = ROOT) -> None:
    """Restarts the app through the start script. This process must exit afterwards.

    The start script installs changed dependencies. The new process waits until this one has
    freed the port (see main.wait_for_previous). The browser is not opened again: the open tab
    reconnects by itself.
    """
    env = {**os.environ, NO_BROWSER_ENV: "1", RESTART_ENV: "1"}
    if sys.platform.startswith("win"):
        # Full path: with NoDefaultCurrentDirectoryInExePath set, cmd does not search the current folder.
        subprocess.Popen(["cmd", "/c", str(root / "start.bat")], cwd=root, env=env,
                         creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen(["sh", str(root / "start.sh")], cwd=root, env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("Restart via the start script triggered")
