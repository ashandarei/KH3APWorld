"""Native hint requests with suggestions that never overwrite typed text."""

import asyncio
import re
import tkinter as tk
from tkinter import font, ttk
from typing import NamedTuple

from NetUtils import HintStatus

from .theme import ACCENT, MESSAGE_COLORS, PANEL, SUBTLE, SURFACE, TEXT
from .widgets import ContextMenu, Tooltip


STATUS_NAMES = {HintStatus.HINT_UNSPECIFIED: "Unspecified", HintStatus.HINT_NO_PRIORITY: "No Priority",
                HintStatus.HINT_PRIORITY: "Priority", HintStatus.HINT_AVOID: "Avoid", HintStatus.HINT_FOUND: "Found"}
STATUS_COLORS = {HintStatus.HINT_UNSPECIFIED: "white", HintStatus.HINT_NO_PRIORITY: "cyan",
                 HintStatus.HINT_PRIORITY: "plum", HintStatus.HINT_AVOID: "salmon", HintStatus.HINT_FOUND: "green"}
EDITABLE_STATUSES = tuple(status for status in STATUS_NAMES if status != HintStatus.HINT_FOUND)


def hint_status(hint):
    return HintStatus.HINT_FOUND if hint["found"] else hint.get("status", HintStatus.HINT_UNSPECIFIED)


class HintRow(NamedTuple):
    values: tuple
    colors: tuple
    hint: dict

    @property
    def key(self):
        return self.hint["receiving_player"], self.hint["finding_player"], self.hint["location"]


