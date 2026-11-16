"""Shared page frame, colours and UI helpers."""
from __future__ import annotations

import imaplib
import smtplib
import socket
import ssl
from contextlib import contextmanager

from imap_tools import MailboxLoginError
from nicegui import app, run, ui

from .. import config, db, updater
from ..i18n import format_date, t
from ..services import accounts, events

ACCENT = "#4f46e5"

# Hint shown on every page, e.g. if the app was not closed properly last time (set in main.py).
startup_warning: str | None = None

# The event last chosen in the event selector (the app has a single local user).
_chosen_event: dict[str, int | None] = {"id": None}

# (path, icon, translation key)
NAV = [
    ("/overview", "space_dashboard", "nav.overview"),
    ("/inbox", "inbox", "nav.inbox"),
    ("/list", "groups", "nav.list"),
    ("/people", "contacts", "nav.people"),
    ("/templates", "drafts", "nav.templates"),
    ("/events", "event", "nav.events"),
    ("/settings", "settings", "nav.settings"),
]

# value -> colour; labels come from "familiarity.<value>" and "reply_status.<value>"
FAMILIARITY_COLORS = {"member": "positive", "known": "info", "unknown": "grey-6"}
REPLY_STATUS_COLORS = {"open": "orange-7", "confirmation_sent": "positive", "rejection_sent": "grey-7",
                       "personal_reply_sent": "info"}

CSS = """
body { background: #f6f7fb; }
body.body--dark { background: #0f1117; }
/* Controls drawn by the browser (number arrows, scrollbars, date pickers) follow the actual theme,
   also after switching without reloading. */
body.body--dark, html:has(> body.body--dark) { color-scheme: dark; }
body.body--light, html:has(> body.body--light) { color-scheme: light; }
.q-card { border-radius: 16px; }
.q-btn { text-transform: none; border-radius: 10px; font-weight: 500; }
.q-tab__label { text-transform: none; }
.card-soft { box-shadow: 0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08); }
.body--dark .card-soft { box-shadow: none; border: 1px solid #262a36; }
.page { width: 100%; max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
.page-narrow { width: 100%; max-width: 640px; margin: 0 auto; padding: 32px 16px 48px; }
.muted { color: #6b7280; }
.body--dark .muted { color: #9ca3af; }
.mail-text { white-space: pre-wrap; font-size: 0.95rem; line-height: 1.55; }
.stat-value { font-size: 2.1rem; font-weight: 700; line-height: 1.1; }
.app-bar { background: #ffffff; color: #111827; border-color: rgba(0,0,0,.07) !important; }
.body--dark .app-bar { background: #151821; color: #e5e7eb; border-color: #262a36 !important; }
.hint-info { background: #eef2ff; }
.body--dark .hint-info { background: #1e2236; }
.hint-warn { background: #fffbeb; }
.body--dark .hint-warn { background: #2b2414; }
.nav-item { color: inherit; text-decoration: none; }
.nav-item:hover { background: rgba(0,0,0,.04); }
.body--dark .nav-item:hover { background: rgba(255,255,255,.05); }
.nav-active { background: rgba(79,70,229,.10); color: #4f46e5; font-weight: 600; }
.body--dark .nav-active { background: rgba(129,140,248,.16); color: #a5b4fc; }
iframe.preview { width: 100%; border: 0; border-radius: 12px; background: #f4f5f7; }
"""


def setup_theme() -> None:
    ui.colors(primary=ACCENT, secondary="#0ea5e9", positive="#16a34a", negative="#dc2626", info="#2563eb")
    ui.add_head_html(head_html(config.theme()))
    ui.add_css(CSS)


def head_html(theme: str) -> str:
    """Against flashing on page changes: ui.add_css is inserted via JavaScript and Quasar sets
    body--dark/--light only after loading. These rules are static in <head> and apply from the
    first paint, matching the chosen theme."""
    # color-scheme as CSS instead of <meta>: NiceGUI sets its own meta tag (with "normal" for
    # automatic) that would come before ours and win.
    if theme == "dark":
        return '<style>:root { color-scheme: dark; } html, body:not(.body--light) { background: #0f1117; }</style>'
    if theme == "light":
        return '<style>:root { color-scheme: light; } html, body:not(.body--dark) { background: #f6f7fb; }</style>'
    return ('<style>:root { color-scheme: light dark; } @media (prefers-color-scheme: dark) {'
            ' html, body:not(.body--light) { background: #0f1117; } }</style>')


