"""Registration list with filters and actions (confirmation, rejection, personal reply, block)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from collections import Counter

from nicegui import run, ui

from .. import db, templates
from ..i18n import format_date, t
from ..services import accounts, events, people, registrations, sending
from ..services.registrations import CANCELLED, OPEN, REGISTERED, REPLY_STATUSES
from . import layout

log = logging.getLogger(__name__)


def columns() -> list[dict]:
    return [
        {"name": "name", "label": t("list.col.name"), "field": "display_name", "sortable": True, "align": "left"},
        {"name": "email", "label": t("common.email"), "field": "email", "sortable": True, "align": "left"},
        {"name": "familiarity", "label": t("list.col.familiarity"), "field": "familiarity", "sortable": True,
         "align": "left"},
        {"name": "companions", "label": t("list.col.companions"), "field": "companions", "sortable": True,
         "align": "center"},
        {"name": "status", "label": t("list.col.status"), "field": "status", "sortable": True, "align": "left"},
        {"name": "reply", "label": t("list.col.reply"), "field": "reply_status", "sortable": True, "align": "left"},
        {"name": "registered", "label": t("list.col.registered"), "field": "registered_short", "sortable": True,
         "align": "left", ":sort": "(a, b, rowA, rowB) => rowA.registered_at.localeCompare(rowB.registered_at)"},
    ]


def familiarity_slot() -> str:
    labels = {k: t(f"familiarity.{k}") for k in layout.FAMILIARITY_COLORS}
    return f"""
<q-td :props="props">
  <q-badge rounded class="q-px-sm q-py-xs"
    :color="{json.dumps(layout.FAMILIARITY_COLORS).replace('"', "'")}[props.value]"
    :label="{json.dumps(labels, ensure_ascii=False).replace('"', "'")}[props.value]" />
</q-td>"""


def _status_slot() -> str:
    return f"""
<q-td :props="props">
  <q-badge v-if="props.row.blocked" rounded color="grey-9" label="{t('status.blocked')}" class="q-px-sm q-py-xs"/>
  <q-badge v-else rounded outline class="q-px-sm q-py-xs"
    :color="props.value === '{REGISTERED}' ? 'positive' : 'negative'"
    :label="props.value === '{REGISTERED}' ? '{t('status.registered')}' : '{t('status.cancelled')}'" />
</q-td>"""


def _reply_slot() -> str:
    labels = {k: t(f"reply_status_short.{k}") for k in REPLY_STATUSES}
    return f"""
<q-td :props="props">
  <q-badge rounded class="q-px-sm q-py-xs"
    :color="{json.dumps(layout.REPLY_STATUS_COLORS).replace('"', "'")}[props.value]"
    :label="{json.dumps(labels, ensure_ascii=False).replace('"', "'")}[props.value]" />
