"""KH3 debug view; gameplay and file policy remain in client services."""

from tkinter import ttk

from .view import View
from .widgets import Tooltip


class DebugView(View):
    def __init__(self, app, frame):
        super().__init__(app, frame)
        self.labels = {}
        for key, title in (
            ("slots", "Saves"),
            ("highest_ap_index", "Highest AP Index"),
            ("item_counts", "Received / Pending / Applied"),
            ("special_battle_mode", "Special Battle"),
        ):
            row = self.row()
            ttk.Label(row, text=title, width=30).pack(side="left", pady=4)
            if key == "slots":
                self.slots = ttk.Frame(row)
                self.slots.pack(side="left")
            else:
                self.labels[key] = ttk.Label(row, text="-")
                self.labels[key].pack(side="left", fill="x", expand=key != "item_counts")
                if key == "item_counts":
                    self.counts_tooltip = Tooltip(self.labels[key],
                        "Left to right: Received / Pending / Applied.\n"
                        "Received: items received from Archipelago.\n"
                        "Pending: items waiting to be applied in KH3.\n"
                        "Applied: items recorded as applied by KH3.")
        self.slot_signature = None

    async def refresh(self):
        snapshot = self.ctx._get_status_snapshot_service().build_snapshot()
        for key, label in self.labels.items():
            label.configure(text=snapshot.value_for(key))
        signature = tuple((s.text, s.is_current, s.is_last_loaded, s.tooltip) for s in snapshot.replay_slots)
        if signature != self.slot_signature:
            self.slot_signature = signature
            for child in self.slots.winfo_children():
                child.destroy()
            for text, current, loaded, tooltip in signature:
                style = " ".join(s for s, active in (("bold", current), ("italic", loaded)) if active) or "normal"
                label = ttk.Label(self.slots, text=text, font=(self.app.theme.family, 12, style))
                label.pack(side="left", padx=6)
                label.tooltip = Tooltip(label, tooltip)

