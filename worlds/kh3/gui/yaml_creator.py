# SPDX-License-Identifier: GPL-3.0-only
"""Standalone Tk player-options editor with compact rows and the client's blue accents."""

from pathlib import Path
import re
import tkinter as tk
from tkinter import filedialog, font, messagebox, simpledialog, ttk

import yaml

from Options import Choice, NamedRange, OptionCounter, OptionSet, Range, Toggle
from ..Options import ExpMultiplier
from ..yaml_options import (PlayerOptions, catalog, clamp_range_value, default_value, entry_label, is_random,
                           normalize_value, option_description)
from ..yaml_presets import PresetStore
from .theme import ACCENT, PRIMARY, Theme
from .widgets import Entry, Switch
from .yaml_tooltips import SharedTooltip, TooltipSurface
from .window import configure_window, suspend_redraw

BG = "#060708"
PANEL = "#0d1114"
FIELD = "#12171a"
TEXT = "#e1e6dc"
MUTED = "#8a938a"
LINE = "#273038"
BLUE = ACCENT
SELECTED = PRIMARY


class AutoScrollbar(ttk.Scrollbar):
    """Show a packed scrollbar only when its content reports overflow."""

    def __init__(self, parent, content, *, orient="vertical"):
        self.vertical = orient == "vertical"
        super().__init__(parent, orient=orient, command=content.yview if self.vertical else content.xview)
        self.content = content
        content.configure(**{"yscrollcommand" if self.vertical else "xscrollcommand": self.set})

    def set(self, first, last):
        if float(first) <= 0 and float(last) >= 1:
            if self.winfo_manager():
                self.pack_forget()
        elif not self.winfo_manager():
            self.pack(side="right" if self.vertical else "bottom", fill="y" if self.vertical else "x",
                      before=self.content)
        super().set(first, last)


class PillSelector(ttk.Frame):
    """Native radio buttons with rounded faces and width-bounded wrapping."""

    def __init__(self, parent, choices, variable, command):
        super().__init__(parent, style="Selector.TFrame")
        self.buttons = []
        for value, caption in choices:
            button = ttk.Radiobutton(self, text=caption, value=value, variable=variable,
                                     command=command, style="Pill.TRadiobutton")
            self.buttons.append(button)

    def fit(self, width):
        if getattr(self, "fit_width", None) == width:
            return
        self.fit_width = width
        sizes = [(button.winfo_reqwidth(), button.winfo_reqheight()) for button in self.buttons]
        width = max(1, min(width, sum(size[0] for size in sizes) + 6))
        x = y = 3
        row_height = 0
        for button, (button_width, button_height) in zip(self.buttons, sizes):
            button_width = min(button_width, width - 6)
            if x > 3 and x + button_width > width - 3:
                x = 3
                y += row_height + 2
                row_height = 0
            button.place(x=x, y=y, width=button_width, height=button_height)
            x += button_width
            row_height = max(row_height, button_height)
        self.configure(width=width, height=y + row_height + 3)


