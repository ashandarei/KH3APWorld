"""Selectable, searchable native AP chat with semantic message colors."""

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from .theme import GOLD, MESSAGE_COLORS, PANEL, SUBTLE
from .widgets import Tooltip


@dataclass(frozen=True)
class LogEntry:
    spans: tuple[tuple[str, str], ...]
    item_flags: tuple[int | None, ...] = ()

    @property
    def text(self):
        return "".join(text for text, _ in self.spans)


class LogView:
    def __init__(self, app, parent):
        self.app = app
        self.search = tk.StringVar()
        self.find_panel = None
        self.find_visible = False
        self.matches = []
        self.match_index = 0
        area = self.area = ttk.Frame(parent)
        area.pack(fill="both", expand=True)
        self.text = tk.Text(area, wrap="word", state="disabled", background=PANEL,
                            font=(app.theme.family, 15), padx=0, pady=0, borderwidth=0,
                            highlightthickness=0, spacing1=0, spacing3=0, selectbackground=SUBTLE)
        scroll = ttk.Scrollbar(area, command=self.text.yview)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.message_number = 0
        # Alternating tags mark message boundaries without adding a widget per message.
        for index in range(2):
            self.text.tag_configure(f"message_{index}", background=PANEL,
                                    relief="flat", borderwidth=0, lmargin1=0, lmargin2=0,
                                    rmargin=0, spacing1=0, spacing3=8)
        for name, color in MESSAGE_COLORS.items():
            self.text.tag_configure(name, foreground=color)
        self.text.tag_configure("hover", background="#293444")
        self.text.tag_configure("match", background=GOLD, foreground="#101319")
        self.item_tooltip = Tooltip(self.text, bind_hover=False, at_pointer=True)
        self.hovered_item = None
        self.item_labels = {}
        for flags in range(8):
            tag = f"item_{flags}"
            self.text.tag_configure(tag)
            self.item_labels[tag] = ", ".join(label for bit, label in ((1, "Progression"), (2, "Useful"), (4, "Trap"))
                                                if flags & bit) or "Filler"
        self.text.bind("<Motion>", self.update_hover)
        self.text.bind("<Leave>", self.clear_hover)
        self.text.bind("<Unmap>", self.clear_hover)
        self.text.bind("<Button-3>", self.copy_line)

        def scrolled(first, last):
            scroll.set(first, last)
            self.update_hover()

        self.text.configure(yscrollcommand=scrolled)
        self.search.trace_add("write", lambda *_: self.find(reveal=True))
        app.root.bind("<Control-f>", self.show_find, add="+")
        parent.bind("<Unmap>", lambda _: self.hide_find(restore_focus=False), add="+")

    def clear_hover(self, *_):
        self.text.tag_remove("hover", "1.0", "end")
        self.clear_item_hover()

    def clear_item_hover(self):
        self.hovered_item = None
        self.item_tooltip.hide()

    def copy_line(self, event):
        index = self.text.index(f"@{event.x},{event.y}")
        if any(tag.startswith("message_") for tag in self.text.tag_names(index)):
            # Copy the logical line in full, even when it wraps across the display.
            text = self.text.get(f"{index} linestart", f"{index} lineend")
            if text:
                self.text.clipboard_clear()
                self.text.clipboard_append(text)
        return "break"

    def update_hover(self, event=None):
        if event is None:
            x, y = self.text.winfo_pointerxy()
            if not self.text.winfo_ismapped() or self.text.winfo_containing(x, y) != self.text:
                self.clear_hover()
                return
            x, y = x - self.text.winfo_rootx(), y - self.text.winfo_rooty()
        else:
            x, y = event.x, event.y
        index = self.text.index(f"@{x},{y}")
        message_tag = next((tag for tag in self.text.tag_names(index) if tag.startswith("message_")), None)
        bounds = self.text.tag_prevrange(message_tag, f"{index}+1c") if message_tag else ()
        if tuple(self.text.tag_ranges("hover")) != tuple(bounds):
            self.text.tag_remove("hover", "1.0", "end")
            if bounds:
                self.text.tag_add("hover", *bounds)
        item_tag = next((tag for tag in self.text.tag_names(index) if tag in self.item_labels), None)
        box = self.text.bbox(index) if item_tag else None
        # Tk clamps hit testing to the nearest character, even past the end of text.
        if not box or not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
            self.clear_item_hover()
            return
        item = (item_tag, *self.text.tag_prevrange(item_tag, f"{index}+1c"))
        if item != self.hovered_item:
            self.clear_item_hover()
            self.hovered_item = item
            self.item_tooltip.text = self.item_labels[item_tag]
            self.item_tooltip.schedule()

    def show_find(self, *_):
        if self.app.dialogs or self.app.tabs.tab(self.app.tabs.select(), "text") != "Archipelago":
            return
        if self.find_panel is None:
            self.find_panel = tk.Frame(self.area, background=SUBTLE, padx=1, pady=1)
            body = ttk.Frame(self.find_panel, style="Card.TFrame", padding=6)
            body.pack(fill="both", expand=True)
            ttk.Label(body, text="Find", style="Card.TLabel").pack(side="left", padx=(0, 6))
            self.find_entry = ttk.Entry(body, textvariable=self.search, width=24)
            self.find_entry.pack(side="left")
            self.find_close = ttk.Button(body, text="×", width=2,
                                         command=self.hide_find, style="Card.Icon.TButton")
            self.find_close.pack(side="left", padx=(6, 0))
            self.find_entry.bind("<Escape>", self.hide_find)
            self.find_entry.bind("<Return>", self.find_next)
            self.find_entry.bind("<Shift-Return>", lambda _: self.find_next(backward=True))
        if not self.find_visible:
            self.return_focus = self.app.root.focus_get() or self.text
        self.find_visible = True
        self.find_panel.place(relx=1, x=-24, y=6, anchor="ne")
        self.find_panel.lift()
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")
        return "break"

    def hide_find(self, *_, restore_focus=True):
        if self.find_visible:
            self.find_visible = False
            self.find_panel.place_forget()
            self.search.set("")
            if restore_focus and self.return_focus.winfo_exists():
                self.return_focus.focus_set()
        return "break"

    def find_next(self, *_, backward=False):
        if self.matches:
            self.match_index = (self.match_index + (-1 if backward else 1)) % len(self.matches)
            self.text.see(self.matches[self.match_index])
        return "break"

    def append(self, entry):
        spans = entry.spans if isinstance(entry, LogEntry) else ((str(entry), ""),)
        follow = not self.find_visible and (not self.text.winfo_ismapped() or self.text.yview()[1] >= .999)
        self.text.configure(state="normal")
        message_tag = f"message_{self.message_number % 2}"
        self.message_number += 1
        item_flags = entry.item_flags if isinstance(entry, LogEntry) else ()
        for number, (text, colors) in enumerate(spans):
            tag = next((color for color in colors.split(";") if color in self.text.tag_names()), "")
            tags = (message_tag, tag) if tag else (message_tag,)
            if number < len(item_flags) and item_flags[number] is not None:
                tags += (f"item_{item_flags[number] & 7}",)
            self.text.insert("end", text, tags)
        self.text.insert("end", "\n", (message_tag,))
        self.text.tag_raise("sel")
        line_count = int(self.text.index("end-1c").split(".")[0])
        if line_count > 2000:
            self.text.delete("1.0", f"{line_count - 1999}.0")
        self.text.configure(state="disabled")
        if follow:
            self.text.see("end")
        if self.search.get():
            self.find()

    def find(self, *, reveal=False):
        previous_match = self.match_index
        self.text.tag_remove("match", "1.0", "end")
        self.matches = []
        self.match_index = 0
        term = self.search.get()
        if not term:
            return
        index = "1.0"
        while index := self.text.search(term, index, "end", nocase=True):
            self.matches.append(index)
            end = f"{index}+{len(term)}c"
            self.text.tag_add("match", index, end)
            index = end
        if reveal and self.matches:
            self.text.see(self.matches[0])
        elif self.matches:
            self.match_index = min(previous_match, len(self.matches) - 1)
