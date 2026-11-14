"""Setup wizard: first setup, additional accounts and single settings.

Routes:
  /setup                              first setup (or second system, if the data folder already has data)
  /setup?new=1                        add another account
  /setup?only=<step>&account=<id>     run one step again for an account
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

from nicegui import ui

from .. import config, db, lock, thunderbird
from ..i18n import t
from ..mail import autoconfig, imap, smtp
from ..services import accounts, people
from . import layout
from .folder_picker import choose_folder, suggestions
from .wizard import Step, Wizard

SECURITY = ("ssl", "starttls", "none")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SERVER_FIELDS = ("email", "login", "imap_host", "imap_port", "imap_security", "smtp_host", "smtp_port",
                 "smtp_security")


def _initial_state(account: dict | None, prefill_sender: dict | None = None) -> dict:
    s = db.get_settings() if db.is_open() else {}
    a = account or {}
    sender = prefill_sender or a
    return {
        "account_id": a.get("id"),
        "data_dir": str(config.data_dir() or ""),
        "email": a.get("email", ""),
        "login": a.get("login", ""),
        "imap_host": a.get("imap_host", ""), "imap_port": int(a.get("imap_port") or 993),
        "imap_security": a.get("imap_security", "ssl"),
        "smtp_host": a.get("smtp_host", ""), "smtp_port": int(a.get("smtp_port") or 465),
        "smtp_security": a.get("smtp_security", "ssl"),
        "source": "",
        "password": "",
        "passwords": {},
        "folders": [],
        "inbox_folder": a.get("inbox_folder", ""), "sent_folder": a.get("sent_folder", ""),
        "tb_profile": str(config.thunderbird_profile() or ""),
        "tb_member_source": s.get("tb_member_source", ""),
        "sender_name": sender.get("sender_name", ""),
        "signature": sender.get("signature", ""),
        "accent_color": s.get("accent_color", layout.ACCENT),
        "second_system": False,
        "error": "",
    }


async def _test_connection(settings: dict, password: str) -> list[dict] | None:
    """Tests IMAP and SMTP. Returns the IMAP folders, or None on an error."""
    n = ui.notification(t("setup.testing", email=settings["email"]), spinner=True, timeout=None)
    try:
        ok, folders = await layout.background(imap.list_folders, imap.Settings.from_db(settings, password))
        if not ok:
            return None
        ok, _ = await layout.background(smtp.test, smtp.Settings.from_db(settings, password))
        return folders if ok else None
    finally:
        n.dismiss()


@ui.page("/setup")
def page(only: str = "", account: int | None = None, new: int = 0) -> None:
    layout.setup_theme()
    layout.page_title(t("setup.page_title"))
    adding = bool(new) and db.is_open()
    single = bool(only) and db.is_open()
    if adding:
        st = _initial_state(None, prefill_sender=accounts.primary())
    else:
        st = _initial_state(accounts.get_or_primary(account) if db.is_open() else None)

    def second_system() -> bool:
        return st["second_system"]

    def not_second_system() -> bool:
        return not st["second_system"]

    # --- Step: welcome ---------------------------------------------------------------------
    def r_welcome():
        ui.markdown(t("setup.welcome.text"))

    # --- Step: data folder -----------------------------------------------------------------
    def r_data():
        ui.markdown(t("setup.data.text"))
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            ui.input(t("setup.data.folder")).bind_value(st, "data_dir").classes("grow").props("outlined")

            async def pick():
                chosen = await choose_folder(st["data_dir"] or None)
                if chosen:
                    st["data_dir"] = chosen
            ui.button(t("setup.data.choose"), icon="folder_open", on_click=pick).props("outline")
        hints = suggestions()
        if hints:
            ui.label(t("setup.data.suggestions")).classes("muted text-sm")
            with ui.row().classes("gap-2 flex-wrap"):
                for h in hints:
                    ui.chip(str(h), icon="save", on_click=lambda h=h: st.update(data_dir=str(h))).props(
                        "clickable outline")
        if sys.platform.startswith("win"):
            with ui.card().classes("w-full hint-info").props("flat"), ui.row().classes("no-wrap gap-3"):
                ui.icon("info", color="info").classes("text-xl")
                ui.markdown(t("setup.data.fast_startup")).classes("text-sm")

    def n_data() -> bool:
        raw = (st["data_dir"] or "").strip()
        if not raw:
            ui.notify(t("setup.data.missing"), type="warning")
            return False
        path = Path(raw).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".write-test-{uuid.uuid4().hex}"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            ui.notify(t("setup.data.not_writable", error=exc), type="negative", multi_line=True)
            return False
        existing = db.exists_in(path)
        lock.release()
        config.save_local(data_dir=str(path))
        db.open_db(path)
        lock.acquire()
        if existing and db.get_setting("setup_done") == "1" and accounts.primary():
            fresh = _initial_state(accounts.primary())
            st.update({k: v for k, v in fresh.items() if k not in ("data_dir", "tb_profile")})
            st["second_system"] = True
            ui.notify(t("setup.data.found_existing"), type="positive", multi_line=True)
        return True

    # --- Step: e-mail address -------------------------------------------------------------
    def r_email():
        inp = ui.input(t("setup.email.label"), placeholder=t("setup.email.placeholder")).bind_value(
            st, "email").classes("w-full").props("outlined autofocus type=email")
        inp.on("keydown.enter", lambda: wizard.next())
        ui.label(t("setup.email.hint")).classes("muted text-sm")

    async def n_email() -> bool:
        email = (st["email"] or "").strip().lower()
        if not EMAIL_RE.match(email):
            ui.notify(t("setup.email.invalid"), type="warning")
            return False
        other = db.query_one("SELECT id FROM account WHERE email = ?", (email,))
        if other and other["id"] != st["account_id"]:
            ui.notify(t("setup.email.exists"), type="warning")
            return False
        st["email"] = email
        ok, cfg = await layout.background(autoconfig.discover, email)
        if ok and cfg:
            st.update(imap_host=cfg.imap_host, imap_port=cfg.imap_port, imap_security=cfg.imap_security,
                      smtp_host=cfg.smtp_host, smtp_port=cfg.smtp_port, smtp_security=cfg.smtp_security,
                      login=cfg.username or email, source=cfg.source)
        return True

    # --- Step: servers ----------------------------------------------------------------------
    def r_server():
        if st["source"]:
            ui.label(t("setup.server.source", source=t(f"setup.server.source_{st['source']}"))).classes(
                "muted text-sm")
        ui.input(t("setup.server.login")).bind_value(st, "login").classes("w-full").props("outlined")
        security = {s: t(f"setup.server.security_{s}") for s in SECURITY}
        for proto in ("imap", "smtp"):
            ui.label(t(f"setup.server.{proto}")).classes("font-semibold mt-2")
            with ui.row().classes("w-full no-wrap gap-2"):
                ui.input(t("setup.server.host")).bind_value(st, f"{proto}_host").classes("grow").props("outlined")
                ui.number(t("setup.server.port"), format="%d").bind_value(st, f"{proto}_port").classes("w-24").props(
                    "outlined")
                ui.select(security, label=t("setup.server.security")).bind_value(st, f"{proto}_security").classes(
                    "w-40").props("outlined")

    def n_server() -> bool:
        if not st["imap_host"] or not st["smtp_host"]:
            ui.notify(t("setup.server.missing"), type="warning")
            return False
        st["imap_port"], st["smtp_port"] = int(st["imap_port"] or 993), int(st["smtp_port"] or 465)
        return True

    # --- Step: password (one account) -------------------------------------------------------
    def r_password():
        have = bool(config.get_password(st["email"]))
        ui.label(t("setup.account", email=st["email"])).classes("font-medium")
        pw = ui.input(t("common.password"), password=True, password_toggle_button=True).bind_value(
            st, "password").classes("w-full").props("outlined autofocus")
        pw.on("keydown.enter", lambda: wizard.next())
        ui.label(t("setup.password.hint") + (" " + t("setup.password.keep") if have else "")).classes(
            "muted text-sm")
        ui.label().bind_text_from(st, "error").classes("text-negative")

    async def n_password() -> bool:
        password = st["password"] or config.get_password(st["email"]) or ""
        if not password:
            ui.notify(t("setup.password.missing"), type="warning")
            return False
        settings = {k: st[k] for k in SERVER_FIELDS}
        st["error"] = ""
        folders = await _test_connection(settings, password)
        if folders is None:
            st["error"] = t("setup.password.failed")
            return False
        st["folders"] = folders
        if not config.set_password(st["email"], password):
            ui.notify(t("setup.password.no_vault"), type="warning", multi_line=True)
        inbox, sent = imap.suggest_folders(folders)
        st["inbox_folder"] = st["inbox_folder"] or inbox
        st["sent_folder"] = st["sent_folder"] or sent
        settings.update(inbox_folder=st["inbox_folder"], sent_folder=st["sent_folder"])
        if st["account_id"] is None:  # new account: prefill the sender if still empty
            settings.update(sender_name=st["sender_name"], signature=st["signature"])
        st["account_id"] = accounts.save(settings, st["account_id"])
        ui.notify(t("setup.password.success"), type="positive")
        return True

    # --- Step: passwords of all accounts (second system) --------------------------------------
    def r_passwords():
        ui.label(t("setup.passwords.hint")).classes("muted text-sm")
        for a in accounts.all_accounts():
            have = bool(config.get_password(a["email"]))
            st["passwords"].setdefault(a["email"], "")
            label = t("setup.passwords.for_stored" if have else "setup.passwords.for", email=a["email"])
            ui.input(label, password=True, password_toggle_button=True).bind_value(
                st["passwords"], a["email"]).classes("w-full").props("outlined")
        ui.label().bind_text_from(st, "error").classes("text-negative")

    async def n_passwords() -> bool:
        st["error"] = ""
        for a in accounts.all_accounts():
            password = st["passwords"].get(a["email"]) or config.get_password(a["email"])
            if not password:
                st["error"] = t("setup.passwords.missing", email=a["email"])
                return False
            if st["passwords"].get(a["email"]):
                if await _test_connection(a, password) is None:
                    st["error"] = t("setup.passwords.failed", email=a["email"])
                    return False
                config.set_password(a["email"], password)
        return True

    # --- Step: folders ------------------------------------------------------------------------
    folder_box = {}

    def r_folders():
        ui.label(t("setup.account", email=st["email"])).classes("font-medium")
        folder_box["col"] = ui.column().classes("w-full gap-3")
        fill_folders()
        if not st["folders"]:
            ui.timer(0.1, load_folders, once=True)

    def fill_folders():
        col = folder_box["col"]
        col.clear()
        names = [f["name"] for f in st["folders"]] or [n for n in (st["inbox_folder"], st["sent_folder"]) if n]
        with col:
            ui.select(names, label=t("setup.folders.inbox"), with_input=True).bind_value(
                st, "inbox_folder").classes("w-full").props("outlined")
            ui.select(names, label=t("setup.folders.sent"), with_input=True).bind_value(
                st, "sent_folder").classes("w-full").props("outlined")
            ui.label(t("setup.folders.hint")).classes("muted text-sm")

    async def load_folders():
        a = accounts.get(st["account_id"])
        if not a:
            return
        ok, folders = await layout.background(lambda: imap.list_folders(imap.Settings.from_db(a, accounts.password(a))))
        if ok:
            st["folders"] = folders
            fill_folders()

    def n_folders() -> bool:
        if not st["inbox_folder"] or not st["sent_folder"]:
            ui.notify(t("setup.folders.missing"), type="warning")
            return False
        accounts.save({"inbox_folder": st["inbox_folder"], "sent_folder": st["sent_folder"]}, st["account_id"])
        return True

    # --- Step: Thunderbird ----------------------------------------------------------------------
    tb = {}

    def r_thunderbird():
        profiles = thunderbird.find_profiles()
        options = {str(p.path): p.name + (" " + t("setup.thunderbird.default") if p.is_default else "")
                   for p in profiles}
        if st["tb_profile"] and st["tb_profile"] not in options:
            options[st["tb_profile"]] = st["tb_profile"]
        options[""] = t("setup.thunderbird.none")
        if not st["tb_profile"] and profiles:
            st["tb_profile"] = str(profiles[0].path)
        ui.markdown(t("setup.thunderbird.text"))
        if not profiles:
            ui.label(t("setup.thunderbird.not_found")).classes("muted")
        ui.select(options, label=t("setup.thunderbird.profile"), with_input=True, new_value_mode="add-unique",
                  on_change=lambda: fill_sources()).bind_value(st, "tb_profile").classes("w-full").props("outlined")
        tb["col"] = ui.column().classes("w-full")
        fill_sources()

    def fill_sources():
        col = tb.get("col")
        if col is None:
            return
        col.clear()
        path = Path(st["tb_profile"]) if st["tb_profile"] else None
        if not path or not path.is_dir():
            return
        try:
            books = thunderbird.read_address_books(path)
        except Exception as exc:
            with col:
                ui.label(t("setup.thunderbird.read_failed", error=exc)).classes("text-negative")
            return
        options = {"": t("setup.thunderbird.members_none")}
        for b in books:
            for list_name, emails in b.lists.items():
                options[f"list:{b.file}:{list_name}"] = t("setup.thunderbird.members_list", list=list_name,
                                                          count=len(emails), book=b.name)
        for b in books:
            options[f"book:{b.file}"] = t("setup.thunderbird.members_book", book=b.name, count=len(b.contacts))
        if st["tb_member_source"] not in options:
            st["tb_member_source"] = ""
        with col:
            total = sum(len(b.contacts) for b in books)
            ui.label(t("setup.thunderbird.found", books=len(books), count=total)).classes("muted text-sm")
            ui.select(options, label=t("setup.thunderbird.members")).bind_value(st, "tb_member_source").classes(
                "w-full").props("outlined")

    async def n_thunderbird() -> bool:
        config.save_local(tb_profile=st["tb_profile"] or "")
        db.set_settings(tb_member_source=st["tb_member_source"] or "")
        if st["tb_profile"]:
            ok, result = await layout.background(people.thunderbird_import, Path(st["tb_profile"]),
                                                 st["tb_member_source"] or "")
            if not ok:
                return False
            ui.notify(t("setup.thunderbird.imported", contacts=result["contacts"], members=result["members"]),
                      type="positive")
        return True

    # --- Step: sender ---------------------------------------------------------------------------
    def r_sender():
        ui.label(t("setup.account", email=st["email"])).classes("font-medium")
        ui.input(t("setup.sender.name"), placeholder=t("setup.sender.name_placeholder")).bind_value(
            st, "sender_name").classes("w-full").props("outlined")
        ui.textarea(t("setup.sender.signature"), placeholder=t("setup.sender.signature_placeholder")).bind_value(
            st, "signature").classes("w-full").props("outlined autogrow")
        with ui.row().classes("items-center gap-3"):
            ui.label(t("setup.sender.accent"))
            ui.color_input(value=st["accent_color"], on_change=lambda e: st.update(accent_color=e.value)).props(
                "outlined dense")

    def n_sender() -> bool:
        accounts.save({"sender_name": (st["sender_name"] or "").strip(),
                       "signature": (st["signature"] or "").rstrip()}, st["account_id"])
        db.set_settings(accent_color=st["accent_color"] or layout.ACCENT)
        return True

    # --- Step: done -----------------------------------------------------------------------------
    def r_done():
        with ui.column().classes("items-center w-full gap-2 py-4"):
            ui.icon("check_circle", color="positive").classes("text-7xl")
            ui.label(t("setup.done.title")).classes("text-xl font-semibold")
            for a in accounts.all_accounts():
                ui.label(t("setup.account", email=a["email"])).classes("muted")
            ui.label(t("setup.done.data", path=st["data_dir"])).classes("muted break-all text-center")

    def finish():
        db.set_settings(setup_done="1")
        config.save_local(local_setup_done=True)
        ui.navigate.to("/")

    def finish_settings():
        ui.notify(t("common.saved"), type="positive")
        ui.navigate.to("/settings")

    steps = {s.key: s for s in [
        Step("welcome", t("setup.welcome.title"), "", r_welcome, next_label=t("setup.welcome.next")),
        Step("data_dir", t("setup.data.title"), t("setup.data.subtitle"), r_data, n_data),
        Step("email", t("setup.email.title"), t("setup.email.subtitle"), r_email, n_email, skip=second_system),
        Step("server", t("setup.server.title"), t("setup.server.subtitle"), r_server, n_server, skip=second_system),
        Step("password", t("setup.password.title"), t("setup.password.subtitle"), r_password, n_password,
             skip=second_system, next_label=t("setup.password.next")),
        Step("passwords", t("setup.passwords.title"), t("setup.passwords.subtitle"), r_passwords, n_passwords,
             skip=not_second_system, next_label=t("setup.passwords.next")),
        Step("folders", t("setup.folders.title"), "", r_folders, n_folders, skip=second_system),
        Step("thunderbird", t("setup.thunderbird.title"), "", r_thunderbird, n_thunderbird),
        Step("sender", t("setup.sender.title"), t("setup.sender.subtitle"), r_sender, n_sender, skip=second_system),
        Step("done", t("setup.done.step"), "", r_done, next_label=t("setup.done.next")),
    ]}

    def only_steps(*keys: str) -> list[Step]:
        chosen = [steps[k] for k in keys]
        for s in chosen:
            s.skip = None
        return chosen

    def back_to_settings():
        ui.navigate.to("/settings")

    if adding:
        flow = only_steps("email", "server", "password", "folders", "sender")
        flow[0].title, flow[0].subtitle = t("setup.add.title"), t("setup.add.subtitle")
        flow[-1].next_label = t("setup.add.next")
        wizard = Wizard(flow, finish_settings, on_cancel=back_to_settings)
    elif single and only in steps:
        if only == "email":  # new address or servers: test the connection right away
            flow = only_steps("email", "server", "password")
            flow[-1].next_label = t("setup.test_and_save")
        elif only == "server":
            flow = only_steps("server", "password")
            flow[-1].next_label = t("setup.test_and_save")
        else:
            flow = only_steps(only)
            flow[0].next_label = t("common.save")
        wizard = Wizard(flow, finish_settings, on_cancel=back_to_settings)
    else:
        wizard = Wizard(list(steps.values()), finish)
