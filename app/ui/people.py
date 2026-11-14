"""All people: notes, membership, blocking, hiding."""
from __future__ import annotations

from nicegui import ui

from ..i18n import format_date, t
from ..services import people
from . import layout
from .registration_list import familiarity_slot

FILTERS = ("all", people.MEMBER, people.KNOWN, people.UNKNOWN, "blocked", "hidden")


def columns() -> list[dict]:
    return [
        {"name": "name", "label": t("list.col.name"), "field": "display_name", "sortable": True, "align": "left"},
        {"name": "email", "label": t("common.email"), "field": "email", "sortable": True, "align": "left"},
        {"name": "familiarity", "label": t("list.col.familiarity"), "field": "familiarity", "sortable": True,
         "align": "left"},
        {"name": "registrations", "label": t("people.col.registrations"), "field": "registrations",
         "sortable": True, "align": "center"},
        {"name": "notes", "label": t("people.col.notes"), "field": "notes", "align": "left",
         "classes": "ellipsis", "style": "max-width: 260px"},
    ]


@ui.page("/people")
def page() -> None:
    if layout.guard(need_event=False) is False:
        return
    with layout.frame("/people", t("nav.people")):
        f = {"search": "", "filter": "all"}
        with ui.row().classes("w-full items-center gap-3"):
            # The filters trigger load(); they are connected once load() is defined.
            filters = [
                ui.input(placeholder=t("common.search")).bind_value(f, "search").props(
                    "outlined dense clearable prepend-icon=search").classes("w-64"),
                ui.toggle({k: t(f"people.filter.{k}") for k in FILTERS}).bind_value(f, "filter").props(
                    "no-caps unelevated dense"),
            ]
            ui.space()
            hide_btn = ui.button(icon="visibility_off", on_click=lambda: hide_selected()).props("flat")
            hide_btn.set_visibility(False)
            ui.button(t("people.add"), icon="person_add", on_click=lambda: edit(None)).props("flat")
        table = ui.table(rows=[], columns=columns(), row_key="id", pagination=50, selection="multiple",
                         on_select=lambda: update_hide_btn()).classes("w-full card-soft").props("flat")
        table.add_slot("body-cell-familiarity", familiarity_slot())
        table.on("rowClick", lambda e: edit(e.args[1]))

        def update_hide_btn():
            n = len(table.selected)
            restore = f["filter"] == "hidden"
            hide_btn.text = t("people.show_again_n" if restore else "people.hide_n", count=n)
            hide_btn.props(f"icon={'visibility' if restore else 'visibility_off'}")
            hide_btn.set_visibility(n > 0)

        def hide_selected():
            restore = f["filter"] == "hidden"
            ids = [r["id"] for r in table.selected]
            people.hide(ids, not restore)
            ui.notify(t("people.shown_again" if restore else "people.hidden_done", count=len(ids)))
            load()

        def load():
            rows = people.all_with_status(f["search"] or "", hidden=f["filter"] == "hidden")
            for r in rows:
                r["display_name"] = " ".join(p for p in (r["first_name"], r["last_name"]) if p) or "–"
            if f["filter"] == "blocked":
                rows = [r for r in rows if r["blocked"]]
            elif f["filter"] not in ("all", "hidden"):
                rows = [r for r in rows if r["familiarity"] == f["filter"] and not r["blocked"]]
            table.rows = rows
            table.selected = []
            table.update()
            update_hide_btn()

        def edit(row: dict | None):
            p = people.get(row["id"]) if row else None
            st = {"email": p["email"] if p else "", "first_name": p["first_name"] if p else "",
                  "last_name": p["last_name"] if p else "", "notes": p["notes"] if p else "",
                  "member": bool(p["is_member"]) if p else True, "blocked": bool(p["blocked"]) if p else False,
                  "hidden": bool(p["hidden"]) if p else False}
            with ui.dialog() as dialog, ui.card().classes("w-[560px] max-w-full p-6 gap-3"):
                ui.label(row["display_name"] if row else t("people.add")).classes("text-xl font-bold")
                if p:
                    ui.label(p["email"]).classes("muted")
                    if p["member_source"]:
                        ui.label(t(f"people.member_source.{p['member_source']}")).classes("muted text-sm")
                else:
                    ui.input(t("common.email")).bind_value(st, "email").classes("w-full").props("outlined dense")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ui.input(t("common.first_name")).bind_value(st, "first_name").props("outlined dense").classes(
                        "grow")
                    ui.input(t("common.last_name")).bind_value(st, "last_name").props("outlined dense").classes("grow")
                ui.textarea(t("people.col.notes")).bind_value(st, "notes").classes("w-full").props("outlined autogrow")
                ui.switch(t("people.member")).bind_value(st, "member")
                ui.switch(t("status.blocked")).bind_value(st, "blocked")
                ui.switch(t("people.hidden_switch")).bind_value(st, "hidden")
                if p:
                    hist = people.history(p["id"])
                    if hist:
                        ui.label(t("people.history")).classes("font-semibold mt-2")
                        for h in hist:
                            companions = f" (+{h['companions']})" if h["companions"] else ""
                            ui.label(f"{format_date(h['date'])} · {h['title']} · {t('status.' + h['status'])}"
                                     f"{companions}").classes("text-sm")

                def save():
                    target = p
                    if not target:
                        if "@" not in (st["email"] or ""):
                            ui.notify(t("common.email_missing"), type="warning")
                            return
                        target = people.upsert(st["email"])
                    people.update(target["id"], first_name=st["first_name"] or "", last_name=st["last_name"] or "",
                                  notes=st["notes"] or "")
                    if bool(target["is_member"]) != st["member"]:
                        people.set_member(target["id"], st["member"])
                    if bool(target["blocked"]) != st["blocked"]:
                        people.block(target["id"], st["blocked"])
                    if bool(target.get("hidden")) != st["hidden"]:
                        people.hide([target["id"]], st["hidden"])
                    dialog.close()
                    load()
                with ui.row().classes("w-full justify-end mt-2"):
                    ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("common.save"), on_click=save)
            dialog.open()

        for element in filters:
            element.on_value_change(load)
        load()
