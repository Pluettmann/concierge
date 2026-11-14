"""Step-by-step wizard: one setting per screen."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from nicegui import ui

from .. import config
from ..i18n import t


@dataclass
class Step:
    key: str
    title: str
    subtitle: str
    render: Callable[[], None]
    # Checks/saves the input; returning False stays on the step.
    on_next: Callable[[], bool | Awaitable[bool]] | None = None
    skip: Callable[[], bool] | None = None
    next_label: str = field(default_factory=lambda: t("wizard.next"))


class Wizard:
    def __init__(self, steps: list[Step], on_finish: Callable[[], Awaitable[None] | None], start: int = 0,
                 on_cancel: Callable[[], None] | None = None):
        self.steps = steps
        self.on_finish = on_finish
        self.on_cancel = on_cancel
        self.index = start
        self.history: list[int] = []
        self.container = ui.column().classes("page-narrow gap-5")
        self.render()

    def visible_steps(self) -> list[Step]:
        return [s for s in self.steps if not (s.skip and s.skip())]

    def render(self) -> None:
        step = self.steps[self.index]
        visible = self.visible_steps()
        pos = visible.index(step) + 1 if step in visible else 1
        self.container.clear()
        with self.container:
            with ui.row().classes("w-full items-center gap-3"):
                ui.icon("room_service", color="primary").classes("text-3xl")
                ui.label(config.APP_NAME).classes("text-lg font-semibold")
                ui.space()
                if len(visible) > 1:
                    ui.label(t("wizard.step", current=pos, total=len(visible))).classes("muted text-sm")
            if len(visible) > 1:
                ui.linear_progress(value=pos / len(visible), show_value=False).props("rounded size=6px")
            with ui.card().classes("w-full card-soft p-7 gap-4").props("flat"):
                ui.label(step.title).classes("text-2xl font-bold")
                if step.subtitle:
                    ui.label(step.subtitle).classes("muted")
                with ui.column().classes("w-full gap-3 mt-2"):
                    step.render()
                with ui.row().classes("w-full justify-between mt-4"):
                    if self.history:
                        ui.button(t("wizard.back"), icon="arrow_back", on_click=self.back).props("flat")
                    elif self.on_cancel:
                        ui.button(t("common.cancel"), on_click=self.on_cancel).props("flat")
                    else:
                        ui.element("div")
                    self.next_button = ui.button(step.next_label, on_click=self.next)
                    self.next_button.props("unelevated size=md icon-right=arrow_forward").classes("px-5")

    async def next(self) -> None:
        step = self.steps[self.index]
        if step.on_next:
            self.next_button.props("loading")
            try:
                ok = step.on_next()
                if inspect.isawaitable(ok):
                    ok = await ok
            finally:
                self.next_button.props(remove="loading")
            if ok is False:
                return
        nxt = self.index + 1
        while nxt < len(self.steps) and self.steps[nxt].skip and self.steps[nxt].skip():
            nxt += 1
        if nxt >= len(self.steps):
            result = self.on_finish()
            if inspect.isawaitable(result):
                await result
            return
        self.history.append(self.index)
        self.index = nxt
        self.render()

    def back(self) -> None:
        if self.history:
            self.index = self.history.pop()
            self.render()
