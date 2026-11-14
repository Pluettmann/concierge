"""Simple folder picker dialog (a browser cannot pick local folders)."""
from __future__ import annotations

import getpass
import os
import string
import sys
from pathlib import Path

from nicegui import ui

from .. import config
from ..i18n import t


def roots() -> list[tuple[str, Path, bool]]:
    """(label, path, is_drive) of the places to start from."""
    found: list[tuple[str, Path, bool]] = [(t("folder_picker.home"), Path.home(), False)]
    if sys.platform.startswith("win"):
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                found.append((t("folder_picker.drive", letter=letter), drive, True))
    else:
        user = getpass.getuser()
        for base in (Path("/media") / user, Path("/run/media") / user, Path("/mnt")):
            if base.is_dir():
                for child in sorted(base.iterdir()):
                    if child.is_dir():
                        found.append((child.name, child, True))
        found.append((t("folder_picker.filesystem"), Path("/"), False))
    return found


def suggestions() -> list[Path]:
    """Likely shared partitions (everything except the system partition)."""
    system_drive = os.environ.get("SYSTEMDRIVE", "C:").upper()
    result = []
    for _, path, is_drive in roots():
        if is_drive and not str(path).upper().startswith(system_drive):
            result.append(path / config.APP_NAME)
    return result


async def choose_folder(start: str | None = None) -> str | None:
    current = {"path": Path(start) if start and Path(start).exists() else Path.home()}

    with ui.dialog() as dialog, ui.card().classes("w-[560px] max-w-full p-0"):
        with ui.column().classes("w-full p-5 gap-3"):
            ui.label(t("folder_picker.title")).classes("text-lg font-semibold")
            with ui.row().classes("gap-2 flex-wrap"):
                for label, path, _ in roots():
                    ui.chip(label, icon="folder_special", on_click=lambda p=path: go(p)).props("clickable outline")
            path_label = ui.label().classes("muted break-all")
            listing = ui.column().classes("w-full gap-0")
            with ui.row().classes("w-full items-center gap-2"):
                new_name = ui.input(placeholder=t("folder_picker.new_folder")).classes("grow").props(
                    "dense outlined")
                ui.button(t("folder_picker.create"), icon="create_new_folder", on_click=lambda: create()).props("flat")
        with ui.row().classes("w-full justify-end gap-2 p-4").style("border-top:1px solid rgba(0,0,0,.08)"):
            ui.button(t("common.cancel"), on_click=lambda: dialog.submit(None)).props("flat")
            ui.button(t("folder_picker.use"), on_click=lambda: dialog.submit(str(current["path"])))

    def go(path: Path) -> None:
        current["path"] = path
        path_label.text = str(path)
        listing.clear()
        with listing, ui.scroll_area().classes("w-full h-72 border rounded-xl"):
            if path.parent != path:
                ui.item("..", on_click=lambda: go(path.parent)).props("clickable dense")
            try:
                children = sorted((c for c in path.iterdir() if c.is_dir() and not c.name.startswith(".")),
                                  key=lambda c: c.name.lower())
            except OSError:
                children = []
                ui.label(t("folder_picker.no_access")).classes("muted p-3")
            for child in children:
                with ui.item(on_click=lambda c=child: go(c)).props("clickable dense"):
                    with ui.item_section().props("avatar"):
                        ui.icon("folder", color="amber-7")
                    ui.item_section(child.name)

    def create() -> None:
        name = (new_name.value or "").strip()
        if not name:
            return
        try:
            target = current["path"] / name
            target.mkdir(exist_ok=True)
            new_name.value = ""
            go(target)
        except OSError as exc:
            ui.notify(t("folder_picker.create_failed", error=exc), type="negative")

    go(current["path"])
    return await dialog
