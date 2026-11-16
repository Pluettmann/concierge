"""Inbox review: confirm or correct the suggestions of the classifier.

The inbox shows the new mails of all active events together. Each card shows the event the mail
was assigned to; it can be changed, and a registration can apply to several events at once.
"""
from __future__ import annotations

import json

from nicegui import ui

from ..classifier import CANCELLATION, OTHER, REGISTRATION
from ..i18n import format_date, reasons_text, t
from ..services import events, people, registrations
from ..services.registrations import BLOCKED, VIEW_BLOCKED, VIEW_DONE, VIEW_OPEN, VIEWS
from . import layout
from .overview import fetch_mails

# classification -> (colour, icon); labels come from "classification.<value>"
CLASSIFICATION_STYLE = {
    REGISTRATION: ("positive", "how_to_reg"),
    CANCELLATION: ("negative", "event_busy"),
    OTHER: ("grey-7", "mail"),
    BLOCKED: ("grey-9", "block"),
}
VIEW_ICONS = {VIEW_OPEN: "mark_email_unread", VIEW_DONE: "done_all", VIEW_BLOCKED: "block"}


@ui.page("/inbox")
def page(view: str = VIEW_OPEN) -> None:
    if view not in VIEWS:
        view = VIEW_OPEN
    if layout.guard() is False:
        return
    with layout.frame("/inbox", t("inbox.title")):
        active = events.active_events()
        counts = {v: len(registrations.inbox(None, v)) for v in VIEWS}
        tabs_by_view = {}
        with ui.row().classes("w-full items-center"):
            with ui.tabs(value=view, on_change=lambda e: ui.navigate.to(f"/inbox?view={e.value}")).props(
                    "dense no-caps inline-label align=left"):
                for v in VIEWS:
                    tabs_by_view[v] = ui.tab(v, t(f"inbox.view.{v}", count=counts[v]), icon=VIEW_ICONS[v])
            ui.space()
            btn = ui.button(t("common.fetch_mails"), icon="sync").props("flat")
            btn.on_click(lambda: fetch_mails(btn))

        mails = registrations.inbox(None, view)
        if not mails:
            if view == VIEW_OPEN:
                layout.empty_state("task_alt", t("inbox.all_done"), t("inbox.all_done_text"))
            else:
                layout.empty_state("inbox", t("inbox.empty"))
            return
        if view == VIEW_OPEN:
            ui.label(t("inbox.hint")).classes("muted")
        remaining = {"n": len(mails)}

        def done():
            remaining["n"] -= 1
            open_tab = tabs_by_view[VIEW_OPEN]
            open_tab._props["label"] = t(f"inbox.view.{VIEW_OPEN}", count=remaining["n"])
            open_tab.update()
            if remaining["n"] == 0:
                ui.navigate.reload()

        for m in mails:
            with ui.column().classes("w-full") as holder:
                mail_card(m, view, done, active, holder)


