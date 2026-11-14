"""Start page of the active event."""
from __future__ import annotations

from datetime import date

from nicegui import ui

from .. import db, templates
from ..i18n import format_date, t
from ..services import events, fetching
from . import layout


async def fetch_mails(event: dict, button=None) -> None:
    if button:
        button.props("loading")
    try:
        ok, result = await layout.background(fetching.fetch, event)
    finally:
        if button:
            button.props(remove="loading")
    if not ok:
        return
    db.set_settings(last_fetch=db.now())
    if result.new:
        ui.notify(t("overview.fetched", count=result.new), type="positive")
    else:
        ui.notify(t("overview.nothing_new"), type="info")
    for email, exc in result.errors:
        ui.notify(f"{email}: {layout.explain(exc)}", type="negative", multi_line=True, timeout=10000)
    if not result.errors:
        ui.navigate.reload()
    else:
        ui.timer(4, ui.navigate.reload, once=True)


@ui.page("/")
def index() -> None:
    if layout.guard() is not False:
        ui.navigate.to("/overview")


@ui.page("/overview")
def page() -> None:
    event = layout.guard()
    if event is False:
        return
    with layout.frame("/overview", t("nav.overview")) as event:
        s = events.stats(event["id"])

        with ui.card().classes("w-full card-soft p-6").props("flat"):
            with ui.row().classes("w-full items-start gap-6"):
                with ui.column().classes("gap-1 grow"):
                    ui.label(event["title"]).classes("text-xl font-bold")
                    when = templates.time_range(event) or t("overview.no_time")
                    ui.label(f"{format_date(date.fromisoformat(event['date']), 'date_long')} · {when}").classes(
                        "muted")
                    if event["venue"] or event["address"]:
                        ui.label(", ".join(p for p in (event["venue"], event["address"]) if p)).classes("muted")
                with ui.column().classes("items-end gap-1"):
                    btn = ui.button(t("common.fetch_mails"), icon="sync")
                    btn.on_click(lambda: fetch_mails(event, btn))
                    last = db.get_setting("last_fetch")
                    if last:
                        ui.label(t("overview.last_fetch", when=format_date(last, "day_time"))).classes(
                            "muted text-xs")

        if date.fromisoformat(event["date"]) < date.today():
            with ui.card().classes("w-full hint-info").props("flat"):
                with ui.row().classes("w-full items-center no-wrap gap-3"):
                    ui.icon("celebration", color="primary").classes("text-2xl")
                    ui.label(t("overview.event_over")).classes("grow")
                    ui.button(t("events.archive_and_new"), on_click=lambda: ui.navigate.to("/events")).props("flat")

        with ui.grid(columns="repeat(auto-fit, minmax(180px, 1fr))").classes("w-full gap-4"):
            stat(t("status.registered"), s["registered"], "how_to_reg", "primary",
                 t("overview.people", count=s["people"]))
            stat(t("overview.to_review"), s["to_review"], "mark_email_unread", "orange-7", t("overview.new_mails"),
                 "/inbox")
            stat(t("overview.open"), s["open"], "schedule_send", "info", t("overview.without_reply"), "/list")
            stat(t("status.cancelled"), s["cancelled"], "event_busy", "grey-7", "")

        if s["to_review"]:
            with ui.card().classes("w-full card-soft p-5").props("flat"):
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.icon("inbox", color="primary").classes("text-3xl")
                    ui.label(t("overview.waiting", count=s["to_review"])).classes("grow text-lg")
                    ui.button(t("overview.review_now"), on_click=lambda: ui.navigate.to("/inbox")).props(
                        "icon-right=arrow_forward")
        elif s["open"]:
            with ui.card().classes("w-full card-soft p-5").props("flat"):
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.icon("send", color="primary").classes("text-3xl")
                    ui.label(t("overview.unanswered", count=s["open"])).classes("grow text-lg")
                    ui.button(t("overview.to_list"), on_click=lambda: ui.navigate.to("/list")).props(
                        "icon-right=arrow_forward")


def stat(label: str, value: int, icon: str, color: str, hint: str, href: str | None = None) -> None:
    card = ui.card().classes("card-soft p-5 gap-1" + (" cursor-pointer" if href else "")).props("flat")
    if href:
        card.on("click", lambda: ui.navigate.to(href))
    with card:
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon, color=color).classes("text-xl")
            ui.label(label).classes("muted")
        ui.label(str(value)).classes("stat-value")
        if hint:
            ui.label(hint).classes("muted text-sm")
