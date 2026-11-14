"""Creates the key pair used to sign update files (run once).

Usage in the project folder:
    .venv-windows\\Scripts\\python tools\\make_keys.py

The private key is stored outside the project (default ~/.concierge/update-signing-key.txt,
override with CONCIERGE_SIGNING_KEY). It must never go into the project, onto GitHub or into an
update file: whoever has it can build updates that every installed app accepts. Keep a safe copy
(e.g. in a password manager): if it is lost, the app accepts no further updates until a version
with a new public key is installed by hand.

Put the printed public key into app/updater.py as PUBLIC_KEY.
"""
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import updater  # noqa: E402


def main() -> int:
    path = updater.key_file()
    if path.exists():
        print(f"A key already exists: {path}")
        print("Public key:", updater.public_key_of(updater.load_private_key(path)))
        return 1
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(raw).decode() + "\n", encoding="ascii")
    print(f"Private key stored: {path}")
    print("-> Keep it safe and never share it.")
    print("Public key (set as PUBLIC_KEY in app/updater.py):")
    print(updater.public_key_of(raw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