class HintTable(ttk.Frame):
    """Wrapped, per-cell colors using portable Tk drawing rather than native tree rows."""
    columns = (("Receiving Player", 160), ("Item", 230), ("Finding Player", 160),
               ("Location", 230), ("Status", 120))

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.rows = []
        self.visible_rows = []
        self.sort_column = None
        self.sort_reverse = False
        self.filters = {}
        self.selected = None
        self.session = None
        self.text_font = font.nametofont("TkDefaultFont")
        self.header = tk.Canvas(self, background=PANEL, highlightthickness=0,
                                height=self.text_font.metrics("linespace") + 20)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.configure(cursor="hand2")
        Tooltip(self.header, "Click a column to sort; click again to reverse. Right-click to filter. * marks a filtered column.")
        self.canvas = tk.Canvas(self, background=SURFACE, highlightthickness=0, takefocus=True,
                                yscrollincrement=24)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, rowspan=2, sticky="ns")
        def scrolled(first, last):
            self.context_menu.unpost()
            scrollbar.set(first, last)
        self.canvas.configure(yscrollcommand=scrolled)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.filter_bar = ttk.Frame(self)
        self.filter_summary = ttk.Label(self.filter_bar)
        self.filter_summary.pack(side="left", fill="x", expand=True)
        ttk.Button(self.filter_bar, text="Clear Filters", command=self.clear_filters).pack(side="right")
        self.context_menu = ContextMenu(app.root, app.theme.family)
        self.header.bind("<Button-1>", self.sort_header)
        self.header.bind("<Button-3>", self.show_filter_menu)
        self.canvas.bind("<Button-3>", self.show_filter_menu)
        self.bind("<Unmap>", lambda _: self.context_menu.unpost(), add="+")
        self.bind("<Destroy>", lambda event: self.context_menu.destroy() if event.widget is self else None, add="+")
        self.canvas.bind("<Configure>", lambda _: self.redraw())
        self.canvas.bind("<Button-1>", self.select_row)
        self.canvas.bind("<Motion>", self.hover_status)
        self.canvas.bind("<Return>", self.open_status_menu)
        self.canvas.bind("<space>", self.open_status_menu)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda _: self.scroll(-3))
        self.canvas.bind("<Button-5>", lambda _: self.scroll(3))
        for key, amount in (("Up", -1), ("Down", 1)):
            self.canvas.bind(f"<{key}>", lambda _, n=amount: self.move_selection(n))
        for key, amount, units in (("Prior", -1, "pages"), ("Next", 1, "pages")):
            self.canvas.bind(f"<{key}>", lambda _, n=amount, u=units: self.scroll(n, u))

    def scroll(self, amount, units="units"):
        self.canvas.yview_scroll(amount, units)
        return "break"

    def wheel(self, event):
        return self.scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1))

    def current_session(self):
        ctx = self.app.ctx
        return ctx.server, ctx.team, ctx.slot, bool(ctx.server and ctx.server.socket.open and not ctx.server.socket.closed)

    def set_rows(self, rows, *, session=None):
        if rows != self.rows or session != self.session:
            self.session = session
            self.rows = rows
            self.apply_view()

    @staticmethod
    def sort_key(value):
        return tuple((0, int(part)) if part.isdecimal() else (1, part.casefold())
                     for part in re.split(r"(\d+)", value))

    def apply_view(self, *, reset_scroll=False):
        selected = self.visible_rows[self.selected] if self.selected is not None else None
        def matches(row):
            return all(self.filter_matches(column, row[0][column]) for column in self.filters)
        self.visible_rows = [row for row in self.rows if matches(row)]
        if self.sort_column is not None:
            self.visible_rows.sort(key=lambda row: self.sort_key(row[0][self.sort_column]), reverse=self.sort_reverse)
        self.selected = next((index for index, row in enumerate(self.visible_rows)
                              if selected is not None and row.key == selected.key), None)
        if reset_scroll:
            self.canvas.yview_moveto(0)
        if self.filters:
            descriptions = [f"{self.columns[column][0]}: {len(value)} selected" if mode == "selected" else
                            f"{self.columns[column][0]} {mode}: {value}"
                            for column, (mode, value) in self.filters.items()]
            self.filter_summary.configure(text=f"{len(self.visible_rows)} / {len(self.rows)} hints · " + "; ".join(descriptions))
            self.filter_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        else:
            self.filter_bar.grid_remove()
        self.redraw()

    def column_at(self, x):
        width = self.canvas.winfo_width()
        total = sum(weight for _, weight in self.columns)
        right = 0
        for column, (_, weight) in enumerate(self.columns):
            right += width * weight / total
            if 0 <= x < right:
                return column
        return None

    def sort_header(self, event):
        column = self.column_at(event.x)
        if column is not None:
            self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
            self.sort_column = column
            self.apply_view(reset_scroll=True)
        return "break"

    def set_filter(self, column, mode, value):
        self.filters[column] = (mode, value)
        self.apply_view(reset_scroll=True)

    def filter_matches(self, column, value):
        if column not in self.filters:
            return True
        mode, selected = self.filters[column]
        value = value.casefold()
        if mode == "selected":
            return value in selected
        return value == selected.casefold() if mode == "only" else value != selected.casefold()

    def filter_values(self, column):
        return sorted({row[0][column] for row in self.rows}, key=self.sort_key)

    def select_filter_values(self, column, values):
        selected = frozenset(value.casefold() for value in values)
        available = {value.casefold() for value in self.filter_values(column)}
        if selected == available:
            self.clear_filters(column)
        else:
            self.set_filter(column, "selected", selected)

    def clear_filters(self, column=None):
        if column is None:
            self.filters.clear()
        else:
            self.filters.pop(column, None)
        self.apply_view(reset_scroll=True)

    async def filter_column(self, column):
        from .hint_filter import HintFilterDialog
        if self.app.closed:
            return
        values = self.filter_values(column)
        selected = {value for value in values if self.filter_matches(column, value)}
        future = asyncio.get_running_loop().create_future()
        def resolve(accepted):
            if not future.done():
                future.set_result(dialog.selected_values() if accepted else None)
        dialog = HintFilterDialog(self.app.root, f"Filter {self.columns[column][0]}", values, selected, resolve)
        self.app.dialogs[dialog] = future
        dialog.show(resolve)
        try:
            result = await future
            if result is not None and not self.app.closed and self.winfo_exists():
                self.select_filter_values(column, result)
        finally:
            self.app.dialogs.pop(dialog, None)
            if not self.app.closed:
                dialog.close()

    def show_filter_menu(self, event):
        column = self.column_at(event.x)
        if column is None:
            return "break"
        menu = self.context_menu
        menu.clear()
        label = self.columns[column][0]
        menu.add_command(label=f"Filter {label}…", command=lambda: self.app.spawn(self.filter_column(column)))
        index = self.row_at(event) if event.widget is self.canvas else None
        if index is not None:
            value = self.visible_rows[index][0][column]
            caption = value.replace("\n", " ")
            caption = caption[:61] + "…" if len(caption) > 64 else caption
            menu.add_command(label=f"Show only: {caption}", command=lambda: self.set_filter(column, "only", value))
            menu.add_command(label=f"Hide: {caption}", command=lambda: self.set_filter(column, "hide", value))
        menu.add_command(label=f"Clear {label} filter", command=lambda: self.clear_filters(column),
                         state="normal" if column in self.filters else "disabled")
        menu.add_command(label="Clear all filters", command=self.clear_filters,
                         state="normal" if self.filters else "disabled")
        menu.show(event.x_root, event.y_root)
        return "break"

    def redraw(self):
        self.context_menu.unpost()
        self.canvas.configure(cursor="")
        width = self.canvas.winfo_width()
        if width <= 1:
            return
        self.filter_summary.configure(wraplength=max(100, width - 150))
        position = self.canvas.yview()[0]
        self.header.delete("all")
        self.canvas.delete("all")
        total = sum(weight for _, weight in self.columns)
        widths = [width * weight / total for _, weight in self.columns]
        x = 0
        for column, ((label, _), column_width) in enumerate(zip(self.columns, widths)):
            if column == self.sort_column:
                label += " ▼" if self.sort_reverse else " ▲"
            if column in self.filters:
                label += " *"
            self.header.create_text(x + column_width / 2, self.header.winfo_reqheight() / 2,
                                    text=label, fill=ACCENT, font=self.text_font)
            x += column_width
        y = 0
        for index, row in enumerate(self.visible_rows):
            values, colors = row.values, row.colors
            background = self.canvas.create_rectangle(0, y, width, y, outline="",
                          fill=SUBTLE if self.selected == index else PANEL if index % 2 else SURFACE,
                          tags=(f"row_{index}", f"background_{index}"))
            x, bottom = 0, y + self.text_font.metrics("linespace") + 16
            for column, (value, color, column_width) in enumerate(zip(values, colors, widths)):
                editable = column == 4 and self.can_edit(row)
                if editable:
                    control = self.canvas.create_rectangle(x + 3, y + 3, x + column_width - 3, y + 3,
                              outline=SUBTLE, fill=PANEL, tags=(f"row_{index}", f"status_control_{index}"))
                cell = self.canvas.create_text(x + 8, y + 8, anchor="nw", text=value,
                       fill=MESSAGE_COLORS[color], font=self.text_font,
                       width=max(1, column_width - (34 if editable else 16)),
                       tags=(f"row_{index}",))
                bottom = max(bottom, self.canvas.bbox(cell)[3] + 8)
                if editable:
                    control_bottom = self.canvas.bbox(cell)[3] + 5
                    self.canvas.coords(control, x + 3, y + 3, x + column_width - 3, control_bottom)
                    self.canvas.create_text(x + column_width - 13, (y + 3 + control_bottom) / 2,
                                            text="▾", fill=MESSAGE_COLORS[color], font=self.text_font,
                                            tags=(f"row_{index}",))
                x += column_width
            self.canvas.coords(background, 0, y, width, bottom)
            y = bottom
        if not self.visible_rows:
            self.canvas.create_text(16, 16, anchor="nw", fill=TEXT, font=self.text_font,
                                    text="No hints match these filters." if self.filters else "No hints yet.")
        self.canvas.configure(scrollregion=(0, 0, width, y))
        self.canvas.yview_moveto(position)

    def row_at(self, event):
        hits = self.canvas.find_overlapping(event.x, self.canvas.canvasy(event.y),
                                            event.x, self.canvas.canvasy(event.y))
        for hit in reversed(hits):
            tag = next((tag for tag in self.canvas.gettags(hit) if tag.startswith("row_")), None)
            if tag:
                return int(tag[4:])
        return None

    def select_row(self, event):
        self.canvas.focus_set()
        index = self.row_at(event)
        if index is not None:
            if self.selected is not None:
                self.canvas.itemconfigure(f"background_{self.selected}", fill=PANEL if self.selected % 2 else SURFACE)
            self.selected = index
            self.canvas.itemconfigure(f"background_{self.selected}", fill=SUBTLE)
            if self.column_at(event.x) == 4:
                self.open_status_menu()

    def can_edit(self, row):
        return (self.session is not None and self.session == self.current_session() and self.session[3]
                and self.app.ctx.slot is not None and hint_status(row.hint) != HintStatus.HINT_FOUND
                and self.app.ctx.slot_concerns_self(row.hint["receiving_player"]))

    def hover_status(self, event):
        index = self.row_at(event)
        editable = index is not None and self.column_at(event.x) == 4 and self.can_edit(self.visible_rows[index])
        self.canvas.configure(cursor="hand2" if editable else "")

    def move_selection(self, amount):
        if self.visible_rows:
            self.selected = (max(0, min(len(self.visible_rows) - 1, self.selected + amount))
                             if self.selected is not None else (0 if amount > 0 else len(self.visible_rows) - 1))
            self.redraw()
            _, top, _, bottom = self.canvas.coords(f"background_{self.selected}")
            viewport_top = self.canvas.canvasy(0)
            height = self.canvas.winfo_height()
            total_height = float(self.canvas.cget("scrollregion").split()[3])
            if top < viewport_top or bottom > viewport_top + height:
                target = top if top < viewport_top else bottom - height
                self.canvas.yview_moveto(max(0, target) / max(1, total_height))
        return "break"

    def open_status_menu(self, *_):
        if self.selected is None:
            return "break"
        row = self.visible_rows[self.selected]
        if not self.can_edit(row):
            return "break"
        session = self.session
        menu = self.context_menu
        menu.clear()
        for index, status in enumerate(EDITABLE_STATUSES):
            selected = status == hint_status(row.hint)
            menu.add_command(label=("✓  " if selected else "    ") + STATUS_NAMES[status],
                             command=lambda value=status: self.update_status(row.key, value, session))
            menu.entryconfigure(index, foreground=MESSAGE_COLORS[STATUS_COLORS[status]],
                                activeforeground=MESSAGE_COLORS[STATUS_COLORS[status]],
                                font=self.text_font, pady=6)
        left, _, _, bottom = self.canvas.coords(f"status_control_{self.selected}")
        menu.show(round(self.canvas.winfo_rootx() + left),
                  round(self.canvas.winfo_rooty() + bottom + 1 - self.canvas.canvasy(0)))
        current = hint_status(row.hint)
        menu.buttons[EDITABLE_STATUSES.index(current) if current in EDITABLE_STATUSES else 0].focus_set()
        return "break"

    def update_status(self, key, status, session):
        # Recheck the live row/session: a hint may be found or the client may disconnect while the menu is open.
        row = next((row for row in self.rows if row.key == key), None)
        if (row is not None and session == self.session and self.can_edit(row)
                and status in EDITABLE_STATUSES and status != hint_status(row.hint)):
            self.app.ctx.update_hint(row.hint["location"], row.hint["finding_player"], status)
        # The normal server hint notification supplies the authoritative status and reapplies filters/sorting.


