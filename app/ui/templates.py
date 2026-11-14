"""Templates for confirmation and rejection with a live preview."""
from __future__ import annotations

from datetime import date, timedelta

from nicegui import ui

from .. import db, templates
from ..i18n import t
from ..services import accounts, sending
from . import layout


def sample_event() -> dict:
    return {"title": t("sample.title"), "date": (date.today() + timedelta(days=14)).isoformat(),
            "start_time": "19:00", "end_time": "22:00", "venue": t("sample.venue"), "address": t("sample.address"),
            "map_link": "https://www.google.com/maps", "info": t("sample.info"),
            "ics_uid": "sample@concierge", "ics_sequence": 0}


def sample_person() -> dict:
    return {"first_name": t("sample.first_name"), "last_name": t("sample.last_name"), "email": "anna@example.org"}


@ui.page("/templates")
def page(kind: str = templates.CONFIRMATION) -> None:
    if kind not in templates.KINDS:
        kind = templates.CONFIRMATION
    if layout.guard(need_event=False) is False:
        return
    with layout.frame("/templates", t("nav.templates")) as event:
        sample = event or sample_event()
        person = sample_person()
        with ui.tabs(value=kind, on_change=lambda e: ui.navigate.to(f"/templates?kind={e.value}")).props(
                "dense no-caps inline-label align=left"):
            ui.tab(templates.CONFIRMATION, t("kind.confirmation"), icon="event_available")
            ui.tab(templates.REJECTION, t("kind.rejection"), icon="event_busy")

        stored = templates.get(kind)
        st = {"subject": stored["subject"], "html": stored["html"]}

        with ui.row().classes("w-full gap-5 items-start no-wrap max-md:flex-wrap"):
            with ui.column().classes("grow basis-1/2 gap-3 min-w-[320px]"):
                # These trigger refresh(); they are connected once refresh() is defined.
                fields = [
                    ui.input(t("common.subject")).bind_value(st, "subject").classes("w-full").props("outlined"),
                    ui.editor().bind_value(st, "html").classes("w-full").style("min-height: 300px"),
                ]
                ui.label(t("templates.placeholders")).classes("muted text-sm")
                with ui.row().classes("gap-1 flex-wrap"):
                    for name in templates.PLACEHOLDERS:
                        token = "{{ " + name + " }}"
                        ui.chip(token, on_click=lambda tok=token: (ui.clipboard.write(tok),
                                                                   ui.notify(t("templates.copied", token=tok)))
                                ).props("clickable outline dense").tooltip(t(f"placeholder.{name}"))
                error = ui.label().classes("text-negative text-sm")
                with ui.row().classes("w-full gap-2"):
                    ui.button(t("common.save"), icon="save", on_click=lambda: save())
                    ui.button(t("templates.test"), icon="outgoing_mail", on_click=lambda: test()).props("outline")
                    ui.space()
                    ui.button(t("templates.reset"), icon="restart_alt", on_click=lambda: reset()).props("flat")
            with ui.column().classes("grow basis-1/2 gap-2 min-w-[320px]"):
                hint = t("templates.preview") if event else t("templates.preview_sample")
                if len(accounts.all_accounts()) > 1:
                    hint += " " + t("templates.preview_accounts")
                ui.label(hint).classes("muted text-sm")
                subject_label = ui.label().classes("font-medium")
                frame = layout.preview_frame("", 640)

        def preview_settings() -> dict:
            # Sender name and signature come from the sending account; the preview shows the first one.
            account = accounts.primary()
            return accounts.with_globals(account) if account else db.get_settings()

        def refresh():
            try:
                subject, html, _ = templates.render(kind, sample, person, 1, preview_settings(),
                                                    st["subject"] or "", st["html"] or "")
                error.text = ""
                subject_label.text = subject
                layout.set_preview(frame, html)
            except templates.RenderError as exc:
                error.text = str(exc)

        def save():
            try:
                templates.render(kind, sample, person, 1, preview_settings(), st["subject"], st["html"])
            except templates.RenderError as exc:
                ui.notify(str(exc), type="negative")
                return
            templates.save(kind, st["subject"], st["html"])
            ui.notify(t("templates.saved"), type="positive")

        def reset():
            st["subject"], st["html"] = templates.default(kind)
            refresh()

        async def test():
            save()
            ok, address = await layout.background(sending.send_test, kind, sample)
            if ok:
                ui.notify(t("templates.test_sent", email=address), type="positive")

        for element in fields:
            element.on_value_change(refresh)
        refresh()
