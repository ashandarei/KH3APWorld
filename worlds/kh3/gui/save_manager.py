"""KH3 save manager view; gameplay and file policy remain in client services."""

import asyncio
import tkinter as tk
from tkinter import ttk

import Utils

from .view import View
from .widgets import ContextMenu, Switch, Tooltip


class SaveManagerView(View):
    def __init__(self, app, frame):
        super().__init__(app, frame)
        row = self.row()
        self.button(row, "Import Seed Pak", self.import_seed)
        self.button(row, "Open Run Storage", self.ctx.open_kh3_run_library)
        self.button(row, "Delete All Except Active", lambda: app.spawn(self.delete_others()))
        self.auto_delete = tk.BooleanVar(value=self.ctx.auto_delete_old_kh3_runs)
        Switch(frame, text="Automatically delete old seeds when starting a new run", variable=self.auto_delete,
                        command=lambda: self.ctx.set_auto_delete_old_kh3_runs(self.auto_delete.get())).pack(anchor="w", pady=8)
        self.tree = ttk.Treeview(frame, columns=("slot", "seed", "server", "status"), show="headings", selectmode="extended")
        from .theme import RUN_PLATFORM_COLORS
        for platform, color in RUN_PLATFORM_COLORS.items():
            self.tree.tag_configure(f"platform:{platform}", background=color)
        self.hover_run_id = None
        self.platform_tooltip = Tooltip(self.tree, self.platform_tooltip_text, bind_hover=False, at_pointer=True)
        self.tree.bind("<Motion>", self.hover_platform)
        for event in ("<Leave>", "<ButtonPress>", "<MouseWheel>", "<Unmap>"):
            self.tree.bind(event, self.hide_platform_tooltip, add="+")
        for key in self.tree["columns"]:
            self.tree.heading(key, text=key.title())
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.context_menu = ContextMenu(app.root, app.theme.family)
        self.context_run_id = None
        self.context_delete_run_ids = ()
        self.game_running = True  # Refreshed off the UI thread before enabling active deletion.
        frame.bind("<Unmap>", lambda _: self.context_menu.unpost(), add="+")
        row = self.row()
        self.button(row, "Activate", self.activate)
        self.button(row, "Edit", lambda: app.spawn(self.edit()))
        self.button(row, "Room", self.room)
        self.delete_button = self.button(row, "Delete", lambda: app.spawn(self.delete()))
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.update_delete_button())
        self.runs = {}

    def platform_tooltip_text(self):
        run = self.runs.get(self.hover_run_id)
        if run is None:
            return ""
        return {"steam": "Steam", "egs": "Epic Games Store"}.get(
            getattr(run, "platform", ""), "Legacy run — platform not recorded",
        )

    def hover_platform(self, event):
        run_id = self.tree.identify_row(event.y)
        if run_id != self.hover_run_id:
            self.platform_tooltip.hide()
            self.hover_run_id = run_id
            if run_id:
                self.platform_tooltip.schedule()

    def hide_platform_tooltip(self, *_):
        self.hover_run_id = None
        self.platform_tooltip.hide()

    def selected(self, run_id=None):
        if run_id is not None:
            return self.runs.get(run_id)
        selection = self.tree.selection()
        return self.runs.get(selection[0]) if selection else None

    def can_delete_runs(self, run_ids):
        return bool(run_ids) and all(self.can_delete(self.runs.get(run_id)) for run_id in run_ids)

    def needs_activation(self, run):
        # Installed pak identity alone cannot establish ownership of the saves.
        # Keep the normal activation path available after the seed guard rejects
        # an externally copied pak, even if inventory calls that run active.
        return not run.active or bool(getattr(self.ctx, "save_seed_mismatch_active", False))

    def show_context_menu(self, event):
        run_id = self.tree.identify_row(event.y)
        run = self.runs.get(run_id)
        if run is None or self.tree.identify_region(event.x, event.y) not in {"cell", "tree"}:
            return "break"
        if run_id not in self.tree.selection():
            self.tree.selection_set(run_id)
        self.tree.focus(run_id)
        menu = self.context_menu
        menu.clear()
        self.context_run_id = run_id
        delete_run_ids = tuple(self.tree.selection())
        self.context_delete_run_ids = delete_run_ids
        busy = self.app.action_running or self.ctx.kh3_run_action_running
        menu.add_command(label="Activate" if self.needs_activation(run) else "Activated",
                         state="disabled" if busy or not self.needs_activation(run) or (run.managed and not run.pak_available) else "normal",
                         command=lambda: self.activate(run_id))
        menu.add_command(label="Edit", state="disabled" if busy else "normal",
                         command=lambda: self.app.spawn(self.edit(run_id)))
        menu.add_command(label="Room", state="disabled" if busy or not (run.room_url or "").strip() else "normal",
                         command=lambda: self.room(run_id))
        menu.add_command(label="Delete", state="disabled" if busy or not self.can_delete_runs(delete_run_ids) else "normal",
                         command=lambda: self.app.spawn(self.delete(run_ids=delete_run_ids)))
        menu.show(event.x_root, event.y_root)
        return "break"

    def can_delete(self, run):
        return bool(run and not (run.active and self.ctx._is_ap_connected())
                    and not ((run.active or run.installed) and self.game_running))

    def update_delete_button(self):
        enabled = not (self.app.action_running or self.ctx.kh3_run_action_running) and self.can_delete_runs(self.tree.selection())
        self.delete_button.state(["!disabled" if enabled else "disabled"])

    def import_seed(self):
        if self.ctx.kh3_run_action_running:
            return
        path = Utils.open_filename("Import KH3 AP Seed", (("KH3 AP seed", ("*.pak", "*.apkh3", "*.zip")),))
        if path:
            self.ctx.request_import_kh3_run_pak(path)

    def activate(self, run_id=None):
        run = self.selected(run_id)
        if run and not self.ctx.kh3_run_action_running and not self.app.action_running:
            if not run.managed:
                self.app.spawn(self.edit(run.run_id))
            elif self.needs_activation(run) and run.pak_available:
                self.ctx.request_activate_kh3_run(run.run_id)

    async def edit(self, run_id=None):
        from ..client_support.run_manager import split_kh3_run_server_address
        run = self.selected(run_id)
        if run is None or self.ctx.kh3_run_action_running or self.app.action_running:
            return
        server, port = split_kh3_run_server_address(run.server_address)
        values = await self.app.prompt_run_connection_profile(slot_name=run.effective_slot_name, server=server,
                    port=port, room_url=run.room_url, importing=not run.managed)
        if values and not self.ctx.kh3_run_action_running:
            if run.managed:
                self.ctx.request_update_kh3_run_connection_profile(run.run_id, **values, show_activity=run.active)
            else:
                self.ctx.request_import_kh3_run_pak(run.installed_pak_path, **values)

    def room(self, run_id=None):
        run = self.selected(run_id)
        if run and run.room_url and not self.ctx.kh3_run_action_running and not self.app.action_running:
            self.ctx.open_kh3_room_page(run.room_url)

    async def delete(self, run_id=None, *, run_ids=None):
        if run_id is not None:
            run_ids = (run_id,)
        elif run_ids is None:
            run_ids = self.tree.selection()
        run_ids = tuple(dict.fromkeys(run_ids))
        if self.ctx.kh3_run_action_running or self.app.action_running or not self.can_delete_runs(run_ids):
            return
        from ..ExternalBridge import KH3_PROCESS_NAME
        from ..platform_support.process_status import is_process_running
        self.game_running = await asyncio.to_thread(is_process_running, KH3_PROCESS_NAME)
        if not self.can_delete_runs(run_ids):
            return
        if len(run_ids) == 1:
            title = "Delete Run"
            message = (f"Permanently delete {self.runs[run_ids[0]].slot_name}'s seed pak, AP saves, and "
                       "unshared bridge folder? Vanilla backups are kept.")
        else:
            title = "Delete Runs"
            message = (f"Permanently delete all {len(run_ids)} selected runs, including their seed paks, AP saves, "
                       "and unshared bridge folders? Vanilla backups are kept.")
        accepted = await self.app.form(title, message, submit="Delete")
        if (accepted is not None and not self.ctx.kh3_run_action_running and not self.app.action_running
                and self.can_delete_runs(run_ids)):
            if len(run_ids) == 1:
                self.ctx.request_delete_kh3_run(run_ids[0])
            else:
                self.ctx.request_delete_kh3_runs(run_ids)

    async def delete_others(self):
        if self.ctx.kh3_run_action_running:
            return
        runs = self.ctx.list_kh3_runs(force_refresh=True)
        active = [run.run_id for run in runs if run.active]
        current_platform = next((getattr(run, "platform", "") for run in runs if run.active), None)
        others = tuple(run.run_id for run in runs if not run.active and getattr(run, "platform", "") == current_platform)
        if len(active) != 1 or not others:
            return
        accepted = await self.app.form("Delete Other Runs", f"Permanently delete {len(others)} inactive runs, including "
                    "their AP saves and unshared bridge folders? The active run is kept.", submit="Delete")
        if accepted is not None:
            self.ctx.request_delete_inactive_kh3_runs(others, active[0])

    async def refresh(self):
        from ..ExternalBridge import KH3_PROCESS_NAME
        from ..platform_support.process_status import is_process_running
        self.game_running = await asyncio.to_thread(is_process_running, KH3_PROCESS_NAME)
        runs = self.ctx.list_kh3_runs()
        self.runs = {run.run_id: run for run in runs}
        for existing in self.tree.get_children():
            if existing not in self.runs:
                self.tree.delete(existing)
        for run in runs:
            tags = (f"platform:{getattr(run, 'platform', '')}",)
            values = (run.slot_name, run.seed_id, run.server_address,
                      "Needs activation" if run.active and self.needs_activation(run) else "Active" if run.active else
                      "Stored" if run.managed else "Unmanaged")
            if self.tree.exists(run.run_id):
                self.tree.item(run.run_id, values=values, tags=tags)
            else:
                self.tree.insert("", "end", iid=run.run_id, values=values, tags=tags)
        self.update_delete_button()
        if self.context_menu.winfo_ismapped():
            run = self.runs.get(self.context_run_id)
            if run is None:
                self.context_menu.unpost()
            else:
                busy = self.app.action_running or self.ctx.kh3_run_action_running
                self.context_menu.entryconfigure(0, label="Activate" if self.needs_activation(run) else "Activated",
                    state="disabled" if busy or not self.needs_activation(run) or (run.managed and not run.pak_available) else "normal")
                self.context_menu.entryconfigure(1, state="disabled" if busy else "normal")
                self.context_menu.entryconfigure(2, state="disabled" if busy or not (run.room_url or "").strip() else "normal")
                self.context_menu.entryconfigure(3, state="disabled" if busy or not self.can_delete_runs(self.context_delete_run_ids) else "normal")