class HintsView:
    def __init__(self, app, parent):
        self.app, self.parent = app, parent
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text="New Hint:").pack(side="left")
        self.query = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.query)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self.submit)
        self.entry.bind("<Down>", self.focus_matches)
        self.entry.bind("<Escape>", self.dismiss)
        self.entry.bind("<FocusOut>", self.defer_dismiss)
        Tooltip(self.entry, "Type at least 3 characters to find an item. Choose a match or press Enter to request a hint.")
        ttk.Button(row, text="Hint", style="Primary.TButton", command=self.submit).pack(side="right", padx=(0, 4))
        self.table = HintTable(parent, app)
        self.table.pack(fill="both", expand=True)
        self.popup = ttk.Frame(parent)
        self.matches = tk.Listbox(self.popup, background=PANEL, foreground=TEXT, selectbackground=SUBTLE,
                                  selectforeground=TEXT, font="TkDefaultFont", borderwidth=0,
                                  highlightthickness=1, highlightbackground=SUBTLE, exportselection=False)
        match_scroll = ttk.Scrollbar(self.popup, command=self.matches.yview)
        self.matches.configure(yscrollcommand=match_scroll.set)
        match_scroll.pack(side="right", fill="y")
        self.matches.pack(fill="both", expand=True)
        self.matches.bind("<ButtonRelease-1>", self.choose)
        self.matches.bind("<Return>", self.choose)
        self.matches.bind("<Escape>", self.dismiss)
        self.matches.bind("<FocusOut>", self.defer_dismiss)
        parent.bind("<Unmap>", self.dismiss, add="+")
        parent.bind("<Configure>", self.dismiss, add="+")
        self.query.trace_add("write", self.suggest)

    def refresh(self, hints):
        ctx = self.app.ctx
        rows = []
        for hint in hints:
            status = hint_status(hint)
            values = (
                ctx.player_names.get(hint["receiving_player"], "?"),
                ctx.item_names.lookup_in_slot(hint["item"], hint["receiving_player"]),
                ctx.player_names.get(hint["finding_player"], "?"),
                ctx.location_names.lookup_in_slot(hint["location"], hint["finding_player"]),
                STATUS_NAMES.get(status, "Unknown"),
            )
            flags = hint.get("item_flags", 0)
            colors = (
                "magenta" if ctx.slot_concerns_self(hint["receiving_player"]) else "yellow",
                "plum" if flags & 1 else "slateblue" if flags & 2 else "salmon" if flags & 4 else "cyan",
                "magenta" if ctx.slot_concerns_self(hint["finding_player"]) else "yellow",
                "green", STATUS_COLORS.get(status, "red"),
            )
            rows.append(HintRow(values, colors, dict(hint)))
        self.table.set_rows(rows, session=self.table.current_session())

    def suggest(self, *_):
        query = self.query.get().strip().casefold()
        self.matches.delete(0, "end")
        if len(query) < 3 or not self.app.ctx.game or not self.entry.winfo_ismapped():
            self.dismiss()
            return
        names = self.app.ctx.item_names[self.app.ctx.game].values()
        matches = sorted({name for name in names if query in name.casefold()}, key=str.casefold)
        if not matches:
            self.dismiss()
            return
        self.matches.insert("end", *matches)
        self.matches.configure(height=min(8, len(matches)))
        self.popup.place(x=self.entry.winfo_x(), y=self.entry.winfo_height(), width=self.entry.winfo_width())
        self.popup.lift()

    def focus_matches(self, *_):
        if self.matches.size() and self.popup.winfo_ismapped():
            self.matches.focus_set()
            self.matches.selection_set(0)
            self.matches.activate(0)
            return "break"

    def choose(self, *_):
        selected = self.matches.curselection()
        if selected:
            self.query.set(self.matches.get(selected[0]))
            self.dismiss()
            self.entry.focus_set()
            self.entry.icursor("end")
        return "break"

    def dismiss(self, *_):
        self.popup.place_forget()
        return "break"

    def defer_dismiss(self, *_):
        def check():
            if self.parent.winfo_exists() and self.parent.focus_get() not in (self.entry, self.matches):
                self.dismiss()
        self.parent.after_idle(check)

    def submit(self, *_):
        query = self.query.get().strip()
        self.dismiss()
        if query:
            command = "!hint " + query
            self.app.ctx.on_ui_command(command)
            self.app.commandprocessor(command)
        return "break"
