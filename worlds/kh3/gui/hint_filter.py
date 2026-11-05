"""Pick exact values from a hint column without typing a search query."""
import tkinter as tk
from tkinter import ttk

from .theme import PANEL, SUBTLE, TEXT
from .widgets import Modal


class HintFilterDialog(Modal):
    def __init__(self, root, title, values, selected, resolve):
        super().__init__(root, title)
        self.values = values
        ttk.Label(self.body, text="Click values to include. You can select more than one.",
                  style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        choices = ttk.Frame(self.body, style="Card.TFrame")
        choices.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            choices, selectmode="multiple", exportselection=False, height=min(12, max(3, len(values))),
            width=55, background=PANEL, foreground=TEXT, selectbackground=SUBTLE,
            selectforeground=TEXT, font="TkDefaultFont", activestyle="none", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(choices, command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(choices, orient="horizontal", command=self.listbox.xview)
        horizontal.pack(side="bottom", fill="x")
        self.listbox.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.listbox.pack(fill="both", expand=True)
        for index, value in enumerate(values):
            self.listbox.insert("end", value)
            if value in selected:
                self.listbox.selection_set(index)
        buttons = ttk.Frame(self.body, style="Card.TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Select All", style="Card.TButton",
                   command=lambda: self.listbox.selection_set(0, "end")).pack(side="left")
        ttk.Button(buttons, text="Select None", style="Card.TButton",
                   command=lambda: self.listbox.selection_clear(0, "end")).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cancel", style="Card.TButton", command=lambda: resolve(False)).pack(side="right")
        ttk.Button(buttons, text="Apply Filter", style="Card.Primary.TButton",
                   command=lambda: resolve(True)).pack(side="right", padx=6)

    def selected_values(self):
        return [self.values[index] for index in self.listbox.curselection()]