class YamlCreator:
    def __init__(self, preset_directory=None):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Kingdom Hearts III · YAML Creator")
        self.root.geometry("1180x850")
        self.root.minsize(960, 680)
        self.font_size = 12
        self.theme = Theme(self.root, font_size=self.font_size)
        self.font_specs = {"normal": (0, "normal"), "bold": (0, "bold"),
                           "small": (-1, "normal"), "small_bold": (-1, "bold"),
                           "heading": (7, "bold"), "mono": (0, "normal")}
        self.fonts = {name: font.Font(root=self.root, family="Consolas" if name == "mono" else self.theme.family,
                                      size=self.font_size + offset, weight=weight)
                      for name, (offset, weight) in self.font_specs.items()}
        configure_window(self.root)
        self._style()
        self.row_icons = {name: self.theme.icon(name) for name in ("reset", "help")}
        self.tooltip_surface = TooltipSurface(self.root)
        self.model = PlayerOptions()
        self.review = None
        self.model.name = ""
        self.presets = PresetStore(preset_directory)
        self.active_preset = None
        self.current_group = next(iter(self.model.groups))
        self.pending_search = None
        self.editors = {}
        self.rows = {}
        self.visible_frames = ()
        self.showing_groups = False
        self.dirty_rows = set()
        self.rendering = False
        self.updating_controls = False
        self.name = tk.StringVar(self.root, value=self.model.name)
        self.search = tk.StringVar(self.root)
        self.changed_only = tk.BooleanVar(self.root)
        self.status = tk.StringVar(self.root)
        self.zoom_text = tk.StringVar(self.root, value=f"Zoom {self.font_size} pt")
        self.preset_name = tk.StringVar(self.root, value="Saved settings…")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._refresh_presets()
        self.root.update_idletasks()
        self.name.trace_add("write", self._name_changed)
        self.search.trace_add("write", self._schedule_search)
        self.show_group(self.current_group)
        self.root.bind("<Control-s>", lambda _: self.save_preset())
        self.root.bind("<Control-Shift-S>", lambda _: self.export_file())
        self.root.bind("<Control-o>", lambda _: self.import_file())
        self.root.bind("<Control-f>", lambda _: self.search_entry.focus_set())
        self.root.bind("<MouseWheel>", self._wheel)
        self.root.bind("<Button-4>", self._wheel)
        self.root.bind("<Button-5>", self._wheel)
        self.root.deiconify()

    def _style(self):
        self.root.configure(background=BG)
        style = self.theme.style
        style.configure(".", font=self.fonts["normal"])
        self.root.option_add("*Text.font", self.fonts["normal"])
        self.root.option_add("*TCombobox*Listbox.font", self.fonts["normal"])
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Eyebrow.TLabel", foreground=BLUE, font=self.fonts["small_bold"])
        style.configure("Section.TLabel", foreground=TEXT, font=self.fonts["heading"])
        style.configure("Card.TFrame", background=PANEL)
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure("CardTitle.TLabel", background=PANEL, foreground=TEXT,
                        font=self.fonts["bold"])
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("TEntry", fieldbackground=FIELD, bordercolor=LINE, foreground=TEXT)
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                        arrowcolor=BLUE, bordercolor=LINE, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", FIELD)],
                  selectforeground=[("readonly", TEXT)], background=[("active", SELECTED)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        track = self.theme._pill(FIELD, LINE, radius=12)
        style.element_create("Yaml.Selector.border", "image", track, border=12, padding=0,
                             width=24, height=24, sticky="nsew")
        style.layout("Selector.TFrame", [("Yaml.Selector.border", {"sticky": "nsew"})])
        normal = self.theme._pill(FIELD, FIELD, radius=10)
        selected = self.theme._pill(SELECTED, BLUE, radius=10)
        hover = self.theme._pill(SELECTED, SELECTED, radius=10)
        style.element_create("Yaml.Pill.border", "image", normal, ("selected", selected),
                             ("active", hover), border=10, padding=0, width=20, height=20, sticky="nsew")
        style.layout("Pill.TRadiobutton", [("Yaml.Pill.border", {"sticky": "nsew", "children": [
            ("Radiobutton.padding", {"sticky": "nsew", "children": [
                ("Radiobutton.focus", {"sticky": "nsew", "children": [
                    ("Radiobutton.label", {"sticky": "nsew"})]})]})]})])
        style.configure("Pill.TRadiobutton", background=PANEL, foreground=TEXT,
                        font=self.fonts["small_bold"], padding=(9, 3), anchor="center")
        style.map("Pill.TRadiobutton", foreground=[("selected", "#ffffff"), ("!selected", "#eef2f7")],
                  background=[("active", PANEL), ("!active", PANEL)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Card.TCheckbutton", background=PANEL, padding=(0, 2))
        style.map("Card.TCheckbutton", background=[("active", PANEL), ("!active", PANEL)])
        style.configure("Card.Switch.TCheckbutton", background=PANEL, padding=(0, 2))
        style.map("Card.Switch.TCheckbutton", background=[("active", PANEL), ("!active", PANEL)])
        style.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT, padding=4, arrowcolor=BLUE)
        self.category_font = self.fonts["normal"]
        style.configure("Category.Treeview", background=BG, fieldbackground=BG, foreground="#cbd5df",
                        font=self.category_font, rowheight=self.category_font.metrics("linespace") + 12,
                        borderwidth=0, relief="flat", bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.map("Category.Treeview", background=[("selected", SELECTED), ("!selected", BG)],
                  foreground=[("selected", "#ffffff"), ("!selected", "#cbd5df")])
        style.layout("Category.Treeview.Item", [("Treeitem.padding", {"sticky": "nsew", "children": [
            ("Treeitem.text", {"sticky": "w"})]})])
        style.configure("Category.Treeview.Item", padding=(10, 0))

    def button(self, parent, text, command, *, primary=False):
        return tk.Button(parent, text=text, command=command, relief="flat", borderwidth=0,
                         background=PRIMARY if primary else FIELD,
                         foreground=TEXT, activebackground=SELECTED,
                         activeforeground=TEXT, padx=16, pady=10, cursor="hand2",
                         font=self.fonts["bold" if primary else "normal"])

    def icon_button(self, parent, name, command):
        return tk.Button(parent, image=self.row_icons[name],
                         text="Reset to default" if name == "reset" else "Help", command=command,
                         relief="flat", borderwidth=0, highlightthickness=0, background=FIELD,
                         activebackground=SELECTED, padx=4, pady=0, cursor="hand2", takefocus=True)

    def tooltip(self, widget, text):
        return SharedTooltip(widget, text, self.tooltip_surface)

    def _build(self):
        actions = ttk.Frame(self.root, padding=(26, 16, 26, 18))
        self.actions = actions
        actions.pack(fill="x")
        player_name = Entry(actions, textvariable=self.name, placeholder="Player name", width=22)
        player_name.placeholder_label.configure(background=FIELD)
        player_name.pack(side="left")
        self.button(actions, "Export YAML", self.export_file, primary=True).pack(side="right")
        self.button(actions, "Preview", self.preview).pack(side="right", padx=8)
        self.button(actions, "Import YAML", self.import_file).pack(side="right")
        self.button(actions, "Reset", self.reset).pack(side="right", padx=8)
        body = ttk.Frame(self.root, padding=(26, 0, 26, 0))
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, width=235)
        sidebar.pack(side="left", fill="y", padx=(0, 22))
        self.preset_picker = ttk.Combobox(sidebar, textvariable=self.preset_name, state="readonly", width=26)
        self.preset_picker.pack(fill="x")
        self.preset_picker.bind("<<ComboboxSelected>>", self.load_preset)
        self.preset_picker.tooltip = self.tooltip(self.preset_picker, "Load saved settings. Your player name stays the same.")
        preset_actions = ttk.Frame(sidebar)
        preset_actions.pack(fill="x", pady=(6, 14))
        save = self.button(preset_actions, "Save", self.save_preset)
        save_as = self.button(preset_actions, "Save as…", lambda: self.save_preset(as_new=True))
        self.delete_preset_button = self.button(preset_actions, "Delete", self.delete_preset)
        self.delete_preset_button.configure(state="disabled", disabledforeground=MUTED)
        for index, button in enumerate((save, save_as, self.delete_preset_button)):
            button.configure(padx=9, pady=7)
            button.pack(side="left", fill="x", expand=True, padx=(6 if index else 0, 0))
        self.search_entry = Entry(sidebar, textvariable=self.search, placeholder="Search ...", width=26)
        self.search_entry.placeholder_label.configure(background=FIELD)
        self.search_entry.pack(fill="x")
        self.search_entry.tooltip = self.tooltip(self.search_entry, "Search every category by name or description (Ctrl+F).")
        ttk.Checkbutton(sidebar, text="Changed settings only", variable=self.changed_only,
                        command=self.render).pack(anchor="w", pady=(8, 12))
        navigation = ttk.Frame(sidebar)
        navigation.pack(fill="both", expand=True)
        self.nav = ttk.Treeview(navigation, show="tree", selectmode="browse", style="Category.Treeview", height=1)
        self.nav_scroll = AutoScrollbar(navigation, self.nav)
        self.nav.pack(side="left", fill="both", expand=True)
        category_width = max(self.category_font.measure(self.group_label(group)) for group in self.model.groups) + 24
        self.nav.column("#0", width=category_width, minwidth=category_width, stretch=True)
        for group in self.model.groups:
            self.nav.insert("", "end", iid=group, text=self.group_label(group))
        self.nav.bind("<<TreeviewSelect>>", self._select_group)
        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True)
        self.heading = ttk.Label(main, style="Section.TLabel")
        self.heading.pack(anchor="w")
        self.summary = ttk.Label(main, style="Muted.TLabel")
        self.summary.pack(anchor="w", pady=(4, 14))
        viewport = ttk.Frame(main)
        viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(viewport, background=BG, highlightthickness=0)
        self.settings_scroll = AutoScrollbar(viewport, self.canvas)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.cards = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window(0, 0, window=self.cards, anchor="nw")
        self.cards.bind("<Configure>", self._sync_scrollregion)
        self.canvas.bind("<Configure>", self._resize)
        footer = ttk.Frame(self.root, padding=(26, 12))
        footer.pack(fill="x")
        self.zoom_out = self.button(footer, "−", lambda: self.change_zoom(-1))
        self.zoom_out.configure(padx=8, pady=3)
        self.zoom_out.pack(side="left")
        ttk.Label(footer, textvariable=self.zoom_text, style="Muted.TLabel").pack(side="left", padx=8)
        self.zoom_in = self.button(footer, "+", lambda: self.change_zoom(1))
        self.zoom_in.configure(padx=8, pady=3)
        self.zoom_in.pack(side="left")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left", padx=(18, 0))
        self.empty_label = ttk.Label(self.cards, text="No matching settings.", style="Muted.TLabel")

    def change_zoom(self, step):
        size = max(8, min(20, self.font_size + step))
        if size == self.font_size:
            return
        self.font_size = size
        self.rendering = True
        for name, (offset, _) in self.font_specs.items():
            self.fonts[name].configure(size=size + offset)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name, root=self.root).configure(size=size)
        self.theme.style.configure("Category.Treeview", rowheight=self.category_font.metrics("linespace") + 12)
        category_width = max(self.category_font.measure(self.group_label(group)) for group in self.model.groups) + 24
        self.nav.column("#0", width=category_width, minwidth=category_width)
        for row in self.rows.values():
            row["frame"].grid_rowconfigure(0, minsize=0)
            if row["selector"] is not None:
                row["selector"].fit_width = None
        self.tooltip_surface.invalidate()
        self.zoom_text.set(f"Zoom {size} pt")
        self.zoom_out.configure(state="disabled" if size == 8 else "normal")
        self.zoom_in.configure(state="disabled" if size == 20 else "normal")
        self.root.update_idletasks()
        self.root.minsize(max(960, self.actions.winfo_reqwidth()), 680)
        self.render(reset_scroll=False)

    @staticmethod
    def group_label(group):
        return group.removeprefix("KH3 ").replace("Enemy and Party Pools (Experimental)", "Enemies & party (experimental)")

    def _resize(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)
        if not self.rendering:
            self._fit_rows(event.width)
            self._sync_scrollregion()

    def _sync_scrollregion(self, *_):
        if not self.rendering:
            content_height = self.cards.winfo_reqheight()
            viewport_height = self.canvas.winfo_height()
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(),
                                               max(content_height, viewport_height)))
            if content_height <= viewport_height:
                self.canvas.yview_moveto(0)

    def _fit_rows(self, width):
        for selector in getattr(self, "pill_selectors", []):
            selector.fit(max(160, width - 370))
        # Controls must have their final requested widths before labels wrap.
        self.cards.update_idletasks()
        for label, controls in getattr(self, "row_labels", []):
            wrap_width = max(140, width - controls.winfo_reqwidth() - 60)
            if getattr(label, "wrap_width", None) != wrap_width:
                label.configure(wraplength=wrap_width)
                label.wrap_width = wrap_width

    def _wheel(self, event):
        if event.widget.winfo_toplevel() != self.root or isinstance(event.widget, (tk.Listbox, ttk.Treeview, ttk.Spinbox, ttk.Combobox)):
            return
        if self.cards.winfo_reqheight() <= self.canvas.winfo_height():
            return "break"
        steps = (-1 if getattr(event, "num", 0) == 4 else
                 1 if getattr(event, "num", 0) == 5 else -int(event.delta / 120))
        first, last = self.canvas.yview()
        if (steps < 0 and first <= 0) or (steps > 0 and last >= 1):
            return "break"
        self.canvas.yview_scroll(steps, "units")
        return "break"

    def _name_changed(self, *_):
        self.model.name = self.name.get()

    def _schedule_search(self, *_):
        if self.pending_search is not None:
            self.root.after_cancel(self.pending_search)
        self.pending_search = self.root.after(120, self.render)

    def _select_group(self, _):
        selection = self.nav.selection()
        if selection and (selection[0] != self.current_group or self.search.get()):
            self.show_group(selection[0])

    def show_group(self, group):
        self.current_group = group
        self.search.set("")
        if self.nav.selection() != (group,):
            self.nav.selection_set(group)
        self.nav.focus(group)
        self.nav.see(group)
        self.render()

    def render(self, *, reset_scroll=True):
        with suspend_redraw(self.canvas) as batched:
            try:
                self._render(reset_scroll=reset_scroll, batched=batched)
            finally:
                self.rendering = False

    def _render(self, *, reset_scroll, batched):
        if self.pending_search is not None:
            self.root.after_cancel(self.pending_search)
            self.pending_search = None
        self.rendering = True
        first_layout = not self.rows
        # Build each option once, then retain it across navigation and searches.
        # Model replacements update existing controls. Rebuild only when the
        # control type changes, e.g. a fixed value becomes weighted or random.
        for group, options in self.model.groups.items():
            for key, option in options.items():
                if key not in self.rows or key in self.dirty_rows:
                    if key in self.rows and self._sync_row(key, option):
                        continue
                    self.editors.pop(key, None)
                    self.rows[key] = self._card(key, option, group, row=self.rows.get(key))
        self.dirty_rows.clear()
        if first_layout:
            # Warm every row while the top-level window is still hidden, so
            # a broad first search also has no first-use layout cost.
            self.row_labels = [(row["label"], row["controls"]) for row in self.rows.values()]
            self.pill_selectors = [row["selector"] for row in self.rows.values() if row["selector"] is not None]
            self._fit_rows(self.canvas.winfo_width())
        query = self.search.get().strip().casefold()
        all_groups = bool(query or self.changed_only.get())
        groups = self.model.groups if all_groups else {self.current_group: self.model.groups[self.current_group]}
        visible = []
        for group, options in groups.items():
            for key, option in options.items():
                if query and query not in self.rows[key]["search_text"]:
                    continue
                if self.changed_only.get() and self.model.values[key] == default_value(option):
                    continue
                row = self.rows[key]
                visible.append(row)
        visible_frames = tuple(row["frame"] for row in visible)
        layout_changed = visible_frames != self.visible_frames or all_groups != self.showing_groups
        if layout_changed:
            if not batched:
                self.canvas.itemconfigure(self.canvas_window, state="hidden")
            for frame in self.visible_frames:
                if frame.winfo_exists():
                    frame.pack_forget()
            self.empty_label.pack_forget()
            for row in visible:
                row["frame"].pack(fill="x", pady=(0, 4))
                if all_groups:
                    row["group"].pack(anchor="w")
                else:
                    row["group"].pack_forget()
            self.visible_frames = visible_frames
            self.showing_groups = all_groups
        self.row_labels = [(row["label"], row["controls"]) for row in visible]
        self.pill_selectors = [row["selector"] for row in visible if row["selector"] is not None]
        self.canvas.itemconfigure(self.canvas_window, width=self.canvas.winfo_width())
        self._fit_rows(self.canvas.winfo_width())
        self.heading.configure(text="Search results" if query else "Changed settings" if self.changed_only.get()
                               else self.group_label(self.current_group))
        self.summary.configure(text=f"{len(visible)} settings")
        if not visible:
            self.empty_label.pack(anchor="w", pady=20)
        self.cards.update_idletasks()
        self.rendering = False
        self._sync_scrollregion()
        if reset_scroll:
            self.canvas.yview_moveto(0)
        if layout_changed and not batched:
            self.canvas.itemconfigure(self.canvas_window, state="normal")

    def _set(self, key, value):
        if self.updating_controls:
            return
        try:
            value = normalize_value(self.model.types[key], value)
        except Exception:
            pass  # Keep partially typed numbers visible; validate again on export.
        self.model.values[key] = value
        self.dirty_rows.add(key)
        self.status.set("")

    @staticmethod
    def _control_kind(option, value):
        if issubclass(option, (OptionSet, OptionCounter)):
            return "picker"
        if is_random(value) and issubclass(option, (Toggle, Choice, Range)):
            return "random"
        if isinstance(value, dict):
            return "weighted"
        return "value"

    def _sync_row(self, key, option):
        """Apply a new model value without recreating the row or firing edits."""
        row = self.rows[key]
        value = self.model.values[key]
        if row["kind"] != self._control_kind(option, value):
            return False
        self.updating_controls = True
        try:
            if row["kind"] == "picker":
                caption = f"{len(value)} selected…"
                if row["picker"].cget("text") != caption:
                    row["picker"].configure(text=caption)
                row["picker"].tooltip.text = ", ".join(entry_label(option, name) for name in value) if value else "Choose entries"
            elif row["kind"] == "value":
                variable = self.editors[key]
                desired = bool(value) if issubclass(option, Toggle) else str(value)
                if variable.get() != desired:
                    variable.set(desired)
                if row["switch"] is not None:
                    caption = "  On" if value else "  Off"
                    if row["switch"].cget("text") != caption:
                        row["switch"].configure(text=caption)
        finally:
            self.updating_controls = False
        return True

    def _card(self, key, option, group, *, row=None):
        if row is not None:
            # Keep the option's established height when a shorter random or
            # weighted label replaces its spinner, switch or wrapped pills.
            height = max(row["controls"].winfo_reqheight(), row["label"].master.winfo_reqheight(),
                         row["frame"].grid_rowconfigure(0)["minsize"])
            row["frame"].grid_rowconfigure(0, minsize=height)
            for child in row["editor"].winfo_children():
                child.destroy()
            return self._populate_editor(key, option, row)
        card = ttk.Frame(self.cards, style="Card.TFrame", padding=(12, 7))
        card.columnconfigure(0, weight=1)
        title = ttk.Frame(card, style="Card.TFrame")
        title.grid(row=0, column=0, sticky="w", padx=(0, 14))
        label = ttk.Label(title, text=option.display_name, style="CardTitle.TLabel", wraplength=240)
        label.pack(anchor="w")
        description = option_description(option)
        label.tooltip = self.tooltip(label, description + "\n\nDefault: " + self._default_label(option))
        group_label = ttk.Label(title, text=self.group_label(group), style="CardMuted.TLabel")
        controls = ttk.Frame(card, style="Card.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        reset = self.icon_button(controls, "reset", lambda: self.reset_option(key))
        reset.pack(side="right", padx=(10, 0))
        reset.tooltip = self.tooltip(reset, "Reset to default: " + self._default_label(option))
        help_button = self.icon_button(controls, "help", lambda: messagebox.showinfo(option.display_name, description, parent=self.root))
        help_button.pack(side="right", padx=(8, 0))
        help_button.tooltip = self.tooltip(help_button, description)
        random = None
        random_button = None
        if issubclass(option, (Toggle, Choice, Range)):
            random = tk.BooleanVar(self.root)
            random_button = ttk.Checkbutton(controls, text="Random", variable=random, style="Card.TCheckbutton",
                                            command=lambda: self._randomize(key, random.get()))
            random_button.pack(side="right", padx=(14, 0))
        editor = ttk.Frame(controls, style="Card.TFrame")
        editor.pack(side="left")
        row = {"frame": card, "label": label, "controls": controls, "group": group_label,
               "editor": editor, "reset": reset, "help": help_button, "random": random,
               "random_button": random_button,
               "search_text": f"{key} {option.display_name} {description} {group}".casefold()}
        return self._populate_editor(key, option, row)

    def _populate_editor(self, key, option, row):
        controls = row["editor"]
        selector = picker = switch = None
        value = self.model.values[key]
        if row["random"] is not None:
            row["random"].set(is_random(value))
        if issubclass(option, (OptionSet, OptionCounter)):
            count = len(value)
            picker = self.button(controls, f"{count} selected…", lambda: self.pick_entries(key))
            picker.configure(pady=3, padx=10)
            picker.pack(side="left")
            picker.tooltip = self.tooltip(picker, ", ".join(entry_label(option, name) for name in value) if count else "Choose entries")
        elif is_random(value) and issubclass(option, (Toggle, Choice, Range)):
            ttk.Label(controls, text="Randomized", style="CardMuted.TLabel").pack(side="left")
        elif isinstance(value, dict):
            self.button(controls, "Edit weighted choices", lambda: self.edit_weights(key)).pack(side="left")
        else:
            variable = tk.StringVar(self.root, value=str(value).lower() if isinstance(value, bool) else str(value))
            self.editors[key] = variable
            if issubclass(option, Toggle):
                enabled = tk.BooleanVar(self.root, value=bool(value))
                self.editors[key] = enabled
                switch = Switch(controls, text="On" if enabled.get() else "Off", variable=enabled)
                switch.configure(style="Card.Switch.TCheckbutton")

                def toggle():
                    self._set(key, enabled.get())
                    switch.configure(text="  On" if enabled.get() else "  Off")

                switch.configure(command=toggle)
                switch.pack(side="left")
            elif issubclass(option, Choice):
                choices = [(str(name), "On" if name == "true" else "Off" if name == "false" else
                            name.replace("_", " ").title()) for name in option.name_lookup.values()]
                if issubclass(option, ExpMultiplier):
                    choices = [(name, option.get_option_name(value).replace("x", "×"))
                               for value, name in option.name_lookup.items()]
                selector = PillSelector(controls, choices, variable,
                                        lambda k=key, v=variable: self._set(k, v.get()))
                selector.pack(side="left")
            elif issubclass(option, Range):
                spin = ttk.Spinbox(controls, from_=option.range_start, to=option.range_end, textvariable=variable, width=6)
                spin.pack(side="left")
                variable.trace_add("write", lambda *_, k=key, v=variable: self._set(k, v.get()))
                spin.bind("<FocusOut>", lambda _, k=key: self._commit_range(k))
                spin.bind("<Return>", lambda _, k=key: self._commit_range(k))
                spin.tooltip = self.tooltip(spin, f"Range: {option.range_start} – {option.range_end}")
                if issubclass(option, NamedRange) and key != "battle_level_offset":
                    for name, number in option.special_range_names.items():
                        preset = self.button(controls, name.replace("_", " ").title(), lambda n=number, v=variable: v.set(str(n)))
                        preset.configure(pady=3, padx=6)
                        preset.pack(side="left", padx=4)
            else:
                ttk.Entry(controls, textvariable=variable, width=35).pack(side="left")
                variable.trace_add("write", lambda *_, k=key, v=variable: self._set(k, v.get()))
        row.update(selector=selector, kind=self._control_kind(option, value), picker=picker, switch=switch)
        return row

    @staticmethod
    def _default_label(option):
        value = default_value(option)
        if value in ([], {}):
            return "None selected"
        if isinstance(value, bool):
            return "On" if value else "Off"
        if issubclass(option, ExpMultiplier):
            return option.get_option_name(option.default).replace("x", "×")
        return str(value).replace("_", " ")

    def _randomize(self, key, enabled):
        self._set(key, "random" if enabled else default_value(self.model.types[key]))
        self._refresh_option(key)

    def _commit_range(self, key):
        value = clamp_range_value(self.model.types[key], self.model.values[key])
        if value != self.model.values[key]:
            self._set(key, value)
        variable = self.editors.get(key)
        if variable is not None and type(value) is int and variable.get() != str(value):
            variable.set(str(value))

    def _commit_ranges(self):
        for key, option in self.model.types.items():
            if issubclass(option, Range):
                self._commit_range(key)

    def reset_option(self, key):
        self._set(key, default_value(self.model.types[key]))
        self._refresh_option(key)

    def _refresh_option(self, key):
        if self.changed_only.get():
            self.render(reset_scroll=False)
            return
        row = self.rows[key]
        option = self.model.types[key]
        if not self._sync_row(key, option):
            self.editors.pop(key, None)
            self._card(key, option, "", row=row)
        self.dirty_rows.discard(key)
        self.pill_selectors = [candidate["selector"] for candidate in self.rows.values()
                               if candidate["frame"] in self.visible_frames and candidate["selector"] is not None]
        if row["selector"] is not None:
            row["selector"].fit(max(160, self.canvas.winfo_width() - 370))
        self.cards.update_idletasks()
        wrap_width = max(140, self.canvas.winfo_width() - row["controls"].winfo_reqwidth() - 60)
        if getattr(row["label"], "wrap_width", None) != wrap_width:
            row["label"].configure(wraplength=wrap_width)
            row["label"].wrap_width = wrap_width

    def pick_entries(self, key):
        option = self.model.types[key]
        counter = issubclass(option, OptionCounter)
        selected = dict(self.model.values[key]) if counter else {name: 1 for name in self.model.values[key]}
        dialog = tk.Toplevel(self.root)
        dialog.title(option.display_name)
        dialog.geometry("760x620")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=option.display_name, style="Section.TLabel").pack(anchor="w")
        query = tk.StringVar(dialog)
        ttk.Entry(body, textvariable=query).pack(fill="x", pady=(0, 8))
        lists = ttk.Frame(body)
        lists.pack(fill="both", expand=True)
        category = tk.StringVar(dialog, value="All")
        song_catalog = getattr(option, "song_catalog", None)
        entry_categories = {}
        if song_catalog:
            entry_categories.update((song["key"], "Songs") for song in song_catalog["songs"])
            entry_categories.update((group["key"], group["kind"].title()) for group in song_catalog["groups"])
        boxes = []
        for heading in ("Available", "Selected"):
            column = ttk.Frame(lists)
            column.pack(side="left", fill="both", expand=True, padx=(0, 8))
            header = ttk.Frame(column)
            header.pack(fill="x", pady=6)
            ttk.Label(header, text=heading, style="Eyebrow.TLabel").pack(side="left")
            if heading == "Available" and song_catalog:
                ttk.Combobox(header, textvariable=category, state="readonly", width=10,
                             values=("All", "Songs", "Source", "Theme", "World")).pack(side="right")
            box = tk.Listbox(column, selectmode="extended", exportselection=False, background=FIELD,
                             foreground=TEXT, selectbackground=SELECTED, selectforeground=BLUE,
                             highlightthickness=0, borderwidth=0, width=28, font=self.fonts["small"])
            AutoScrollbar(column, box)
            box.pack(fill="both", expand=True)
            boxes.append(box)
        available, chosen = boxes
        entries = catalog(option)
        visible = []
        chosen_names = []
        error = tk.StringVar(dialog)

        def refresh(*_):
            visible[:] = [name for name in entries
                          if (category.get() == "All" or entry_categories.get(name) == category.get())
                          and query.get().casefold() in entry_label(option, name).casefold()]
            available.delete(0, "end")
            for name in visible:
                available.insert("end", entry_label(option, name))
            chosen_names[:] = sorted(selected, key=lambda name: entry_label(option, name).casefold())
            chosen.delete(0, "end")
            for name in chosen_names:
                label = entry_label(option, name)
                chosen.insert("end", f"{selected[name]} × {label}" if counter else label)

        def add():
            try:
                number = max(1, min(999, int(amount.get()))) if counter else 1
                if counter:
                    amount.set(str(number))
            except ValueError:
                error.set("Enter a whole number from 1 to 999.")
                return
            error.set("")
            for index in available.curselection():
                selected[visible[index]] = number
            refresh()

        def remove():
            for index in chosen.curselection():
                selected.pop(chosen_names[index], None)
            refresh()

        def apply():
            try:
                value = normalize_value(option, selected if counter else sorted(selected))
            except Exception as exc:
                error.set(str(exc))
                return
            self._set(key, value)
            dialog.destroy()
            self.render()

        ttk.Label(body, textvariable=error, foreground="#ffb1bb", wraplength=680).pack(fill="x", pady=(8, 0))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(12, 0))
        amount = tk.StringVar(dialog, value="1")
        if counter:
            ttk.Label(row, text="Count").pack(side="left", padx=(0, 5))
            ttk.Spinbox(row, from_=1, to=999, textvariable=amount, width=5).pack(side="left", padx=(0, 8))
        self.button(row, "Add / set count" if counter else "Add", add).pack(side="left")
        self.button(row, "Remove", remove).pack(side="left", padx=6)
        self.button(row, "Apply", apply, primary=True).pack(side="right")
        self.button(row, "Cancel", dialog.destroy).pack(side="right", padx=6)
        query.trace_add("write", refresh)
        category.trace_add("write", refresh)
        refresh()

    def edit_weights(self, key):
        dialog = tk.Toplevel(self.root)
        dialog.title("Weighted choices · " + self.model.types[key].display_name)
        dialog.transient(self.root)
        dialog.grab_set()
        text = tk.Text(dialog, width=60, height=16, background=FIELD, foreground=TEXT)
        text.pack(padx=20, pady=20)
        text.insert("1.0", yaml.safe_dump(self.model.values[key], sort_keys=False))
        error = tk.StringVar(dialog)
        ttk.Label(dialog, textvariable=error, foreground="#ffb1bb", wraplength=560).pack(fill="x", padx=20, pady=(0, 10))

        def apply():
            try:
                value = normalize_value(self.model.types[key], yaml.safe_load(text.get("1.0", "end")))
            except Exception as exc:
                error.set(str(exc))
                return
            self._set(key, value)
            dialog.destroy()
            self.render()
        self.button(dialog, "Apply", apply, primary=True).pack(pady=(0, 20))

    def _export_text(self):
        self._commit_ranges()
        try:
            return self.model.export_text()
        except Exception as exc:
            self._show_review(self.model.draft_text(), exc)
            return None

    def _show_review(self, content, error=None):
        from .yaml_review import YamlReview
        if self.review is not None:
            self.review.close()
        self.review = YamlReview(self, content, error)

    def preview(self):
        content = self._export_text()
        if content is None:
            return
        self._show_review(content)

    def export_file(self):
        content = self._export_text()
        if content is None:
            return
        filename = re.sub(r'[^\w .-]', "_", self.model.name).strip(" .") or "Player"
        path = filedialog.asksaveasfilename(parent=self.root, title="Export KH3 player YAML",
                                          initialfile=f"{filename}.yaml", defaultextension=".yaml",
                                          filetypes=[("YAML", "*.yaml *.yml")])
        if not path:
            return
        try:
            Path(path).write_text(content, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Could not save YAML", str(exc), parent=self.root)
            return
        self.status.set(f"Saved {Path(path).name}")

    def _refresh_presets(self):
        try:
            self.preset_picker.configure(values=self.presets.names())
        except OSError as exc:
            self.status.set(f"Could not list saved settings: {exc}")

    def save_preset(self, *, as_new=False):
        self._commit_ranges()
        try:
            self.model.validated_values()
        except Exception as exc:
            self._show_review(self.model.draft_text(), exc)
            return
        name = None if as_new else self.active_preset
        if name is None:
            name = simpledialog.askstring("Save settings", "Preset name", parent=self.root)
        if name is None:
            return
        try:
            if as_new or self.active_preset is None:
                if name.strip().casefold() in {item.casefold() for item in self.presets.names()}:
                    raise ValueError("That preset already exists. Select it and use Save to update it, or choose another name.")
            saved_name = self.presets.save(name, self.model)
        except Exception as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self.root)
            return
        self.active_preset = saved_name
        self.delete_preset_button.configure(state="normal")
        self.preset_name.set(saved_name)
        self._refresh_presets()
        self.status.set(f"Saved {saved_name}")

    def load_preset(self, _=None):
        name = self.preset_name.get()
        try:
            candidate = self.presets.load(name)
        except Exception as exc:
            self.preset_name.set(self.active_preset or "Saved settings…")
            try:
                content = self.presets._path(name).read_text(encoding="utf-8-sig")
            except (OSError, ValueError):
                self.status.set(f"Could not load settings: {exc}")
                return
            self._show_review(content, exc)
            return
        candidate.name = self.name.get()
        self.active_preset = name
        self.delete_preset_button.configure(state="normal")
        self._replace_model(candidate)
        self.status.set(f"Loaded {name}")

    def delete_preset(self):
        name = self.active_preset
        if name is None:
            return
        try:
            self.presets.delete(name)
        except Exception as exc:
            messagebox.showerror("Could not delete settings", str(exc), parent=self.root)
            return
        self.active_preset = None
        self.delete_preset_button.configure(state="disabled")
        self.preset_name.set("Saved settings…")
        self._refresh_presets()
        self.status.set(f"Deleted {name}")

    def _replace_model(self, candidate):
        self.model = candidate
        self.dirty_rows.update(self.rows)
        self.name.set(candidate.name)
        self.render()

    def import_file(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import KH3 player YAML",
                                         filetypes=[("YAML", "*.yaml *.yml")])
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            self.status.set(f"Could not read YAML: {exc}")
            return
        try:
            candidate = PlayerOptions.from_text(content)
        except Exception as exc:
            self._show_review(content, exc)
            return
        self.active_preset = None
        self.delete_preset_button.configure(state="disabled")
        self.preset_name.set("Saved settings…")
        self._replace_model(candidate)
        self.status.set(f"Loaded {Path(path).name}")

    def reset(self):
        self.active_preset = None
        self.delete_preset_button.configure(state="disabled")
        self.preset_name.set("Saved settings…")
        self.model = PlayerOptions()
        self.model.name = ""
        self.dirty_rows.update(self.rows)
        self.name.set(self.model.name)
        self.changed_only.set(False)
        self.show_group(next(iter(self.model.groups)))
        self.status.set("Restored default settings.")

    def close(self):
        if self.pending_search is not None:
            self.root.after_cancel(self.pending_search)
        self.theme.close()
        self.root.destroy()


def launch(*args):
    app = YamlCreator()
    app.root.mainloop()
