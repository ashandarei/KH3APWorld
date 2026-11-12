# SPDX-License-Identifier: GPL-3.0-only
"""One prepared tooltip surface shared by every YAML creator row."""

import tkinter as tk
from tkinter import font, ttk

from .theme import PANEL, SUBTLE, TEXT
from .widgets import Tooltip, wrapped_line_count


class TooltipSurface:
    def __init__(self, root):
        self.owner = None
        self.sizes = {}
        self.text = None
        self.font = font.nametofont("TkDefaultFont", root=root)
        self.window = window = tk.Toplevel(root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        body = tk.Frame(window, background=SUBTLE, padx=1, pady=1)
        body.pack(fill="both", expand=True)
        self.details = tk.Text(body, wrap="word", background=PANEL, foreground=TEXT, font=self.font,
                               width=1, height=1, padx=12, pady=10, borderwidth=0, highlightthickness=0)
        self.scroll = ttk.Scrollbar(body, command=self.details.yview)
        self.details.configure(yscrollcommand=self.scroll.set, state="disabled")
        self.details.pack(side="left", fill="both", expand=True)
        window.bind("<Enter>", self._enter)
        window.bind("<Leave>", self._leave)

    def prepare(self, text):
        if text not in self.sizes:
            width = min(440, max((self.font.measure(line) for line in text.splitlines()), default=0)) + 26
            content_height = wrapped_line_count(text, self.font, width - 26) * self.font.metrics("linespace") + 22
            height = min(content_height, 440, max(100, self.window.winfo_screenheight() - 80))
            overflow = content_height > height
            if overflow:
                width += self.scroll.winfo_reqwidth()
            self.sizes[text] = width, height, overflow
        return self.sizes[text]

    def show(self, owner, text):
        if self.owner is not None:
            self.owner.cancel_timer()
        self.owner = owner
        width, height, overflow = self.prepare(text)
        if text != self.text:
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", text)
            self.details.configure(state="disabled")
            self.text = text
        self.details.yview_moveto(0)
        if overflow:
            self.scroll.pack(side="right", fill="y", before=self.details)
        else:
            self.scroll.pack_forget()
        anchor_x, anchor_y = owner.widget.winfo_rootx(), owner.widget.winfo_rooty()
        y = anchor_y + owner.widget.winfo_height() + 6
        x = min(anchor_x, self.window.winfo_screenwidth() - width - 12)
        if y + height > self.window.winfo_screenheight():
            y = anchor_y - height - 6
        self.window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.window.deiconify()

    def hide(self, owner):
        if self.owner is owner:
            self.owner = None
            if self.window.winfo_exists():
                self.window.withdraw()

    def invalidate(self):
        self.window.withdraw()
        self.owner = None
        texts = tuple(self.sizes)
        self.sizes.clear()
        for text in texts:
            self.prepare(text)

    def _enter(self, _):
        if self.owner is not None:
            self.owner.cancel_timer()

    def _leave(self, _):
        if self.owner is not None:
            self.owner.defer_hide()


class SharedTooltip(Tooltip):
    def __init__(self, widget, text, surface):
        self.surface = surface
        super().__init__(widget, text)
        surface.prepare(text)

    def show(self):
        self.timer = None
        text = self.text() if callable(self.text) else self.text
        if text and self.widget.winfo_ismapped():
            self.surface.show(self, text)

    def hide(self, *_):
        self.cancel_timer()
        self.surface.hide(self)
