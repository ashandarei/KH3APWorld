"""Shared tab action and layout helpers; gameplay policy remains in services."""

import asyncio
import logging
from tkinter import ttk

from .widgets import Tooltip


class View:
    def __init__(self, app, frame):
        self.app, self.ctx, self.frame = app, app.ctx, frame
        self.busy = False

    def button(self, parent, text, callback, *, primary=False, icon=None, tooltip="", pack=True, card=False):
        def invoke():
            if self.app.action_running or self.ctx.kh3_run_action_running:
                return
            try:
                callback()
            except Exception:
                logging.getLogger("Client").exception("Native UI action failed")
        style = "Icon.TButton" if icon else "Primary.TButton" if primary else "TButton"
        button = ttk.Button(parent, text=text, command=invoke, style=("Card." if card else "") + style)
        if icon:
            button.configure(text="", image=self.app.theme.icon(icon))
        if tooltip:
            button.tooltip = Tooltip(button, tooltip)
        if pack:
            button.pack(side="left", padx=(0, 6), pady=4)
        return button

    def row(self, parent=None):
        row = ttk.Frame(parent or self.frame)
        row.pack(fill="x")
        return row

    async def work(self, operation):
        if self.app.action_running or self.ctx.kh3_run_action_running:
            return None
        self.busy = True
        self.app.action_running = True
        try:
            return await asyncio.to_thread(operation)
        finally:
            self.busy = False
            self.app.action_running = False
            self.app.next_refresh = 0

