"""Version of the app (scheme: major.minor.patch).

Bump it for every change that reaches users and add a section to CHANGELOG.md;
then build the update file with `python tools/make_update.py`.
"""
VERSION = "2.0.0"


def parse(version: str) -> tuple[int, ...]:
    """'1.10.2' -> (1, 10, 2), so versions compare as numbers, not as text."""
    parts = version.strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"Invalid version number: {version!r}")
    return tuple(int(p) for p in parts)
