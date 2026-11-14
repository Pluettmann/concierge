"""Settings: every wizard step on its own, plus sync, backups and updates."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nicegui import app, run, ui

from .. import config, db, i18n, updater, version
from ..i18n import format_date, t
from ..services import accounts, fetching
from . import layout

# Per-account settings: (wizard step, icon, title key, summary)
ACCOUNT_SECTIONS = [
    ("server", "dns", "settings.section.server", lambda a: f"{a['imap_host']} · {a['smtp_host']}"),
    ("password", "key", "settings.section.password", lambda a: t("settings.password_stored")
     if config.get_password(a["email"]) else t("settings.password_missing")),
    ("folders", "folder", "settings.section.folders", lambda a: f"{a['inbox_folder']} / {a['sent_folder']}"),
    ("sender", "badge", "settings.section.sender", lambda a: a["sender_name"] or "–"),
]
GLOBAL_SECTIONS = [
    ("thunderbird", "contacts", "settings.section.thunderbird",
     lambda: str(config.thunderbird_profile() or t("settings.not_connected"))),
    ("data_dir", "save", "settings.section.data_dir", lambda: str(config.data_dir())),
]


def _item(icon: str, title: str, caption: str, href: str) -> None:
    with ui.item(on_click=lambda: ui.navigate.to(href)).props("clickable").classes("py-3"):
        with ui.item_section().props("avatar"):
            ui.icon(icon, color="primary")
        with ui.item_section():
            ui.item_label(title)
            ui.item_label(caption).props("caption").classes("ellipsis")
        with ui.item_section().props("side"):
            ui.icon("chevron_right")


@ui.page("/settings")
def page() -> None:
    if layout.guard(need_event=False) is False:
        return
    with layout.frame("/settings", t("nav.settings")):
        s = db.get_settings()
        all_accounts = accounts.all_accounts()

        with ui.row().classes("w-full items-center"):
            ui.label(t("settings.accounts")).classes("text-lg font-semibold grow")
            ui.button(t("settings.add_account"), icon="add", on_click=lambda: ui.navigate.to("/setup?new=1")).props(
                "flat")
        if len(all_accounts) > 1:
            ui.label(t("settings.accounts_hint")).classes("muted text-sm")
        for i, a in enumerate(all_accounts):
            with ui.card().classes("w-full card-soft p-0").props("flat"):
                with ui.row().classes("w-full items-center px-4 pt-3"):
                    ui.icon("alternate_email", color="primary").classes("text-xl")
                    ui.label(a["email"]).classes("font-semibold grow")
                    if i == 0 and len(all_accounts) > 1:
                        ui.badge(t("settings.primary"), color="primary").props("outline rounded").tooltip(
                            t("settings.primary_tooltip"))
                    if len(all_accounts) > 1:
                        ui.button(icon="delete", on_click=lambda a=a: remove_account(a)).props(
                            "flat round dense color=grey-7").tooltip(t("settings.remove_account"))
                with ui.list().classes("w-full").props("separator"):
                    for key, icon, title, value in ACCOUNT_SECTIONS:
                        _item(icon, t(title), value(a), f"/setup?only={key}&account={a['id']}")

        ui.label(t("settings.general")).classes("text-lg font-semibold mt-2")
        with ui.card().classes("w-full card-soft p-0").props("flat"):
            with ui.list().classes("w-full").props("separator"):
                for key, icon, title, value in GLOBAL_SECTIONS:
                    _item(icon, t(title), value(), f"/setup?only={key}")

        with ui.card().classes("w-full card-soft p-6 gap-3").props("flat"):
            ui.label(t("settings.appearance")).classes("text-lg font-semibold")
            ui.toggle({v: t(f"settings.theme.{v}") for v in config.THEMES}, value=config.theme(),
                      on_change=lambda e: layout.set_theme(e.value)).props("no-caps unelevated")
            ui.label(t("settings.theme_hint")).classes("muted text-sm")
            languages = i18n.available_languages()
            if len(languages) > 1:
                ui.select({lang: i18n.catalog(lang).get("language.name", lang) for lang in languages},
                          value=i18n.current_language(), label=t("settings.language"),
                          on_change=lambda e: change_language(e.value)).props("outlined dense").classes("w-60")

        with ui.card().classes("w-full card-soft p-6 gap-3").props("flat"):
            ui.label(t("settings.sending")).classes("text-lg font-semibold")
            pause = {"v": float(s.get("send_pause") or 3)}
            ui.number(t("settings.send_pause"), min=0, max=60, step=1,
                      on_change=lambda: db.set_settings(send_pause=pause["v"])).bind_value(pause, "v").props(
                "outlined dense").classes("w-72")
            ui.label(t("settings.send_pause_hint")).classes("muted text-sm")

        with ui.card().classes("w-full card-soft p-6 gap-3").props("flat"):
            ui.label(t("settings.sync")).classes("text-lg font-semibold")
            ui.label(t("settings.sync_hint")).classes("muted text-sm")

            async def resync():
                for a in accounts.all_accounts():
                    accounts.save({"correspondence_synced_until": ""}, a["id"])
                ok, _ = await layout.background(lambda: (fetching.sync_thunderbird(), fetching.sync_all_sent()))
                if ok:
                    ui.notify(t("settings.sync_done"), type="positive")
            ui.button(t("settings.sync_now"), icon="sync", on_click=resync).props("outline")

        with ui.card().classes("w-full card-soft p-6 gap-3").props("flat"):
            ui.label(t("settings.backups")).classes("text-lg font-semibold")
            ui.label(t("settings.backups_hint")).classes("muted text-sm")
            data_dir = config.data_dir()
            for b in (db.backups(data_dir) if data_dir else [])[:db.BACKUPS_KEEP]:
                with ui.row().classes("w-full items-center"):
                    ui.icon("history", color="grey-6")
                    ui.label(format_date(db.backup_time(b), "datetime")).classes("grow")
                    ui.button(t("settings.restore"), on_click=lambda b=b: restore(b)).props("flat dense")

            def backup_now():
                db.backup(config.data_dir())
                ui.navigate.reload()
            ui.button(t("settings.backup_now"), icon="backup", on_click=backup_now).props("outline")

        update_card()


def change_language(language: str) -> None:
    config.save_local(language=language)
    i18n.set_language(language)
    ui.navigate.reload()


def update_card() -> None:
    with ui.card().classes("w-full card-soft p-6 gap-3").props("flat"):
        with ui.row().classes("w-full items-center"):
            ui.label(t("update.title")).classes("text-lg font-semibold grow")
            ui.badge(t("update.version", version=version.VERSION), color="primary").props("outline rounded")
        ui.label(t("update.hint")).classes("muted text-sm")
        status = {"text": ""}

        @ui.refreshable
        def online() -> None:
            info = updater.available
            if info:
                with ui.row().classes("w-full items-center hint-info rounded-xl px-4 py-3 gap-3"):
                    ui.icon("new_releases", color="primary").classes("text-2xl")
                    ui.label(t("update.available", version=info.version)).classes("grow font-medium")
                    btn = ui.button(t("update.download"), icon="download")
                    btn.on_click(lambda: fetch(btn, info))
            else:
                with ui.row().classes("items-center gap-3"):
                    btn = ui.button(t("update.check"), icon="refresh").props("outline")
                    btn.on_click(lambda: search(btn))
                    if status["text"]:
                        ui.label(status["text"]).classes("muted text-sm")

        async def search(btn) -> None:
            btn.props("loading")
            try:
                info = await run.io_bound(updater.find_latest)
            except updater.UpdateError as exc:
                btn.props(remove="loading")
                ui.notify(str(exc), type="warning", multi_line=True)
                return
            status["text"] = "" if info else t("update.up_to_date", time=format_date(datetime.now(), "time"))
            online.refresh()

        async def fetch(btn, info: updater.AvailableUpdate) -> None:
            btn.props("loading")
            try:
                update = await run.io_bound(updater.download, info)
            except updater.UpdateError as exc:
                layout.show_errors(t("update.failed_title"), [str(exc)])
                return
            finally:
                btn.props(remove="loading")
            confirm_update(update)

        online()

        async def on_upload(e) -> None:
            data = await e.file.read()
            upload.reset()
            try:
                update = updater.check(data)
            except updater.UpdateError as exc:
                layout.show_errors(t("update.failed_title"), [str(exc)])
                return
            confirm_update(update)

        with ui.expansion(t("update.offline"), icon="usb").classes("w-full").props("dense"):
            upload = ui.upload(label=t("update.choose_file"), auto_upload=True, max_file_size=updater.MAX_SIZE,
                               on_upload=on_upload,
                               on_rejected=lambda: ui.notify(t("update.error.too_large"), type="negative")).props(
                'accept=".zip" flat bordered color=primary').classes("w-full max-w-md")


def confirm_update(update: updater.Update) -> None:
    with ui.dialog().props("persistent") as dialog, ui.card().classes("w-[600px] max-w-full p-6 gap-3"):
        title = (t("update.reinstall_title", version=update.version) if update.is_reinstall
                 else t("update.confirm_title", version=update.version))
        ui.label(title).classes("text-xl font-bold")
        ui.label(t("update.installed", version=version.VERSION)).classes("muted")
        if update.notes:
            with ui.scroll_area().classes("w-full max-h-72 hint-info rounded-xl"):
                ui.markdown(update.notes).classes("text-sm px-2")
        ui.label(t("update.restart_hint")).classes("muted text-sm")
        status = ui.label().classes("font-medium")
        with ui.row().classes("w-full justify-end gap-2"):
            cancel = ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            go = ui.button(t("update.install"), icon="system_update_alt")

    async def do_update():
        go.props("loading")
        cancel.disable()
        try:
            await run.io_bound(updater.install, update)
        except updater.UpdateError as exc:
            dialog.close()
            layout.show_errors(t("update.install_failed_title"), [str(exc)])
            return
        status.set_text(t("update.restarting"))
        updater.restart()
        ui.timer(1.0, app.shutdown, once=True)

    go.on_click(do_update)
    dialog.open()


def remove_account(account: dict) -> None:
    with ui.dialog() as dialog, ui.card().classes("p-6"):
        ui.label(t("settings.remove_title", email=account["email"])).classes("text-lg font-semibold")
        ui.label(t("settings.remove_text")).classes("muted")

        def do():
            accounts.delete(account["id"])
            dialog.close()
            ui.navigate.reload()
        with ui.row().classes("w-full justify-end mt-3"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("settings.remove_confirm"), color="negative", on_click=do)
    dialog.open()


def restore(backup: Path) -> None:
    with ui.dialog() as dialog, ui.card().classes("p-6"):
        ui.label(t("settings.restore_title")).classes("text-lg font-semibold")
        ui.label(t("settings.restore_text")).classes("muted")

        def do():
            db.backup(config.data_dir())
            db.restore_backup(config.data_dir(), backup)
            db.open_db(config.data_dir())
            dialog.close()
            ui.notify(t("settings.restored"), type="positive")
            ui.navigate.to("/")
        with ui.row().classes("w-full justify-end mt-3"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("settings.restore"), color="negative", on_click=do)
    dialog.open()
