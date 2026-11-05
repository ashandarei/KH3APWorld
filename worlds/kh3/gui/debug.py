"""KH3 debug view; gameplay and file policy remain in client services."""

import asyncio
from tkinter import ttk

from .view import View
from .widgets import Tooltip


class DebugView(View):
    def __init__(self, app, frame):
        super().__init__(app, frame)
        self.read_button = self.button(self.row(), "Read UObjects", lambda: app.spawn(self.read_uobjects()))
        self.uobjects = ttk.Label(frame, text="UObjects: current / maximum. Click Read UObjects for a snapshot.")
        self.uobjects.pack(anchor="w")
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x")
        self.labels = {}
        for key, title in (
            ("last_ingredient_pickup", "Last Ingredient Spot"), ("slots", "Saves"),
            ("highest_ap_index", "Highest AP Index"), ("checks", "Checks"),
            ("item_counts", "Received / Pending / Applied"), ("current_map", "Current Map"),
            ("map_safe", "Map Safe?"), ("special_battle_mode", "Special Battle"),
            ("sora_state", "Sora State"),
        ):
            row = self.row(footer)
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
            if key == "last_ingredient_pickup":
                self.labels[key].configure(wraplength=780, justify="left")
                self.button(row, "Copy ID", self.copy_spot)
        self.slot_signature = None

    def copy_spot(self):
        spot = self.labels["last_ingredient_pickup"].cget("text").split(" | ", 1)[0]
        if spot != "-":
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(spot)

    async def read_uobjects(self):
        from ..bridge_support.files import atomic_write_json
        from ..platform_support.uobject_pool import format_uobject_snapshot, snapshot_uobject_pool
        if self.busy:
            return
        self.read_button.configure(state="disabled")
        root, bridge = self.ctx.kh3_dir, self.ctx.bridge_dir
        try:
            result = await self.work(lambda: snapshot_uobject_pool(root))
            if result is None:
                return
            if result and bridge:
                await asyncio.to_thread(atomic_write_json, bridge / "uobject_pool_snapshot.json", result)
            if not self.app.closed:
                self.uobjects.configure(text=format_uobject_snapshot(result) if
                                        (root, bridge) == (self.ctx.kh3_dir, self.ctx.bridge_dir) else
                                        "Selection changed; click Read UObjects again.")
        finally:
            if not self.app.closed:
                self.read_button.configure(state="normal")

    async def refresh(self):
        snapshot, slots = self.ctx._get_client_status_presenter().build_status_snapshot_with_replay_slots()
        for key, label in self.labels.items():
            label.configure(text=snapshot.value_for(key))
        signature = tuple((s.text, s.is_current, s.is_last_loaded, s.tooltip) for s in slots)
        if signature != self.slot_signature:
            self.slot_signature = signature
            for child in self.slots.winfo_children():
                child.destroy()
            for text, current, loaded, tooltip in signature:
                style = " ".join(s for s, active in (("bold", current), ("italic", loaded)) if active) or "normal"
                label = ttk.Label(self.slots, text=text, font=(self.app.theme.family, 12, style))
                label.pack(side="left", padx=6)
                label.tooltip = Tooltip(label, tooltip)

