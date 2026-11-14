"""Build, sign, check, download and install update files."""
import ast
import base64
import hashlib
import io
import json
import os
import re
import shutil
import urllib.error
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import updater, version


def _new_key() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    """A test key pair instead of the real one; the real private key is never used.
    Error messages are replaced by their translation keys to keep the assertions language-neutral."""
    key = _new_key()
    monkeypatch.setattr(updater, "PUBLIC_KEY", updater.public_key_of(key))
    monkeypatch.setattr(updater, "available", None)
    monkeypatch.setattr(updater, "t", lambda key, **kwargs: key)
    return key


@pytest.fixture
def install_dir(tmp_path):
    """A copy of the program files to install into (never the real project)."""
    target = tmp_path / "installed"
    for rel in updater.program_files(updater.ROOT):
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(updater.ROOT / rel, target / rel)
    return target


@pytest.fixture
def update_zip(tmp_path, monkeypatch, signing_key):
    """Update file for a version 99.0.0 with a changed main.py."""
    source = tmp_path / "source"
    for rel in updater.program_files(updater.ROOT):
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(updater.ROOT / rel, source / rel)
    (source / "app" / "main.py").write_text("# new version\n", encoding="utf-8")
    (source / "app" / "new.py").write_text("X = 1\n", encoding="utf-8")
    (source / "CHANGELOG.md").write_text("# Changes\n\n## 99.0.0\n\n- Everything new.\n\n## 1.0.0\n\n- old\n",
                                         encoding="utf-8")
    monkeypatch.setattr(version, "VERSION", "99.0.0")
    path = updater.build(source, tmp_path / "dist", signing_key)
    monkeypatch.setattr(version, "VERSION", "1.1.0")
    return path


def _rewrite(path, change):
    """Reads an update file, applies change(files: dict) and returns the new bytes."""
    with zipfile.ZipFile(path) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    change(files)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _resign(files, key):
    manifest = json.loads(files["manifest.json"])
    manifest["signature"] = updater.sign(manifest, key)
    files["manifest.json"] = json.dumps(manifest).encode()


def test_version_compare_is_numeric():
    assert version.parse("1.10.0") > version.parse("1.9.3")
    with pytest.raises(ValueError):
        version.parse("1.x")