def mail_card(m: dict, view: str, on_done=None, active: list[dict] | None = None, holder=None,
              chosen_events: list[int] | None = None) -> None:
    """One mail. With several active events (`active`), it shows and lets you change the event."""
    st = {"first_name": m["p_first_name"], "last_name": m["p_last_name"], "companions": m["companions"],
          "events": chosen_events or [m["event_id"]]}
    several = active is not None and len(active) > 1
    with ui.card().classes("w-full card-soft p-5 gap-3").props("flat") as card:
        with ui.row().classes("w-full items-center no-wrap gap-3"):
            initial = (m["p_first_name"] or m["email"])[:1].upper()
            ui.avatar(initial, color="primary", text_color="white").props("size=40px")
            with ui.column().classes("gap-0 grow min-w-0"):
                with ui.row().classes("items-center gap-2"):
                    name = " ".join(p for p in (m["p_first_name"], m["p_last_name"]) if p)
                    ui.label(name or m["sender_name"] or m["email"]).classes("font-semibold")
                    layout.familiarity_badge(m["familiarity"])
                ui.label(m["email"]).classes("muted text-sm ellipsis")
            ui.label(format_date(m["date"], "day_time")).classes("muted text-sm")

        with ui.row().classes("w-full items-center gap-2"):
            ui.label(m["subject"] or t("common.no_subject")).classes("font-medium grow")
            layout.account_chip(m["account_id"], "inbox.to_account")
            if several and view != VIEW_OPEN:
                ui.chip(t("header.event", title=m["event_title"], date=format_date(m["event_date"])),
                        icon="event").props("dense outline color=grey-7").classes("text-xs")
        text = m["body_clean"] or m["body"]
        ui.label(text[:1200] + (" …" if len(text) > 1200 else "")).classes("mail-text")
        if m["body"] and m["body"].strip() != (m["body_clean"] or "").strip():
            with ui.expansion(t("inbox.full_mail")).classes("w-full muted text-sm").props("dense"):
                ui.label(m["body"]).classes("mail-text muted")
        if m["notes"]:
            with ui.row().classes("items-center gap-2"):
                ui.icon("sticky_note_2", color="amber-8")
                ui.label(m["notes"]).classes("text-sm")

        value = m["classification"] or m["suggestion"]
        color, icon = CLASSIFICATION_STYLE[value]
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(t("inbox.classified_as" if m["classification"] else "inbox.suggestion")).classes(
                "muted text-sm")
            show_score = not m["classification"] and m["suggestion"] != OTHER
            label = t(f"classification.{value}") + (f" · {m['score']} %" if show_score else "")
            ui.chip(label, icon=icon, color=color, text_color="white").props("dense")
            ui.label(reasons_text(m["reasons"])).classes("muted text-xs")

        if view != VIEW_OPEN:
            with ui.row().classes("w-full justify-end"):
                def reset(mid=m["id"]):
                    registrations.reset(mid)
                    ui.navigate.reload()
                ui.button(t("inbox.back_to_review"), icon="undo", on_click=reset).props("flat")
            return

        if several:
            event_picker(m, st, active, holder, view, on_done)

        with ui.row().classes("w-full gap-2 items-center"):
            ui.input(t("common.first_name")).bind_value(st, "first_name").props("outlined dense").classes("w-40")
            ui.input(t("common.last_name")).bind_value(st, "last_name").props("outlined dense").classes("w-40")
            ui.number(t("common.companions"), min=0, max=20, format="%d").bind_value(st, "companions").props(
                "outlined dense").classes("w-36")

        def decide(classification: str):
            registrations.confirm(m["id"], classification, st["first_name"] or "", st["last_name"] or "",
                                  int(st["companions"] or 0), st["events"])
            # Notify and count before deleting: afterwards this handler belongs to no element anymore.
            ui.notify(t(f"inbox.decided.{classification}"))
            if on_done:
                on_done()
            card.delete()

        def confirm_block():
            with ui.dialog() as dialog, ui.card().classes("p-6"):
                ui.label(t("inbox.block_title", email=m["email"])).classes("text-lg font-semibold")
                ui.label(t("inbox.block_text")).classes("muted")
                with ui.row().classes("w-full justify-end mt-3"):
                    ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("common.block"), color="negative",
                              on_click=lambda: (dialog.close(), decide(BLOCKED)))
            dialog.open()

        def hide_sender():
            people.hide([m["person_id"]])
            ui.notify(t("inbox.hidden", email=m["email"]))
            if on_done:
                on_done()
            card.delete()

        with ui.row().classes("w-full gap-2 justify-end"):
            ui.button(t("common.block"), icon="block", on_click=confirm_block).props("flat color=grey-8")
            ui.button(t("inbox.hide_sender"), icon="visibility_off", on_click=hide_sender).props(
                "flat color=grey-8").tooltip(t("inbox.hide_sender_tooltip"))
            ui.space()
            for value in (OTHER, CANCELLATION, REGISTRATION):
                b = ui.button(t(f"classification.{value}"), icon=CLASSIFICATION_STYLE[value][1],
                              on_click=lambda v=value: decide(v))
                b.props("unelevated" if value == m["suggestion"] else "outline")


def event_picker(m: dict, st: dict, active: list[dict], holder, view: str, on_done) -> None:
    """Which event the mail is about. Picking another event first classifies the mail anew; picking
    several registers the person for all of them ("I'll come to both")."""
    options = {e["id"]: t("header.event", title=e["title"], date=format_date(e["date"])) for e in active}
    guessed = {"key": "reason.event_guessed"} in json.loads(m["reasons"] or "[]")

    def changed(e):
        chosen = [i for i in (e.value or []) if i in options]
        if not chosen:  # at least one event
            st["events"] = [m["event_id"]]
            select.value = st["events"]
            return
        st["events"] = chosen
        if chosen[0] != m["event_id"] and holder is not None:
            registrations.move(m["id"], chosen[0])
            fresh = next(x for x in registrations.inbox(chosen[0], view) if x["id"] == m["id"])
            # Keep what was typed into the card.
            fresh.update(p_first_name=st["first_name"], p_last_name=st["last_name"])
            holder.clear()
            with holder:
                mail_card(fresh, view, on_done, active, holder, chosen)

    with ui.row().classes("w-full items-center gap-2"):
        select = ui.select(options, value=st["events"], multiple=True, label=t("inbox.event"),
                           on_change=changed).props("outlined dense use-chips").classes("min-w-[320px] grow")
        if guessed:
            ui.chip(t("inbox.event_check"), icon="help_outline", color="warning",
                    text_color="black").props("dense").tooltip(t("inbox.event_check_tooltip"))
