"""KH3 config view; gameplay and file policy remain in client services."""

import asyncio
import logging
import os
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import Utils

from .view import View
from .widgets import ContextMenu, Dropdown, Entry, Modal, Switch, Tooltip


class ConfigView(View):
    def __init__(self, app, frame):
        # Path settings must not push the status rows off the bottom on smaller
        # windows or higher DPI displays.
        canvas = self.canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.pack(side="left", fill="both", expand=True)

        def update_scrollbar(first, last):
            scrollbar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                scrollbar.pack_forget()
            elif not scrollbar.winfo_manager():
                scrollbar.pack(side="right", fill="y", before=canvas)

        canvas.configure(yscrollcommand=update_scrollbar)
        frame = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        super().__init__(app, frame)
        self.paths = {}
        self.detect_buttons = {}
        self.mod_buttons = {}
        self.mod_actions = {}
        self.mod_button_revision = 0
        self.mod_buttons_checked_revision = -1
        self.source_menu = ContextMenu(app.root, app.theme.family)
        frame.bind("<Unmap>", lambda _: self.source_menu.unpost(), add="+")
        from ..client_support.custom_music import read_directory
        values = {"KH3 Dir": str(self.ctx.kh3_dir), "Save Dir": str(self.ctx.display_kh3_save_dir()),
                  "MoM Dir": Utils.persistent_load().get("kh3_client", {}).get("mom_dir", ""),
                  "KH0.2 Dir": Utils.persistent_load().get("kh3_client", {}).get("kh02_dir", ""),
                  "Music Dir": read_directory()}
        for name, value in values.items():
            row = self.row()
            ttk.Label(row, text=name, width=12).pack(side="left")
            variable = tk.StringVar(value=value)
            self.paths[name] = variable
            entry = (Entry(row, textvariable=variable, placeholder="For the custom Music Randomization in KH3 (.ogg/.wav)")
                     if name == "Music Dir" else ttk.Entry(row, textvariable=variable))
            entry.pack(side="left", fill="x", expand=True, padx=6)
            entry.bind("<Return>", lambda _, key=name: self.app.spawn(self.apply(key)))
            detect = self.button(row, "Default" if name == "Music Dir" else "",
                                 lambda key=name: self.app.spawn(self.detect(key)),
                                 icon=None if name == "Music Dir" else "detect",
                                 tooltip="Use the mod's default custom_music folder." if name == "Music Dir" else
                                 "Auto-detect " + name + (". Right-click to choose Steam or Epic Games Store." if name in ("KH3 Dir", "KH0.2 Dir") else "."))
            self.detect_buttons[name] = detect
            if name in ("KH3 Dir", "KH0.2 Dir"):
                detect.bind("<Button-3>", lambda event, key=name: self.detect_menu(event, key))
            self.button(row, "", lambda key=name: self.app.spawn(self.browse(key)), icon="more",
                        tooltip=f"Browse for {name}")
            if name in ("KH0.2 Dir", "MoM Dir"):
                self.mod_buttons[name] = self.button(
                    row, "Checking…", lambda key=name: app.spawn(self.toggle_mod(key)))
                self.mod_buttons[name].state(["disabled"])
                variable.trace_add("write", lambda *_: self.invalidate_mod_buttons())
        frame.bind("<Map>", lambda _: self.invalidate_mod_buttons(), add="+")
        row = self.row()
        self.patch_button = self.button(row, "Patch Game", lambda: app.spawn(self.patch()), primary=True)
        self.patch_tooltip = Tooltip(self.patch_button, "Checking the installed runtime…")
        self.button(row, "Export Debug Bundle", lambda: app.spawn(self.export_debug_bundle()),
                    tooltip="Choose debug bundle export options.")
        self.button(row, "Restore Vanilla KH3", lambda: app.spawn(self.context_action(self.ctx.uninstall_kh3_ap_files)))
        self.capture_dump_button = self.button(
            row, "Capture Crash Dump", lambda: app.spawn(self.capture_crash_dump()),
            tooltip="Leave KH3's fatal-error window open. Capture an unredacted dump for private support.")
        if os.name != "nt":
            self.capture_dump_button.state(["disabled"])
        self.junction = tk.BooleanVar(value=self.ctx.ignore_unverified_save_seed_warnings)
        Switch(frame, text="I am using a Windows junction/soft link for my save directory",
                        variable=self.junction, command=lambda: self.ctx.set_ignore_unverified_save_seed_warnings(
                            self.junction.get(), persist=True)).pack(anchor="w", pady=12)
        from ..client_support.fast_new_game import read_settings
        self.fast_new_game = tk.BooleanVar(value=read_settings()["enabled"])
        tweaks = self.row()
        Switch(tweaks, text="Fast New Game", variable=self.fast_new_game,
                        command=self.toggle_fast_new_game).pack(side="left", padx=(0, 12))
        self.button(tweaks, "Configure...", self.configure_fast_new_game)
        footer = ttk.Frame(frame)
        footer.pack(side="bottom", fill="x")
        self.labels = {}
        for key, title, action in (
            ("mods", "Mods Dir", self.ctx.open_kh3_mods_dir),
            ("save", "Save Dir", self.ctx.open_kh3_save_dir),
            ("mom", "MoM Mod", self.open_mom_logs),
            ("bridge", "Bridge Dir", self.ctx.open_bridge_dir),
        ):
            row = self.row(footer)
            ttk.Label(row, text=title, width=12).pack(side="left")
            if action:
                self.button(row, "", action, icon="folder", tooltip=f"Open {title}")
                if key == "save":
                    self.delete_saves_button = self.button(
                        row, "", lambda: app.spawn(self.delete_save_files()), icon="trash",
                        tooltip="Delete save files (confirmation required)")
            label = ttk.Label(row, text="Checking…")
            label.pack(side="left", fill="x", expand=True)
            self.labels[key] = label
        self.next_status = 0

        def scroll(event):
            if canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(-int(event.delta / 120), "units")

        def reveal(event):
            top = event.widget.winfo_rooty() - frame.winfo_rooty()
            bottom = top + event.widget.winfo_height()
            visible_top = canvas.canvasy(0)
            if top < visible_top:
                canvas.yview_moveto(top / max(1, frame.winfo_height()))
            elif bottom > visible_top + canvas.winfo_height():
                canvas.yview_moveto((bottom - canvas.winfo_height()) / max(1, frame.winfo_height()))

        def bind_scroll(widget):
            widget.bind("<MouseWheel>", scroll, add="+")
            widget.bind("<FocusIn>", reveal, add="+")
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(frame)
        canvas.bind("<MouseWheel>", scroll)

    async def delete_save_files(self):
        from ..client_support.save_file_policy import checked_root
        from ..client_support.save_management import delete_confirmed_save_files
        from ..platform_support.process_status import is_process_running
        if self.app.action_running or self.ctx.kh3_run_action_running:
            return
        self.app.action_running = True
        try:
            target = checked_root(self.ctx.bridge_save_dir)
            if await asyncio.to_thread(is_process_running, "KINGDOM HEARTS III.exe"):
                await self.app.form("Close KH3", "Close KH3 before deleting save files.")
                return
            accepted = await self.app.form(
                "Delete Save Files", f"Permanently delete recognized KH3 game and randomizer saves from:\n{target}\n\n"
                "This includes autosaves, manual saves, and game system data. Unrecognized files and folders, "
                "configuration files, ModLoader settings, and existing backups are kept.\n\n"
                "This cannot be undone. No new backup will be created.", submit="Delete Save Files")
            if accepted is None:
                return
            if self.ctx.kh3_run_action_running or checked_root(self.ctx.bridge_save_dir) != target:
                raise RuntimeError("The active save directory changed. Open Delete Save Files again.")
            if await asyncio.to_thread(is_process_running, "KINGDOM HEARTS III.exe"):
                raise RuntimeError("KH3 was started. Close it before deleting save files.")
            result = await asyncio.to_thread(delete_confirmed_save_files, target)
            message = f"Deleted {result['removed_files']} save files from {target}."
            if result["errors"]:
                message += "\nSome files could not be deleted:\n" + "\n".join(result["errors"])
            logging.getLogger("Client").info(message)
            await self.app.form("Save File Deletion", message)
        except Exception as exc:
            logging.getLogger("Client").error("Could not delete save files: %s", exc)
            await self.app.form("Could Not Delete Save Files", str(exc))
        finally:
            self.app.action_running = False
            self.app.next_refresh = 0

    def toggle_fast_new_game(self):
        from ..client_support.fast_new_game import read_settings, save_settings
        if self.fast_new_game.get():
            self.configure_fast_new_game(enable_on_save=True)
        else:
            settings = read_settings()
            settings["enabled"] = False
            try:
                save_settings(settings, self.ctx.bridge_dir)
            except Exception:
                self.fast_new_game.set(read_settings()["enabled"])
                logging.getLogger("Client").exception("Could not save Fast New Game settings")

    def configure_fast_new_game(self, *, enable_on_save=False):
        from ..client_support.fast_new_game import (
            DIFFICULTIES,
            KEYBOARD_LAYOUTS,
            TEXT_LANGUAGES,
            VOICE_LANGUAGES,
            read_settings,
            save_settings,
        )
        existing = getattr(self, "fast_new_game_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        settings = read_settings()
        dialog = self.fast_new_game_dialog = tk.Toplevel(self.app.root)
        dialog.title("Fast New Game")
        dialog.transient(self.app.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Choose the settings to confirm automatically at startup.",
                  wraplength=420).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        fields = {}
        for row, (key, label, choices) in enumerate((
            ("text", "Text language", TEXT_LANGUAGES),
            ("voice", "Spoken language", VOICE_LANGUAGES),
            ("keyboard", "Keyboard layout", KEYBOARD_LAYOUTS),
        ), 1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=6)
            variable = tk.StringVar(value=next(name for name, value in choices.items() if value == settings[key]))
            Dropdown(body, textvariable=variable, values=list(choices), family=self.app.theme.family,
                           width=24).grid(row=row, column=1, sticky="ew", pady=6)
            fields[key] = variable, choices
        direct = tk.BooleanVar(value=settings["direct_new_game"])
        Switch(body, text="Go directly to New Game", variable=direct).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 6))
        ttk.Label(body, text="Difficulty (optional)").grid(row=5, column=0, sticky="w", padx=(0, 18), pady=6)
        difficulty = tk.StringVar(value=next(name for name, value in DIFFICULTIES.items()
                                             if value == settings["difficulty"]))
        Dropdown(body, textvariable=difficulty, values=list(DIFFICULTIES),
                       family=self.app.theme.family, width=24).grid(row=5, column=1, sticky="ew", pady=6)
        error = tk.StringVar()
        ttk.Label(body, textvariable=error, wraplength=460).grid(row=6, column=0, columnspan=2, sticky="w")

        def close():
            self.fast_new_game.set(read_settings()["enabled"])
            dialog.grab_release()
            dialog.destroy()
            self.fast_new_game_dialog = None

        def save():
            updated = {key: choices[variable.get()] for key, (variable, choices) in fields.items()}
            updated["enabled"] = enable_on_save or settings["enabled"]
            updated["direct_new_game"] = direct.get()
            updated["difficulty"] = DIFFICULTIES[difficulty.get()]
            try:
                save_settings(updated, self.ctx.bridge_dir, self.ctx.kh3_dir)
            except Exception as exc:
                error.set(f"Could not save settings: {exc}")
                return
            close()

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=close).pack(side="left", padx=6)
        ttk.Button(buttons, text="Save", command=save, style="Primary.TButton").pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _: close())
        dialog.grab_set()
        dialog.focus_set()

    async def browse(self, key):
        if self.app.action_running or self.ctx.kh3_run_action_running:
            return
        selected = Utils.open_directory(f"Select {key}", self.paths[key].get())
        if selected:
            self.paths[key].set(selected)
            await self.apply(key)

    async def apply(self, key, *, offer_patch=True, auto_save=False):
        if self.app.action_running or self.ctx.kh3_run_action_running:
            return False
        value = self.paths[key].get().strip()
        if not value and key != "Music Dir":
            raise ValueError(f"{key} cannot be blank.")
        if key == "KH3 Dir":
            self.ctx.set_kh3_dir(value, persist=True)
            if auto_save:
                self.ctx.clear_kh3_save_dir_override(persist=True)
            self.ctx.configure_probe_for_current_bridge()
            self.paths["Save Dir"].set(str(self.ctx.display_kh3_save_dir()))
            if offer_patch and await self.app.prompt_patch_game_confirmation():
                await self.patch()
        elif key == "Save Dir":
            self.ctx.set_kh3_save_dir(value, persist=True)
        elif key == "KH0.2 Dir":
            from ..kh02.installation import save_selection
            install = await asyncio.to_thread(save_selection, Path(value), self.ctx.kh3_dir)
            self.paths[key].set(str(install.root))
            logging.getLogger("Client").info("KH0.2 source: %s (%s). Applies on the next KH3 launch.",
                                              install.root, install.platform.upper())
        elif key == "Music Dir":
            from ..client_support.custom_music import save_directory
            value = save_directory(value, self.ctx.kh3_dir)
            self.paths[key].set(value)
            logging.getLogger("Client").info("Custom music folder: %s. Changes apply the next time KH3 launches.",
                                              value or "default custom_music folder")
        else:
            from ..client_support.mom_installation import validate_game
            Utils.persistent_store("kh3_client", "mom_dir", str(validate_game(Path(value))))
        self.next_status = 0
        return True

    def detect_menu(self, event, key="KH3 Dir"):
        menu = self.source_menu
        menu.clear()
        busy = self.app.action_running or self.ctx.kh3_run_action_running
        for title, source in (("Detect Steam installation", "steam"),
                              ("Detect Epic Games Store installation", "egs")):
            menu.add_command(label=title, state="disabled" if busy else "normal",
                             command=lambda value=source: self.app.spawn(self.detect(key, value)))
        menu.show(event.x_root, event.y_root)
        return "break"

    async def detect(self, key, source="auto"):
        if self.app.action_running or self.ctx.kh3_run_action_running:
            return
        if key == "Music Dir":
            self.paths[key].set("")
            await self.apply(key)
        elif key == "Save Dir":
            self.ctx.clear_kh3_save_dir_override(persist=True)
            self.paths[key].set(str(self.ctx.display_kh3_save_dir()))
        elif key == "MoM Dir":
            from ..client_support.mom_installation import discover_installs
            choices = await self.work(discover_installs)
            if choices is None:
                return
            if len(choices) != 1:
                raise ValueError("Browse to a MoM directory; automatic detection did not find exactly one installation.")
            self.paths[key].set(str(choices[0][0]))
            await self.apply(key)
        elif key == "KH0.2 Dir":
            from ..kh02.installation import discover_installs
            choices = await self.work(lambda: discover_installs(source=source))
            if not choices:
                raise ValueError("No supported KH0.2 installation found. Browse to the KH2.8 or KH0.2 folder.")
            self.paths[key].set(str(choices[0].root))
            await self.apply(key)
        else:
            from ..platform_support.game_discovery import detect_kh3_install_dir
            choice = await self.work(lambda: detect_kh3_install_dir(source=source))
            if choice:
                self.paths[key].set(str(choice))
                await self.apply(key, auto_save=True)
            else:
                logging.getLogger("Client").warning("Could not detect a KH3 installation from %s. Use Browse to select it.", source)
        self.next_status = 0

    async def patch(self):
        started = False
        try:
            if not await self.apply("KH3 Dir", offer_patch=False):
                return

            started = True
            self.patch_button.configure(text="Patching…")
            self.patch_button.state(["disabled"])
            # Installer guards and handled failures report through the client
            # log. Keep those messages visible while the worker runs.
            self.app.tabs.select(self.app.pages["Archipelago"])
            logging.getLogger("Client").info("Patching KH3 in: %s", self.ctx.kh3_dir)
            await self.work(self.ctx.install_kh3_runtime_files_in_subprocess)
        except Exception as exc:
            logging.getLogger("Client").exception("Could not patch KH3")
            if not self.app.closed:
                await self.app.form("Could Not Patch KH3", str(exc))
        finally:
            if started and not self.app.closed:
                self.patch_button.configure(text="Patch Game")
                self.patch_button.state(["!disabled"])
            self.next_status = 0

    async def context_action(self, action):
        if await self.apply("KH3 Dir", offer_patch=False):
            action()
            self.next_status = 0

    async def capture_crash_dump(self):
        if not await self.apply("KH3 Dir", offer_patch=False):
            return
        self.capture_dump_button.configure(text="Capturing…")
        self.capture_dump_button.state(["disabled"])
        try:
            await self.work(self.ctx.capture_crash_dump)
        except Exception as exc:
            logging.getLogger("Client").error("Could not capture KH3 crash dump: %s", exc)
            if not self.app.closed:
                await self.app.form("Could Not Capture Crash Dump", str(exc))
        finally:
            if not self.app.closed:
                self.capture_dump_button.configure(text="Capture Crash Dump")
                self.capture_dump_button.state(["!disabled"])
            self.next_status = 0

    async def debug_export_options(self):
        if self.app.closed:
            return None
        dialog = Modal(self.app.root, "")
        include_crash_dumps = tk.BooleanVar(master=dialog, value=False)
        ttk.Checkbutton(dialog.body, text="Include crash dumps from today", style="Card.TCheckbutton",
                        variable=include_crash_dumps).pack(anchor="w")
        future = asyncio.get_running_loop().create_future()
        self.app.dialogs[dialog] = future

        def resolve(accepted):
            if not future.done():
                future.set_result(include_crash_dumps.get() if accepted else None)

        buttons = ttk.Frame(dialog.body, style="Card.TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Close", style="Card.TButton", command=lambda: resolve(False)).pack(side="right")
        ttk.Button(buttons, text="Export", style="Card.Primary.TButton",
                   command=lambda: resolve(True)).pack(side="right", padx=8)
        dialog.show(resolve)
        try:
            return await future
        finally:
            self.app.dialogs.pop(dialog, None)
            if not self.app.closed:
                dialog.close()

    async def export_debug_bundle(self):
        include_crash_dumps = await self.debug_export_options()
        if include_crash_dumps is None or self.app.closed:
            return
        if not await self.apply("KH3 Dir", offer_patch=False):
            return
        try:
            await self.work(lambda: self.ctx.export_debug_bundle(include_crash_dumps=include_crash_dumps))
        except Exception as exc:
            logging.getLogger("Client").error("Could not export KH3 debug bundle: %s", exc)
            if not self.app.closed:
                await self.app.form("Could Not Export Debug Bundle", str(exc))
        finally:
            self.next_status = 0

    def invalidate_mod_buttons(self):
        self.mod_button_revision += 1
        for button in self.mod_buttons.values():
            button.configure(text="Checking…")
            button.state(["disabled"])
        self.app.next_refresh = 0

    @staticmethod
    def installed_mod_action(key, value):
        if not value:
            return None
        if key == "KH0.2 Dir":
            from ..kh02.installation import inspect_install
            from ..kh02.runtime_install import MOD, STATE, safe_path
            root = inspect_install(Path(value)).executable.parent
            installed = safe_path(root, MOD).is_file() or safe_path(root, f"{STATE}/installation.json").is_file()
        else:
            from ..client_support.mom_installation import MOD_PATH, STATE_DIR, safe_path, validate_game
            root = validate_game(Path(value))
            installed = safe_path(root, MOD_PATH).is_file() or safe_path(root, f"{STATE_DIR}/installation.json").is_file()
        return "uninstall" if installed else "install"

    async def refresh_mod_buttons(self):
        revision = self.mod_button_revision
        if revision == self.mod_buttons_checked_revision:
            return
        for key, button in self.mod_buttons.items():
            value = self.paths[key].get().strip()
            try:
                action = await asyncio.to_thread(self.installed_mod_action, key, value)
            except (OSError, ValueError):
                action = None
            if self.app.closed or revision != self.mod_button_revision:
                return
            self.mod_actions[key] = action
            button.configure(text="Uninstall Mod" if action == "uninstall" else "Install Mod")
            button.state(["!disabled"] if action else ["disabled"])
        self.mod_buttons_checked_revision = revision

    async def toggle_mod(self, key):
        if self.app.action_running or self.ctx.kh3_run_action_running:
            return
        await self.refresh_mod_buttons()
        action = self.mod_actions.get(key)
        if action is None:
            return
        # Recheck on click so an external install cannot turn a stale Install
        # button into an unexpected uninstall (or vice versa).
        revision = self.mod_button_revision
        current = await asyncio.to_thread(self.installed_mod_action, key, self.paths[key].get().strip())
        if self.app.closed or revision != self.mod_button_revision:
            return
        if current != action:
            self.invalidate_mod_buttons()
            await self.refresh_mod_buttons()
            return
        try:
            if key == "KH0.2 Dir":
                await self.kh02_action(action)
            else:
                await self.mom_action(action)
        finally:
            if not self.app.closed:
                self.invalidate_mod_buttons()
                await self.refresh_mod_buttons()

    async def kh02_action(self, action):
        from ..kh02 import runtime_install
        if action not in ("install", "uninstall"):
            raise ValueError("Unknown KH0.2 DLL action.")
        if not await self.apply("KH0.2 Dir"):
            return
        root = Path(self.paths["KH0.2 Dir"].get())
        result = await self.work(lambda: getattr(runtime_install, action)(root))
        if result:
            self.app.messages.put(result)
        self.next_status = 0

    async def mom_action(self, action):
        from ..client_support import mom_installation
        if not await self.apply("MoM Dir"):
            return
        root = Path(self.paths["MoM Dir"].get())
        if action == "install":
            if not await self.apply("KH3 Dir", offer_patch=False):
                return
            kh3_root = Path(self.ctx.kh3_dir)
            result = await self.work(lambda: mom_installation.install_integration(root, kh3_root, self.app.messages.put))
        else:
            result = await self.work(lambda: getattr(mom_installation, action)(root, self.app.messages.put))
        if result:
            self.app.messages.put(result)
        self.next_status = 0

    def open_mom_logs(self):
        from ..platform_support.path_opening import open_path
        value = self.paths["MoM Dir"].get().strip()
        if value:
            open_path(Path(value) / "MelonLoader", description="MoM logs")

    async def refresh(self):
        self.labels["mods"].configure(text=str(self.ctx.kh3_mods_dir))
        self.labels["save"].configure(text=str(self.ctx.display_kh3_save_dir()))
        self.labels["bridge"].configure(text=str(self.ctx.bridge_dir or "-"))
        if self.busy:
            return
        await self.refresh_mod_buttons()
        if time.monotonic() < self.next_status:
            return
        selection = (self.ctx.kh3_dir, self.paths["MoM Dir"].get().strip())
        presenter = self.ctx._get_client_status_presenter()
        summary = await asyncio.to_thread(presenter.build_installed_runtime_summary, now=time.monotonic())
        if self.app.closed or selection != (self.ctx.kh3_dir, self.paths["MoM Dir"].get().strip()):
            return
        self.app.update_title(summary.ue4ss_status, summary.goa_version)
        self.patch_button.configure(style="Primary.TButton" if summary.installed else "Alert.TButton")
        from ..platform_support.game_discovery import is_likely_steam_kh3_root_dir
        self.patch_tooltip.text = summary.patch_tooltip
        if is_likely_steam_kh3_root_dir(Path(self.ctx.kh3_dir)):
            self.patch_tooltip.text += "\n\nSteam: Patch Game also disables Steam Cloud for KINGDOM HEARTS III only."
        value = self.paths["MoM Dir"].get().strip()
        message = "Select a MoM directory."
        if value:
            from ..client_support.mom_installation import status
            result = await asyncio.to_thread(status, Path(value))
            message = result.message
        if not self.app.closed and selection == (self.ctx.kh3_dir, self.paths["MoM Dir"].get().strip()):
            self.labels["mom"].configure(text=message)
            self.next_status = time.monotonic() + 3

