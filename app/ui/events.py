"""Create an event (wizard), edit and archive events."""
from __future__ import annotations

import urllib.parse
from datetime import date, timedelta

from nicegui import ui

from ..i18n import format_date, t
from ..services import accounts, events, fetching, people
from . import layout
from .wizard import Step, Wizard


def maps_link(address: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(address) if address else ""


@ui.page("/events/new")
def new_event() -> None:
    if not layout.is_configured():
        ui.navigate.to("/setup")
        return
    layout.setup_theme()
    layout.page_title(t("new_event.page_title"))
    previous = events.active()
    st = {"title": "", "date": "", "start_time": "19:00", "end_time": "", "venue": "", "address": "",
          "map_link": "", "info": "", "newsletter": None, "sent": [], "search_from": date.today().isoformat(),
          "take_over_members": True}
    if previous:  # reusing place and time saves typing
        st.update({k: previous[k] for k in ("start_time", "end_time", "venue", "address", "map_link")})

    def r_title():
        if previous:
            with ui.card().classes("w-full hint-warn").props("flat"):
                ui.label(t("new_event.archives_previous", title=previous["title"])).classes("text-sm")
        ui.input(t("event.title"), placeholder=t("new_event.title_placeholder")).bind_value(st, "title").classes(
            "w-full").props("outlined autofocus")

    def n_title():
        if not st["title"].strip():
            ui.notify(t("new_event.title_missing"), type="warning")
            return False
        return True

    def r_date():
        with ui.row().classes("w-full justify-center"):
            ui.date(mask="YYYY-MM-DD").bind_value(st, "date").props("minimal")

    def n_date():
        if not st["date"]:
            ui.notify(t("new_event.date_missing"), type="warning")
            return False
        return True

    def r_time():
        with ui.row().classes("w-full gap-4"):
            ui.time_input(t("event.start_time")).bind_value(st, "start_time").classes("grow").props("outlined")
            ui.time_input(t("new_event.end_time_optional")).bind_value(st, "end_time").classes("grow").props(
                "outlined")

    def r_place():
        ui.input(t("event.venue"), placeholder=t("new_event.venue_placeholder")).bind_value(st, "venue").classes(
            "w-full").props("outlined")
        adr = ui.input(t("event.address"), placeholder=t("new_event.address_placeholder")).bind_value(
            st, "address").classes("w-full").props("outlined")
        adr.on("blur", lambda: st.update(map_link=maps_link(st["address"])) if st["address"] else None)
        ui.input(t("new_event.map_link_generated")).bind_value(st, "map_link").classes("w-full").props("outlined")

    def n_place():
        if st["address"] and not st["map_link"]:
            st["map_link"] = maps_link(st["address"])
        return True

    def r_info():
        ui.textarea(t("new_event.info_label"), placeholder=t("new_event.info_placeholder")).bind_value(
            st, "info").classes("w-full").props("outlined autogrow")

    box = {}

    def r_newsletter():
        ui.markdown(t("new_event.newsletter_text"))
        box["col"] = ui.column().classes("w-full gap-2")
        if st["sent"]:
            fill_sent()
        else:
            with box["col"]:
                ui.spinner(size="lg")
            ui.timer(0.1, load_sent, once=True)

    async def load_sent():
        _, msgs = await layout.background(fetching.recent_sent_all, 25)
        st["sent"] = msgs or []
        fill_sent()

    def fill_sent():
        col = box["col"]
        col.clear()
        several = len(accounts.all_accounts()) > 1
        options = {}
        for i, m in enumerate(st["sent"]):
            label = t("new_event.sent_option", subject=m.subject or t("common.no_subject"),
                      date=format_date(m.date, "datetime"), count=len(m.recipients))
            options[i] = label + (" " + t("new_event.sent_from", account=m.account) if several else "")
        options[-1] = t("new_event.no_newsletter")
        if st["newsletter"] is None:
            title = st["title"].casefold()
            st["newsletter"] = next((i for i, m in enumerate(st["sent"])
                                     if title and title in (m.subject or "").casefold()), -1)
        with col:
            ui.radio(options, on_change=lambda: since_row.set_visibility(st["newsletter"] == -1)).bind_value(
                st, "newsletter").classes("w-full")
            with ui.row().classes("items-center gap-3") as since_row:
                ui.date_input(t("new_event.search_from")).bind_value(st, "search_from").props("outlined dense")
            since_row.set_visibility(st["newsletter"] == -1)

    def chosen():
        i = st["newsletter"]
        return st["sent"][i] if i is not None and 0 <= i < len(st["sent"]) else None

    def r_members():
        m = chosen()
        if not m:
            ui.label(t("new_event.members_none"))
            return
        own = accounts.own_addresses()
        ui.label(t("new_event.members_count", count=len([r for r in m.recipients if r not in own])))
        ui.switch(t("new_event.members_take_over")).bind_value(st, "take_over_members")

    def r_summary():
        m = chosen()
        rows = [(t("event.title"), st["title"]),
                (t("event.date"), st["date"] and format_date(date.fromisoformat(st["date"]))),
                (t("event.time"), " – ".join(x for x in (st["start_time"], st["end_time"]) if x)),
                (t("event.venue"), ", ".join(p for p in (st["venue"], st["address"]) if p)),
                (t("new_event.newsletter"), m.subject if m else t("new_event.newsletter_none",
                                                                  date=st["search_from"]))]
        with ui.grid(columns="auto 1fr").classes("gap-x-6 gap-y-2"):
            for k, v in rows:
                ui.label(k).classes("muted")
                ui.label(v or "–")

    def finish():
        m = chosen()
        search_from = (m.date - timedelta(hours=1)).isoformat(timespec="seconds") if m else \
            f"{st['search_from']}T00:00:00"
        data = {k: st[k] for k in ("title", "date", "start_time", "end_time", "venue", "address", "map_link", "info")}
        data.update(title=st["title"].strip(), search_from=search_from,
                    newsletter_message_id=m.message_id if m else "", newsletter_subject=m.subject if m else "")
        events.create(data)
        if m and st["take_over_members"]:
            own = accounts.own_addresses()
            n = people.mark_members([r for r in m.recipients if r not in own], people.SOURCE_NEWSLETTER)
            if n:
                ui.notify(t("new_event.members_added", count=n), type="positive")
        ui.navigate.to("/overview")

    steps = [
        Step("title", t("new_event.step_title"), t("new_event.step_title_sub"), r_title, n_title),
        Step("date", t("new_event.step_date"), "", r_date, n_date),
        Step("time", t("event.time"), "", r_time),
        Step("place", t("new_event.step_place"), "", r_place, n_place),
        Step("info", t("new_event.step_info"), t("new_event.step_info_sub"), r_info),
        Step("newsletter", t("new_event.step_newsletter"), "", r_newsletter),
        Step("members", t("new_event.step_members"), "", r_members, skip=lambda: chosen() is None),
        Step("done", t("new_event.step_done"), "", r_summary, next_label=t("new_event.create")),
    ]
    Wizard(steps, finish)


@ui.page("/events")
def events_page() -> None:
    if layout.guard(need_event=False) is False:
        return
    with layout.frame("/events", t("nav.events")) as active:
        with ui.row().classes("w-full"):
            ui.button(t("events.new"), icon="add", on_click=lambda: ui.navigate.to("/events/new"))
        if active:
            event_form(active)
        all_events = events.all_events()
        if not all_events:
            layout.empty_state("event", t("events.empty"))
        for ev in all_events:
            if active and ev["id"] == active["id"]:
                continue
            with ui.card().classes("w-full card-soft").props("flat"):
                with ui.row().classes("w-full items-center"):
                    with ui.column().classes("gap-0 grow"):
                        ui.label(ev["title"]).classes("text-lg font-semibold")
                        ui.label(t("events.summary", date=format_date(ev["date"]), count=ev["registrations"],
                                   people=ev["people"])).classes("muted")
                    ui.button(t("events.show_list"), icon="list",
                              on_click=lambda ev=ev: ui.navigate.to(f"/list?event={ev['id']}")).props("flat")
                    ui.button(t("events.reactivate"), icon="restart_alt",
                              on_click=lambda ev=ev: (events.reactivate(ev["id"]),
                                                      ui.navigate.to("/overview"))).props("flat")


def event_form(ev: dict) -> None:
    st = dict(ev)
    with ui.card().classes("w-full card-soft p-6 gap-3").props("flat"):
        with ui.row().classes("w-full items-center"):
            ui.label(t("events.current")).classes("text-lg font-semibold grow")
            layout.badge("events.active", "positive")
        with ui.grid(columns=2).classes("w-full gap-3"):
            ui.input(t("event.title")).bind_value(st, "title").props("outlined")
            ui.date_input(t("event.date")).bind_value(st, "date").props("outlined")
            ui.time_input(t("event.start_time")).bind_value(st, "start_time").props("outlined")
            ui.time_input(t("event.end_time")).bind_value(st, "end_time").props("outlined")
            ui.input(t("event.venue")).bind_value(st, "venue").props("outlined")
            ui.input(t("event.address")).bind_value(st, "address").props("outlined")
        ui.input(t("event.map_link")).bind_value(st, "map_link").classes("w-full").props("outlined")
        ui.textarea(t("event.info")).bind_value(st, "info").classes("w-full").props("outlined autogrow")
        ui.label(t("events.change_hint")).classes("muted text-sm")

        def save():
            if st["address"] != ev["address"] and st["map_link"] == ev["map_link"]:
                st["map_link"] = maps_link(st["address"])
            events.update(ev["id"], st)
            ui.notify(t("common.saved"), type="positive")
            ui.navigate.reload()

        def archive():
            with ui.dialog() as dialog, ui.card().classes("p-6"):
                ui.label(t("events.archive_title")).classes("text-lg font-semibold")
                ui.label(t("events.archive_text")).classes("muted")
                with ui.row().classes("w-full justify-end mt-3"):
                    ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("events.archive_confirm"), on_click=lambda: (events.archive(ev["id"]),
                                                                            ui.navigate.to("/events/new")))
            dialog.open()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(t("events.archive_and_new"), icon="inventory_2", on_click=archive).props("flat")
            ui.button(t("common.save"), icon="save", on_click=save)