def test_build_from_project_contains_program_but_no_cache(tmp_path, signing_key):
    update_path = updater.build(updater.ROOT, tmp_path, signing_key)
    update = updater.check(update_path.read_bytes())
    assert update.version == version.VERSION and update.is_reinstall
    assert "app/main.py" in update.files and "app/locales/de.json" in update.files
    assert not any("__pycache__" in rel or rel.startswith(("tests/", ".venv")) for rel in update.files)
    changelog = (updater.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert update.notes and update.notes == updater.changelog_section(changelog, version.VERSION)
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["version"] == version.VERSION and latest["file"] == update_path.name
    assert latest["sha256"] == hashlib.sha256(update_path.read_bytes()).hexdigest()


def test_update_stays_installable_by_version_1():
    """Version 1.x only accepts its own app id, these top-level files and files below app/."""
    legacy_root_files = {"requirements.txt", "start.bat", "start.sh", "anmeldungen.desktop", "README.md",
                         "CHANGELOG.md", "IDEEN.md"}
    assert updater.MANIFEST_APP_ID == "anmeldungen"
    assert set(updater.ROOT_FILES) <= legacy_root_files and updater.PROGRAM_DIR == "app"


def test_project_settings_are_filled_in():
    """The real constants from the source (the fixture replaces PUBLIC_KEY at runtime)."""
    tree = ast.parse((updater.ROOT / "app" / "updater.py").read_text(encoding="utf-8"))
    consts = {node.targets[0].id: node.value.value for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
              and isinstance(node.targets[0], ast.Name)}
    assert len(base64.b64decode(consts["PUBLIC_KEY"])) == 32
    assert "OWNER" not in consts["UPDATE_REPO"], "set UPDATE_REPO in app/updater.py"


def test_check_accepts_valid_update(update_zip):
    update = updater.check(update_zip.read_bytes())
    assert update.version == "99.0.0" and not update.is_reinstall
    assert update.notes == "- Everything new."


@pytest.mark.parametrize("change,key", [
    (lambda f: f.update({"app/main.py": b"tampered"}), "update.error.corrupt"),
    (lambda f: f.pop("app/version.py"), "update.error.incomplete"),
    (lambda f: f.update({"../evil.py": b"x"}), "update.error.unexpected"),
    (lambda f: f.pop("manifest.json"), "update.error.no_manifest"),
])
def test_check_rejects_broken_files(update_zip, change, key):
    with pytest.raises(updater.UpdateError, match=re.escape(key)):
        updater.check(_rewrite(update_zip, change))


def test_check_rejects_path_outside_program(update_zip, signing_key):
    def evil(files):
        manifest = json.loads(files["manifest.json"])
        manifest["dateien"]["../../Windows/evil.py"] = "0" * 64
        files["manifest.json"] = json.dumps(manifest).encode()
        _resign(files, signing_key)  # even with a valid signature
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.bad_path")):
        updater.check(_rewrite(update_zip, evil))


def test_check_rejects_foreign_or_missing_signature(update_zip):
    def tampered(files):  # file AND checksum replaced: only the signature notices
        manifest = json.loads(files["manifest.json"])
        files["app/main.py"] = b"import os; os.system('evil')"
        manifest["dateien"]["app/main.py"] = hashlib.sha256(files["app/main.py"]).hexdigest()
        files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.bad_signature")):
        updater.check(_rewrite(update_zip, tampered))

    with pytest.raises(updater.UpdateError, match=re.escape("update.error.bad_signature")):
        updater.check(_rewrite(update_zip, lambda f: _resign(f, _new_key())))

    def unsigned(files):
        manifest = json.loads(files["manifest.json"])
        del manifest["signature"]
        files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.unsigned")):
        updater.check(_rewrite(update_zip, unsigned))


def test_manifest_stays_readable_for_old_versions(update_zip):
    """Version 1.x knows no signature: it may only live inside the manifest, not as a file."""
    with zipfile.ZipFile(update_zip) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert set(zf.namelist()) == set(manifest["dateien"]) | {"manifest.json"}
        assert {"app", "version", "erstellt", "notizen", "dateien"} <= set(manifest)


def test_check_rejects_older_version_and_non_zip(update_zip, monkeypatch):
    monkeypatch.setattr(version, "VERSION", "100.0.0")
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.older")):
        updater.check(update_zip.read_bytes())
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.not_zip")):
        updater.check(b"no zip")


@pytest.mark.parametrize("rel,ok", [
    ("app/main.py", True), ("requirements.txt", True), ("app/ui/inbox.py", True), ("app/locales/de.json", True),
    ("../x.py", False), ("app/../../x.py", False), ("/etc/passwd", False), ("C:/x.py", False),
    ("app\\x.py", False), ("tests/test_x.py", False), ("app/__pycache__/x.pyc", False), ("app", False),
])
def test_program_paths(rel, ok):
    assert updater.is_program_path(rel) is ok


def test_install_replaces_files_and_removes_stale(install_dir, update_zip):
    stale = install_dir / "app" / "obsolete.py"
    stale.write_text("OLD = 1\n", encoding="utf-8")
    data_file = install_dir / "concierge.db"  # data in the program folder must never disappear
    data_file.write_bytes(b"data")

    saved = updater.install(updater.check(update_zip.read_bytes()), install_dir)

    assert (install_dir / "app" / "main.py").read_text(encoding="utf-8") == "# new version\n"
    assert (install_dir / "app" / "new.py").is_file()
    assert not stale.exists()
    assert data_file.read_bytes() == b"data"
    assert not list(install_dir.rglob("*.new"))
    with zipfile.ZipFile(saved) as zf:  # the backup contains the old version
        assert b"new version" not in zf.read("app/main.py")
        assert "app/obsolete.py" in zf.namelist()


def test_install_rolls_back_on_error(install_dir, update_zip, monkeypatch):
    original_main = (install_dir / "app" / "main.py").read_bytes()
    calls = {"n": 0}
    real_replace = os.replace

    def failing_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 5:  # in the middle of swapping files
            raise PermissionError("file locked")
        return real_replace(src, dst)
    monkeypatch.setattr(updater.os, "replace", failing_replace)

    with pytest.raises(updater.UpdateError, match=re.escape("update.error.install_failed")):
        updater.install(updater.check(update_zip.read_bytes()), install_dir)

    assert (install_dir / "app" / "main.py").read_bytes() == original_main
    assert not list(install_dir.rglob("*.new"))


def test_backups_are_limited(install_dir):
    for i in range(updater.BACKUPS_KEEP + 3):
        path = updater.backup(install_dir)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
        path.rename(path.with_name(f"before-1.1.0-{i:03d}.zip"))
    updater.backup(install_dir)
    assert len(list((install_dir / updater.BACKUP_DIR).glob("before-*.zip"))) == updater.BACKUPS_KEEP


# --- Online -------------------------------------------------------------------------------

@pytest.fixture
def github(update_zip, monkeypatch):
    """Simulates GitHub: serves latest.json and the update file from dist/."""
    served = {
        "latest.json": (update_zip.parent / "latest.json").read_bytes(),
        update_zip.name: update_zip.read_bytes(),
    }
    requested = []

    def fake_get(url, limit):
        requested.append(url)
        name = url.rsplit("/", 1)[1]
        if name not in served:
            raise updater.UpdateError("update.error.not_found")
        return served[name]
    monkeypatch.setattr(updater, "_get", fake_get)
    return served, requested


def test_find_latest_and_download(github):
    _, requested = github
    info = updater.find_latest()
    assert info.version == "99.0.0" and updater.available is info
    assert requested[0].endswith("/releases/latest/download/latest.json")
    update = updater.download(info)
    assert requested[1].endswith("/releases/download/v99.0.0/concierge-update-99.0.0.zip")
    assert update.version == "99.0.0" and update.notes == "- Everything new."


def test_find_latest_when_current(github, monkeypatch):
    monkeypatch.setattr(version, "VERSION", "99.0.0")
    assert updater.find_latest() is None and updater.available is None


def test_download_rejects_corrupt_file(github):
    served, _ = github
    info = updater.find_latest()
    served[info.url.rsplit("/", 1)[1]] += b"x"
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.download_corrupt")):
        updater.download(info)


@pytest.mark.parametrize("latest", [
    b"no json",
    json.dumps({"app": "anmeldungen", "version": "2.0.0", "file": "../evil.zip", "sha256": "0" * 64}).encode(),
    json.dumps({"app": "other", "version": "2.0.0", "file": "concierge-update-2.0.0.zip",
                "sha256": "0" * 64}).encode(),
])
def test_find_latest_rejects_bad_info(github, latest):
    github[0]["latest.json"] = latest
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.bad_info")):
        updater.find_latest()


def test_network_errors_become_friendly(monkeypatch):
    def offline(*a, **k):
        raise urllib.error.URLError("getaddrinfo failed")
    monkeypatch.setattr(updater.urllib.request, "urlopen", offline)
    with pytest.raises(updater.UpdateError, match=re.escape("update.error.offline")):
        updater.find_latest()
