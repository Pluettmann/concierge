"""Builds the signed update file for the current version (app/version.py) and latest.json.

Usage in the project folder:
    .venv-windows\\Scripts\\python tools\\make_update.py            # build only
    .venv-windows\\Scripts\\python tools\\make_update.py --publish  # build and create a GitHub release (gh)

Before: bump VERSION in app/version.py and add its section to CHANGELOG.md.
Result in dist/: concierge-update-<version>.zip and latest.json. Both belong in a GitHub release
tagged v<version>. The .zip file can also be installed from a USB stick.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import updater, version  # noqa: E402


def publish(target: Path, notes: str) -> int:
    gh = shutil.which("gh")
    if not gh:
        print("\nThe GitHub CLI (gh) is not installed: winget install GitHub.cli, then gh auth login.")
        return 1
    tag = f"v{version.VERSION}"
    cmd = [gh, "release", "create", tag, str(target), str(target.parent / updater.LATEST),
           "--repo", updater.UPDATE_REPO, "--title", f"Version {version.VERSION}", "--notes", notes]
    print(f"\nPublishing {tag} to {updater.UPDATE_REPO} ...")
    return subprocess.call(cmd)


def main() -> int:
    try:
        target = updater.build(ROOT)
        update = updater.check(target.read_bytes())  # checked exactly like on installation (incl. signature)
    except updater.UpdateError as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Update file for version {version.VERSION} created: {target}")
    print(f"{len(update.files)} files, {target.stat().st_size // 1024} KB, signed")
    print("\nRelease notes:\n" + update.notes)
    if "--publish" in sys.argv:
        return publish(target, update.notes)
    print(f"\nTo publish: create a release tagged v{version.VERSION} at "
          f"https://github.com/{updater.UPDATE_REPO}/releases/new and upload both files from dist/ "
          f"({target.name}, {updater.LATEST}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