</q-td>"""


def _rows(event_id: int) -> list[dict]:
    rows = registrations.registrations(event_id)
    for r in rows:
        r["display_name"] = " ".join(p for p in (r["first_name"], r["last_name"]) if p) or "–"
        r["registered_short"] = format_date(r["registered_at"], "day_time")
    return rows


@ui.page("/list")
def page(event: int | None = None) -> None:
    active = layout.guard()
    if active is False:
        return
    ev = events.get(event) if event else active
    readonly = ev["status"] != events.ACTIVE
    title = t("list.title_archive", title=ev["title"]) if readonly else t("list.title")
    with layout.frame("/list", title):
        f = {"search": "", "status": REGISTERED, "familiarity": [], "reply": "all"}
        all_rows = {"rows": _rows(ev["id"])}

        with ui.card().classes("w-full card-soft p-4 gap-3").props("flat"):
            with ui.row().classes("w-full items-center gap-3"):
                # The filters trigger apply(); they are connected once apply() is defined.
                filters = [
                    ui.input(placeholder=t("common.search")).bind_value(f, "search").props(
                        "outlined dense clearable prepend-icon=search").classes("w-60"),
                    ui.toggle({REGISTERED: t("status.registered"), CANCELLED: t("status.cancelled"),
                               "all": t("common.all")}).bind_value(f, "status").props("no-caps unelevated dense"),
                    ui.select({k: t(f"familiarity.{k}") for k in layout.FAMILIARITY_COLORS}, multiple=True,
                              label=t("list.col.familiarity")).bind_value(f, "familiarity").props(
                        "outlined dense use-chips clearable").classes("w-64"),
                    ui.select({"all": t("list.all_replies"), **{k: t(f"reply_status.{k}") for k in REPLY_STATUSES}},
                              label=t("list.col.reply")).bind_value(f, "reply").props("outlined dense").classes(
                        "w-52"),
                ]

        with ui.row().classes("w-full items-center gap-2"):
            summary = ui.label().classes("muted")
            ui.space()
            if not readonly:
                ui.button(t("list.add"), icon="person_add", on_click=lambda: add_dialog()).props("flat")
            ui.button(t("list.csv"), icon="download", on_click=lambda: export()).props("flat")

        actions = ui.row().classes("w-full items-center gap-2 no-wrap").style("min-height: 40px")

        table = ui.table(rows=[], columns=columns(), row_key="id", selection=None if readonly else "multiple",
                         pagination={"rowsPerPage": 50, "sortBy": "registered"},
                         on_select=lambda: show_actions()).classes("w-full card-soft").props("flat")
        table.add_slot("body-cell-familiarity", familiarity_slot())
        table.add_slot("body-cell-status", _status_slot())
        table.add_slot("body-cell-reply", _reply_slot())
        table.on("rowClick", lambda e: detail_dialog(e.args[1]))
        with table.add_slot("no-data"):
            ui.label(t("list.no_rows")).classes("muted p-4")

        def apply():
            q = (f["search"] or "").lower()
            rows = [r for r in all_rows["rows"]
                    if (f["status"] == "all" or r["status"] == f["status"])
                    and (not f["familiarity"] or r["familiarity"] in f["familiarity"])
                    and (f["reply"] == "all" or r["reply_status"] == f["reply"])
                    and (not q or q in r["email"].lower() or q in r["display_name"].lower())]
            table.rows = rows
            table.selected = [s for s in table.selected if s in rows]
            table.update()
            registered = [r for r in all_rows["rows"] if r["status"] == REGISTERED and not r["blocked"]]
            summary.text = t("list.summary", shown=len(rows), registrations=len(registered),
                             people=sum(1 + r["companions"] for r in registered))
            show_actions()

        def reload_rows():
            all_rows["rows"] = _rows(ev["id"])
            table.selected = []
            apply()

        def show_actions():
            actions.clear()
            sel = table.selected
            if readonly:
                return
            with actions:
                if not sel:
                    ui.label(t("list.tip")).classes("muted text-sm")
                    open_rows = [r for r in table.rows if r["reply_status"] == OPEN and not r["blocked"]
                                 and r["status"] == REGISTERED]
                    if open_rows:
                        ui.button(t("list.select_open", count=len(open_rows)), icon="select_all",
                                  on_click=lambda: (setattr(table, "selected", open_rows), table.update(),
                                                    show_actions())).props("flat dense")
                    return
                ui.label(t("list.selected", count=len(sel))).classes("font-medium")
                ui.button(t("kind.confirmation"), icon="event_available",
                          on_click=lambda: send_dialog(sending.CONFIRMATION, list(sel))).props(
                    "unelevated dense").classes("px-3")
                ui.button(t("kind.rejection"), icon="event_busy",
                          on_click=lambda: send_dialog(sending.REJECTION, list(sel))).props(
                    "outline dense").classes("px-3")
                b = ui.button(t("kind.personal"), icon="edit",
                              on_click=lambda: personal_dialog(sel[0])).props("outline dense").classes("px-3")
                b.tooltip(t("list.personal_tooltip") if len(sel) == 1 else t("list.personal_only_one"))
                if len(sel) != 1:
                    b.disable()
                ui.button(icon="block", on_click=lambda: block_dialog(list(sel))).props(
                    "flat round dense color=grey-7").tooltip(t("common.block"))
                ui.button(icon="close", on_click=lambda: (setattr(table, "selected", []), table.update(),
                                                         show_actions())).props("flat round dense").tooltip(
                    t("list.clear_selection"))

        # --- Sending ------------------------------------------------------------------------------
        async def send_dialog(kind: str, rows: list[dict]):
            done_before = [r for r in rows if r["reply_status"] != OPEN]
            blocked = [r for r in rows if r["blocked"]]
            targets = {"again": False}
            routes = sending.route(ev, [r["person_id"] for r in rows])
            first = rows[0]
            settings = accounts.with_globals(routes[first["person_id"]])
            try:
                subject, html, _ = templates.render(kind, ev, first, first["companions"], settings)
            except templates.RenderError as exc:
                ui.notify(str(exc), type="negative")
                return

            def recipients():
                return [r for r in rows if not r["blocked"] and (targets["again"] or r["reply_status"] == OPEN)]

            with ui.dialog().props("maximized=false") as dialog, ui.card().classes("w-[760px] max-w-full p-0"):
                with ui.column().classes("w-full p-6 gap-3"):
                    ui.label(t(f"send.title_{kind}")).classes("text-xl font-bold")
                    ui.label(t(f"send.text_{kind}")).classes("muted")
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.label(t("send.subject")).classes("muted")
                        ui.label(subject).classes("font-medium")
                    ui.label(t("send.preview_for", name=first["display_name"])).classes("muted text-sm")
                    layout.preview_frame(html, 380)
                    if done_before:
                        ui.checkbox(t("send.again", count=len(done_before)),
                                    on_change=lambda: count.set_text(count_text())).bind_value(targets, "again")
                    if blocked:
                        ui.label(t("send.blocked_skipped", count=len(blocked))).classes("muted text-sm")
                    count = ui.label().classes("font-medium")
                    progress = ui.linear_progress(value=0, show_value=False).props("rounded size=8px")
                    progress.set_visibility(False)
                    status = ui.label().classes("muted text-sm")
                with ui.row().classes("w-full justify-end gap-2 p-4").style("border-top:1px solid rgba(0,0,0,.08)"):
                    cancel = ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    go = ui.button(t("send.now"), icon="send")

            def count_text():
                todo = recipients()
                if not todo:
                    return t("send.no_recipients")
                text = t("send.recipients", count=len(todo))
                if len(accounts.all_accounts()) > 1:
                    per_account = Counter(routes[r["person_id"]]["email"] for r in todo)
                    text += " " + t("send.from_accounts",
                                    accounts=", ".join(f"{e} ({n})" for e, n in per_account.items()))
                return text
            count.set_text(count_text())

            async def do_send():
                todo = recipients()
                if not todo:
                    return
                go.disable()
                cancel.disable()
                progress.set_visibility(True)
                status.set_text(t("send.connecting"))
                ok, sender = await layout.background(sending.Sender, ev, [r["person_id"] for r in todo])
                if not ok:
                    go.enable()
                    cancel.enable()
                    return
                errors = []
                pause = float(db.get_setting("send_pause") or 3)
                try:
                    for i, r in enumerate(todo, 1):
                        status.set_text(t("send.progress", email=r["email"], current=i, total=len(todo)))
                        try:
                            err = await run.io_bound(sender.send, kind, r["person_id"])
                        except Exception as exc:  # one error must not stop the other recipients
                            log.exception("Sending to %s aborted", r["email"])
                            err = f"{r['email']}: {layout.explain(exc)}"
                        if err:
                            errors.append(err)
                        progress.set_value(i / len(todo))
                        if i < len(todo):
                            await asyncio.sleep(pause)
                finally:
                    await run.io_bound(sender.close)
                dialog.close()
                sent = len(todo) - len(errors)
                if sent:
                    ui.notify(t(f"send.done_{kind}", count=sent), type="positive")
                if errors:
                    layout.show_errors(t("send.errors", failed=len(errors), total=len(todo)), errors,
                                       t("send.errors_hint"))
                reload_rows()

            go.on_click(do_send)
            dialog.open()

        async def personal_dialog(row: dict):
            mails = registrations.mails_of(row["person_id"], ev["id"])
            last = mails[0] if mails else None
            st = {"subject": ("Re: " + last["subject"]) if last else ev["title"],
                  "html": t("personal.greeting_html", first_name=row["first_name"]) if row["first_name"] else ""}
            with ui.dialog() as dialog, ui.card().classes("w-[1000px] max-w-full p-0"):
                with ui.row().classes("w-full p-6 gap-6 no-wrap items-stretch"):
                    with ui.column().classes("w-2/5 gap-2"):
                        ui.label(t("personal.mails_from", name=row["display_name"])).classes("font-semibold")
                        ui.label(row["email"]).classes("muted text-sm")
                        if row["notes"]:
                            ui.label(row["notes"]).classes("text-sm hint-warn p-2 rounded")
                        with ui.scroll_area().classes("h-96 w-full"):
                            for m in mails:
                                ui.label(format_date(m["date"], "datetime") + " · " + (m["subject"] or "")).classes(
                                    "muted text-xs mt-2")
                                ui.label(m["body_clean"] or m["body"]).classes("mail-text text-sm")
                            if not mails:
                                ui.label(t("personal.no_mails")).classes("muted")
                    with ui.column().classes("grow gap-3"):
                        ui.label(t("personal.title")).classes("text-xl font-bold")
                        if len(accounts.all_accounts()) > 1:
                            sender_account = sending.account_for(row["person_id"], ev["id"])
                            ui.label(t("personal.from", sender=accounts.label(sender_account))).classes(
                                "muted text-sm")
                        ui.input(t("common.subject")).bind_value(st, "subject").classes("w-full").props(
                            "outlined dense")
                        ui.editor().bind_value(st, "html").classes("w-full").style("min-height: 260px")
                        ui.label(t("personal.hint")).classes("muted text-sm")
                with ui.row().classes("w-full justify-end gap-2 p-4").style("border-top:1px solid rgba(0,0,0,.08)"):
                    ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    go = ui.button(t("common.send"), icon="send")

            async def do_send():
                if not (st["html"] or "").strip():
                    ui.notify(t("personal.empty"), type="warning")
                    return
                go.props("loading")

                def job():
                    sender = sending.Sender(ev, [row["person_id"]])
                    try:
                        return sender.send(sending.PERSONAL, row["person_id"], st["subject"], st["html"])
                    finally:
                        sender.close()
                ok, err = await layout.background(job)
                go.props(remove="loading")
                if ok and not err:
                    dialog.close()
                    ui.notify(t("personal.sent"), type="positive")
                    reload_rows()
                elif err:
                    layout.show_errors(t("personal.failed"), [err])

            go.on_click(do_send)
            dialog.open()

        def block_dialog(rows: list[dict]):
            with ui.dialog() as dialog, ui.card().classes("p-6"):
                ui.label(t("list.block_title", count=len(rows))).classes("text-lg font-semibold")
                ui.label(t("list.block_text")).classes("muted")

                def do():
                    for r in rows:
                        people.block(r["person_id"], True)
                    dialog.close()
                    reload_rows()
                with ui.row().classes("w-full justify-end mt-3"):
                    ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("common.block"), color="negative", on_click=do)
            dialog.open()

        # --- Details and manual entry ---------------------------------------------------------------
        def detail_dialog(row: dict):
            p = people.get(row["person_id"])
            st = {"first_name": p["first_name"], "last_name": p["last_name"], "notes": p["notes"],
                  "companions": row["companions"], "status": row["status"], "blocked": bool(p["blocked"])}
            mails = registrations.mails_of(row["person_id"], ev["id"])
            with ui.dialog() as dialog, ui.card().classes("w-[640px] max-w-full p-6 gap-3"):
                with ui.row().classes("w-full items-center"):
                    ui.label(row["display_name"]).classes("text-xl font-bold grow")
                    layout.familiarity_badge(row["familiarity"])
                ui.label(row["email"]).classes("muted")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ui.input(t("common.first_name")).bind_value(st, "first_name").props("outlined dense").classes(
                        "grow")
                    ui.input(t("common.last_name")).bind_value(st, "last_name").props("outlined dense").classes("grow")
                with ui.row().classes("w-full gap-2 items-center"):
                    ui.number(t("common.companions"), min=0, format="%d").bind_value(st, "companions").props(
                        "outlined dense").classes("w-40")
                    ui.toggle({REGISTERED: t("status.registered"), CANCELLED: t("status.cancelled")}).bind_value(
                        st, "status").props("no-caps unelevated dense")
                ui.textarea(t("list.notes")).bind_value(st, "notes").classes("w-full").props("outlined autogrow")
                ui.switch(t("status.blocked")).bind_value(st, "blocked")
                if mails:
                    with ui.expansion(t("list.mails", count=len(mails)), icon="mail").classes("w-full"):
                        for m in mails:
                            ui.label(format_date(m["date"], "datetime") + " · " + (m["subject"] or "")).classes(
                                "muted text-xs mt-2")
                            ui.label(m["body_clean"] or m["body"]).classes("mail-text text-sm")

                def save():
                    people.update(p["id"], first_name=st["first_name"] or "", last_name=st["last_name"] or "",
                                  notes=st["notes"] or "")
                    if st["blocked"] != bool(p["blocked"]):
                        people.block(p["id"], st["blocked"])
                    registrations.update(row["id"], companions=int(st["companions"] or 0), status=st["status"])
                    dialog.close()
                    reload_rows()

                def remove():
                    registrations.delete(row["id"])
                    dialog.close()
                    reload_rows()
                with ui.row().classes("w-full justify-between mt-2"):
                    ui.button(t("list.remove"), icon="delete", on_click=remove).props("flat color=negative")
                    with ui.row().classes("gap-2"):
                        ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                        if not readonly:
                            ui.button(t("common.save"), on_click=save)
            dialog.open()

        def add_dialog():
            st = {"email": "", "first_name": "", "last_name": "", "companions": 0}
            with ui.dialog() as dialog, ui.card().classes("w-[480px] max-w-full p-6 gap-3"):
                ui.label(t("list.add_title")).classes("text-xl font-bold")
                ui.label(t("list.add_text")).classes("muted")
                ui.input(t("common.email")).bind_value(st, "email").classes("w-full").props("outlined dense")
                with ui.row().classes("w-full gap-2 no-wrap"):
                    ui.input(t("common.first_name")).bind_value(st, "first_name").props("outlined dense").classes(
                        "grow")
                    ui.input(t("common.last_name")).bind_value(st, "last_name").props("outlined dense").classes("grow")
                ui.number(t("common.companions"), min=0, format="%d").bind_value(st, "companions").props(
                    "outlined dense")

                def save():
                    if "@" not in (st["email"] or ""):
                        ui.notify(t("common.email_missing"), type="warning")
                        return
                    registrations.add_manual(ev["id"], st["email"], st["first_name"] or "", st["last_name"] or "",
                                             int(st["companions"] or 0))
                    dialog.close()
                    reload_rows()
                with ui.row().classes("w-full justify-end"):
                    ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("list.add"), on_click=save)
            dialog.open()

        def export():
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow([t(f"csv.{c}") for c in ("first_name", "last_name", "email", "familiarity", "companions",
                                                 "status", "reply", "registered_at", "notes")])
            for r in table.rows:
                w.writerow([r["first_name"], r["last_name"], r["email"], t(f"familiarity.{r['familiarity']}"),
                            r["companions"], t(f"status.{r['status']}"), t(f"reply_status.{r['reply_status']}"),
                            r["registered_short"], r["notes"]])
            ui.download.content(("﻿" + buf.getvalue()).encode("utf-8"),
                                t("csv.filename", date=ev["date"]), "text/csv")

        for element in filters:
            element.on_value_change(apply)
        apply()
