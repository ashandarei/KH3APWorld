# SPDX-License-Identifier: GPL-3.0-only
"""Room-wide settings for one generation from the YAML creator."""

from tkinter import ttk

from ..yaml_generation import GenerationSettings


SPOILER = {0: "None", 1: "Item locations", 2: "Locations and playthrough", 3: "Locations, playthrough and paths"}
PERMISSION = {"disabled": "Disabled", "goal": "Allowed after goal", "enabled": "Always allowed"}
COMPLETION = {**PERMISSION, "auto": "Automatic on goal", "auto-enabled": "Automatic on goal + always allowed"}


class AdvancedSettings(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        defaults = GenerationSettings.from_installation()
        self.toggle = ttk.Button(self, text="▸ Advanced settings", style="Compact.TButton", command=self.expand)
        self.toggle.pack(anchor="w")
        self.panel = ttk.Frame(self, padding=(8, 6))
        self.panel.columnconfigure(1, weight=1)
        self.choices = {}
        ttk.Label(self.panel, text="Hint cost (%)").grid(row=0, column=0, sticky="w")
        self.hint_min = min(0, defaults.hint_cost)
        self.hint_max = max(100, defaults.hint_cost)
        self.hint_cost = ttk.Spinbox(self.panel, from_=self.hint_min, to=self.hint_max, width=6)
        self.hint_cost.set(defaults.hint_cost)
        self.hint_cost.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.hint_cost.bind("<FocusOut>", self._commit_hint_cost)
        self.hint_cost.bind("<Return>", self._commit_hint_cost)
        for row, (key, label, options) in enumerate((
            ("spoiler", "Spoiler detail", SPOILER),
            ("release_mode", "Release items to other players", COMPLETION),
            ("collect_mode", "Collect items from other players", COMPLETION),
            ("remaining_mode", "Show remaining items", PERMISSION),
        ), start=1):
            ttk.Label(self.panel, text=label).grid(row=row, column=0, sticky="w")
            default = getattr(defaults, key)
            if default not in options:
                options = {**options, default: str(default)}
            combo = ttk.Combobox(self.panel, state="readonly", values=tuple(options.values()), width=32)
            combo.set(options[default])
            combo.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=2)
            self.choices[key] = (combo, tuple(options))

    def _commit_hint_cost(self, _event=None):
        try:
            value = int(self.hint_cost.get())
        except ValueError:
            return None
        value = max(self.hint_min, min(self.hint_max, value))
        self.hint_cost.set(value)
        return value

    def expand(self):
        if self.panel.winfo_manager():
            self.panel.pack_forget()
            self.toggle.configure(text="▸ Advanced settings")
        else:
            self.panel.pack(fill="x")
            self.toggle.configure(text="▾ Advanced settings")

    def set_busy(self, busy):
        self.toggle.configure(state="disabled" if busy else "normal")
        self.hint_cost.configure(state="disabled" if busy else "normal")
        for combo, _ in self.choices.values():
            combo.configure(state="disabled" if busy else "readonly")

    def values(self):
        hint_cost = self._commit_hint_cost()
        if hint_cost is None:
            if not self.panel.winfo_manager():
                self.expand()
            self.hint_cost.focus_set()
            raise ValueError(f"Enter a whole number from {self.hint_min} to {self.hint_max} for hint cost.")
        return GenerationSettings(hint_cost=hint_cost,
                                  **{key: values[combo.current()] for key, (combo, values) in self.choices.items()})
