"""KH3 mods view; gameplay and file policy remain in client services."""

import tkinter as tk
from tkinter import ttk

from .view import View


class ModsView(View):
    def __init__(self, app, frame):
        super().__init__(app, frame)
        toolbar = self.row()
        ttk.Label(toolbar, text="Download, extract to Storage, then Restore all. Close KH3 to change mods.",
                  style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        self.button(toolbar, "Add Mod", lambda: app.spawn(self.add_mod()), primary=True)
        self.message = tk.StringVar()
        ttk.Label(frame, textvariable=self.message, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        viewport = ttk.Frame(frame)
        viewport.pack(fill="both", expand=True)
        canvas = self.canvas = tk.Canvas(viewport, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.catalog_key = None
        self.rows = {}
        self.file_statuses = {}

    def service(self):
        from ..client_support.compatible_mods import CompatibleModsService
        from ..ExternalBridge import KH3_PROCESS_NAME
        from ..platform_support.process_status import is_process_running
        return CompatibleModsService(self.ctx.kh3_mods_dir, game_running=lambda: is_process_running(KH3_PROCESS_NAME))

    async def add_mod(self):
        values = await self.app.form("Add Mod", "Enter the mod name and exact pak filename.",
                                    [("name", "Name", ""), ("pak_name", "Filename.pak", ""), ("url", "Download URL", "")])
        if values:
            try:
                mod = self.service().add_mod(**values)
                self.message.set(f"{mod.name} added. Extract {mod.pak_name} into Storage, then Restore all.")
                self.catalog_key = None
            except (OSError, ValueError) as exc:
                self.message.set(str(exc))

    def toggle(self, mod, active):
        if self.ctx.kh3_run_action_running or self.app.action_running:
            return
        try:
            self.service().set_group_active(mod, active=active)
            self.message.set(f"{mod.name}: {'restored with its required files' if active else 'returned to storage'}.")
        except (OSError, ValueError) as exc:
            self.message.set(str(exc))
        self.app.next_refresh = 0

    def storage(self, mod):
        from ..platform_support.path_opening import open_path
        open_path(self.service().storage_dir(mod), description="Mod storage", create_directory=True)

    def build_rows(self, mods):
        import webbrowser
        for child in self.content.winfo_children():
            child.destroy()
        self.rows.clear()
        self.file_statuses.clear()

        def action(row, column, text, callback, **kwargs):
            button = self.button(row, text, callback, pack=False, card=True, **kwargs)
            button.grid(row=0, column=column, padx=4, pady=6)
            return button

        for mod in mods:
            card = ttk.Frame(self.content, style="Card.TFrame", padding=12)
            card.pack(fill="x", pady=(0, 12))
            for part in (mod, *mod.dependencies):
                row = ttk.Frame(card, style="Card.TFrame")
                row.pack(fill="x")
                row.columnconfigure(0, weight=1)
                dependency = part is not mod
                title = ("Required: " if dependency else "") + ("Roxas Boss (Coliseum)" if part.id == "roxas-coliseum" else part.name)
                ttk.Label(row, text=title, style="Card.TLabel" if dependency else "CardTitle.TLabel").grid(
                    row=0, column=0, sticky="w", padx=(16 if dependency else 0, 8))
                status = ttk.Label(row, text="", style="Card.TLabel", width=17)
                status.grid(row=0, column=1, sticky="w", padx=6)
                details = (f"{part.name}\nAuthor: {part.author}\nWebsite version: {part.version}\n"
                           f"Download: {part.download_file or part.name}\nFile: {part.pak_name}")
                info = action(row, 2, "", lambda: None, icon="info", tooltip=details)
                if part.url:
                    action(row, 3, "Download", lambda m=part: webbrowser.open(m.url))
                action(row, 4, "", lambda m=part: self.storage(m), icon="folder", tooltip=f"Open {part.name} storage")
                if not dependency:
                    restore = action(row, 5, "Restore all", lambda m=mod: self.toggle(m, True), primary=True)
                    remove = action(row, 6, "Remove all", lambda m=mod: self.toggle(m, False))
                    self.rows[mod.id] = (status, restore, remove, info.tooltip, details)
                else:
                    self.file_statuses[part.id] = status
                    row.columnconfigure(5, minsize=220)
            ttk.Separator(card).pack(fill="x", pady=(6, 0))

        def bind_scroll(widget):
            widget.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"), add="+")
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(self.content)

    async def refresh(self):
        from ..client_support.compatible_mods import COMPATIBLE_MODS
        service = self.service()
        try:
            mods = service.catalog()
        except (OSError, ValueError) as exc:
            self.message.set(str(exc))
            mods = COMPATIBLE_MODS
        key = (self.ctx.kh3_mods_dir, mods)
        if key != self.catalog_key:
            self.catalog_key = key
            self.build_rows(mods)
        running = service.game_running()
        busy = self.ctx.kh3_run_action_running or self.app.action_running
        for mod in mods:
            status, restore, remove, tooltip, details = self.rows[mod.id]
            try:
                states = []
                for part in (mod, *mod.dependencies):
                    if not busy and not running:
                        service.normalize_installed(part)
                    part_state = service.state(part)
                    states.append(part_state)
                    if part.id in self.file_statuses:
                        self.file_statuses[part.id].configure(text="Conflict" if part_state.startswith("Conflict") else part_state)
                problem = service.group_problem(mod)
                complete = all(state == "Active" for state in states)
                caption = "Active" if complete else "Ready to restore"
                if problem:
                    caption = "Base pak missing" if states[0] == "Not installed" else "Missing files" if "Not installed" in states else "Needs attention"
                status.configure(text="KH3 running" if running else caption)
                tooltip.text = details + "\nRestore all installs this mod and its required files together. Remove all returns them to storage."
                if mod.id == "olympus-coliseum":
                    tooltip.text += "\nAP randomized cup rewards require the verified 1.4.2 pak. Website versions do not identify your installed version."
                if problem:
                    tooltip.text += "\n" + problem
                restore.state(["disabled" if running or busy or complete or problem else "!disabled"])
                remove.state(["disabled" if running or busy or "Active" not in states or any(s.startswith("Conflict") for s in states) else "!disabled"])
            except (OSError, ValueError) as exc:
                status.configure(text="Needs attention")
                tooltip.text = str(exc)
                restore.state(["disabled"])
                remove.state(["disabled"])

