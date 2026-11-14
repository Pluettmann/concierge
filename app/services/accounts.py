"""Mail accounts. Each has its own servers, folders, sender name and signature.

An account dict has the keys imap.Settings.from_db and smtp.Settings.from_db expect
(email, login, imap_host, ...), so it can be passed to them directly.
"""
from __future__ import annotations

from .. import config, db

FIELDS = db.ACCOUNT_FIELDS


class MissingPassword(Exception):
    """No password is stored for this account on this system."""

    def __init__(self, email: str):
        super().__init__(email)
        self.email = email


def all_accounts() -> list[dict]:
    return db.query("SELECT * FROM account ORDER BY position, id")


def get(account_id: int | None) -> dict | None:
    if account_id is None:
        return None
    return db.query_one("SELECT * FROM account WHERE id = ?", (account_id,))


def primary() -> dict | None:
    return db.query_one("SELECT * FROM account ORDER BY position, id LIMIT 1")


def get_or_primary(account_id: int | None) -> dict | None:
    return get(account_id) or primary()


def own_addresses() -> set[str]:
    return {a["email"].lower() for a in all_accounts()}


def save(data: dict, account_id: int | None = None) -> int:
    """Creates or changes an account. Returns its id."""
    values = {k: data[k] for k in FIELDS if k in data}
    if "email" in values:
        values["email"] = values["email"].strip().lower()
    if account_id is None:
        existing = db.query_one("SELECT id FROM account WHERE email = ?", (values.get("email", ""),))
        account_id = existing["id"] if existing else None
    if account_id is None:
        position = (db.query_one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM account") or {"p": 0})["p"]
        cols = [*values, "position"]
        return db.execute(f"INSERT INTO account ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          (*values.values(), position))
    if values:
        sets = ", ".join(f"{k} = ?" for k in values)
        db.execute(f"UPDATE account SET {sets} WHERE id = ?", (*values.values(), account_id))
    return account_id


def delete(account_id: int) -> None:
    """Removes an account. Mails and the send log stay; replies then go out from the primary account."""
    with db.connect() as con:
        con.execute("UPDATE mail SET account_id = NULL WHERE account_id = ?", (account_id,))
        con.execute("UPDATE sent_mail SET account_id = NULL WHERE account_id = ?", (account_id,))
        con.execute("DELETE FROM account WHERE id = ?", (account_id,))


def password(account: dict) -> str:
    pw = config.get_password(account["email"])
    if not pw:
        raise MissingPassword(account["email"])
    return pw


def with_globals(account: dict) -> dict:
    """Account plus shared settings (e.g. the accent colour), as used by the mail templates."""
    return {**db.get_settings(), **account}


def label(account: dict) -> str:
    return f"{account['sender_name']} <{account['email']}>" if account.get("sender_name") else account["email"]