def set_theme(value: str) -> None:
    """Stores the theme and switches immediately (also for all further pages)."""
    config.save_local(theme=value)
    app.config.dark = config.dark_value(value)
    ui.dark_mode(app.config.dark)


def is_configured() -> bool:
    return db.is_open() and db.get_setting("setup_done") == "1" and config.load_local().get("local_setup_done")


def current_event(event_id: int | None = None) -> dict | None:
    """The event a page shows: the requested one, else the one chosen last, else the next upcoming.

    A requested active event is remembered for the following pages; archived ones are only shown.
    """
    if event_id:
        event = events.get(event_id)
        if event:
            if event["status"] == events.ACTIVE:
                _chosen_event["id"] = event["id"]
            return event
    if _chosen_event["id"]:
        event = events.get(_chosen_event["id"])
        if event and event["status"] == events.ACTIVE:
            return event
    return events.active()


def choose_event(event_id: int | None) -> None:
    _chosen_event["id"] = event_id


def guard(need_event: bool = True, event_id: int | None = None) -> dict | bool | None:
    """Redirects to the setup or to a new event. Returns False if it redirected, otherwise the
    event the page shows (see current_event)."""
    if not is_configured():
        ui.navigate.to("/setup")
        return False
    events.archive_past()
    event = current_event(event_id)
    if need_event and event is None:
        ui.navigate.to("/events/new")
        return False
    return event


def event_selector(path: str, event: dict) -> None:
    """Selector for the event a page shows; only when several events are active."""
    active = events.active_events()
    if len(active) < 2 or event["status"] != events.ACTIVE:
        return
    options = {e["id"]: t("header.event", title=e["title"], date=format_date(e["date"])) for e in active}
    ui.select(options, value=event["id"], label=t("event.selector"),
              on_change=lambda e: ui.navigate.to(f"{path}?event={e.value}")).props(
        "outlined dense options-dense").classes("w-80 max-w-full")


def page_title(title: str) -> None:
    ui.page_title(f"{title} · {config.APP_NAME}")


@contextmanager
def frame(path: str, title: str, event: dict | None = None):
    """Page frame with header and navigation. Yields the event shown (by default the current one)."""
    setup_theme()
    page_title(title)
    if event is None and db.is_open():
        event = current_event()
    to_review = events.to_review_total() if db.is_open() else 0

    with ui.header().classes("app-bar").style("border-bottom: 1px solid"):
        with ui.row().classes("w-full items-center no-wrap px-2 py-1"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round").classes("lt-md")
            ui.icon("room_service", color="primary").classes("text-2xl")
            ui.label(config.APP_NAME).classes("text-lg font-semibold")
            if event:
                ui.label("·").classes("muted")
                ui.label(t("header.event", title=event["title"], date=format_date(event["date"]))).classes(
                    "muted ellipsis")
            ui.space()
            ui.button(icon="power_settings_new", on_click=quit_app).props("flat round").tooltip(t("header.quit"))

    with ui.left_drawer(value=True).props("width=240 breakpoint=1023").classes("app-bar").style(
            "border-right: 1px solid") as drawer, ui.column().classes("w-full gap-1 pt-2"):
        for href, icon, key in NAV:
            active = path == href
            with ui.link(target=href).classes(
                    "nav-item w-full rounded-xl px-3 py-2 flex items-center gap-3"
                    + (" nav-active" if active else "")):
                ui.icon(icon).classes("text-xl")
                ui.label(t(key)).classes("grow")
                if href == "/inbox" and to_review:
                    ui.badge(str(to_review), color="primary").props("rounded")
                if href == "/settings" and updater.available:
                    ui.badge(t("nav.update_badge"), color="positive").props("rounded").tooltip(
                        t("update.available", version=updater.available.version))

    with ui.column().classes("page gap-5"):
        if startup_warning:
            with ui.card().classes("w-full hint-warn").props("flat bordered") as warning_card:
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.icon("warning", color="warning").classes("text-2xl")
                    ui.label(startup_warning).classes("grow")

                    def dismiss():
                        global startup_warning
                        startup_warning = None
                        warning_card.delete()
                    ui.button(icon="close", on_click=dismiss).props("flat round dense")
        ui.label(title).classes("text-2xl font-bold")
        yield event


def badge(key: str, color: str):
    return ui.badge(t(key), color=color).props("rounded").classes("px-2 py-1")


def familiarity_badge(value: str):
    return badge(f"familiarity.{value}", FAMILIARITY_COLORS.get(value, "grey"))


def account_chip(account_id: int | None, direction: str = "") -> None:
    """Shows which account a mail came in through or goes out from (only with several accounts)."""
    if len(accounts.all_accounts()) < 2:
        return
    account = accounts.get_or_primary(account_id)
    if account:
        text = t(direction, email=account["email"]) if direction else account["email"]
        ui.chip(text, icon="alternate_email").props("dense outline color=grey-7").classes("text-xs")


def quit_app() -> None:
    with ui.dialog() as dialog, ui.card().classes("p-6"):
        ui.label(t("quit.title")).classes("text-lg font-semibold")
        ui.label(t("quit.text")).classes("muted")
        with ui.row().classes("w-full justify-end mt-4"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("quit.confirm"), on_click=lambda: (ui.notify(t("quit.done")), app.shutdown()),
                      color="negative")
    dialog.open()


# --- Error texts and background tasks --------------------------------------------------------

def explain(exc: BaseException) -> str:
    if isinstance(exc, (MailboxLoginError, smtplib.SMTPAuthenticationError)):
        return t("error.login")
    if isinstance(exc, imaplib.IMAP4.error) and "auth" in str(exc).lower():
        return t("error.login")
    if isinstance(exc, socket.gaierror):
        return t("error.host_not_found")
    if isinstance(exc, ConnectionRefusedError):
        return t("error.connection_refused")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return t("error.timeout")
    if isinstance(exc, ssl.SSLError):
        return t("error.ssl")
    if isinstance(exc, smtplib.SMTPException):
        return t("error.smtp", error=exc)
    return f"{type(exc).__name__}: {exc}"


def show_errors(title: str, errors: list[str], hint: str = "") -> None:
    """Error list that stays until closed (notifications disappear too quickly to read them)."""
    with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full p-6 gap-3"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("error", color="negative").classes("text-2xl")
            ui.label(title).classes("text-lg font-semibold")
        with ui.scroll_area().classes("w-full max-h-80"):
            for err in errors:
                ui.label(err).classes("text-sm break-all py-1")
        if hint:
            ui.label(hint).classes("muted text-sm")
        ui.label(t("error.log_hint", path=config.LOG_DIR / "app.log")).classes("muted text-xs break-all")
        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.close"), on_click=dialog.close)
    dialog.open()


async def ask_password(email: str) -> bool:
    with ui.dialog().props("persistent") as dialog, ui.card().classes("p-6 w-96"):
        ui.label(t("password_dialog.title")).classes("text-lg font-semibold")
        ui.label(t("password_dialog.text", email=email)).classes("muted")
        pw = ui.input(t("common.password"), password=True, password_toggle_button=True).classes("w-full").props(
            "autofocus")
        remember = ui.checkbox(t("password_dialog.remember"), value=True)
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button(t("common.cancel"), on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(t("common.ok"), on_click=lambda: dialog.submit(True))
        pw.on("keydown.enter", lambda: dialog.submit(True))
    ok = await dialog
    if ok and pw.value:
        if remember.value:
            if not config.set_password(email, pw.value):
                ui.notify(t("password_dialog.no_vault"), type="warning")
        else:
            config.remember_for_session(email, pw.value)
        return True
    return False


async def background(fn, *args, **kwargs):
    """Runs a blocking task in the background; asks for passwords if needed (one per account) and
    shows errors in plain words. Returns (ok, result)."""
    for _ in range(len(accounts.all_accounts()) + 1):
        try:
            return True, await run.io_bound(fn, *args, **kwargs)
        except accounts.MissingPassword as exc:
            if not await ask_password(exc.email):
                return False, None
        except Exception as exc:
            ui.notify(explain(exc), type="negative", multi_line=True, timeout=8000)
            return False, None
    return False, None


def preview_frame(html: str, height: int = 520):
    frame = ui.element("iframe").classes("preview").style(f"height:{height}px")
    set_preview(frame, html)
    return frame


def set_preview(frame, html: str) -> None:
    frame._props["srcdoc"] = html
    frame.update()


def empty_state(icon: str, title: str, text: str = ""):
    with ui.column().classes("w-full items-center py-16 gap-2"):
        ui.icon(icon, color="grey-5").classes("text-6xl")
        ui.label(title).classes("text-lg font-semibold")
        if text:
            ui.label(text).classes("muted text-center")
