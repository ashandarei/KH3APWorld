"""Default KH3 Tk frontend using the existing context and asyncio lifecycle."""

from __future__ import annotations

import asyncio
import copy
import logging
import queue
import time
import tkinter as tk
from collections import deque
from tkinter import ttk

from NetUtils import RawJSONtoTextParser

from ..client_support.seed_paks import _installed_kh3_apworld_version
from .theme import Theme
from .widgets import Tabs
from .window import configure_window


class LogHandler(logging.Handler):
    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def emit(self, record):
        if not getattr(record, "skip_gui", False):
            self.messages.put(self.format(record))


class KH3Manager:
    """Keep Tk and context callbacks on the same asyncio/main thread."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.root = tk.Tk()
        self.theme = Theme(self.root)
        self.apworld_version = _installed_kh3_apworld_version()
        self.update_title("Checking…")
        configure_window(self.root)
        self.root.geometry("1100x760")
        self.root.minsize(980, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.stop)
        self.closed = False
        self.tasks = set()
        self.action_running = False
        self.dialogs = {}
        self.messagebox = None
        self.messages = queue.SimpleQueue()
        self.log_lines = deque(maxlen=2000)
        self.log = None
        self.hints = None
        self.log_handler = LogHandler(self.messages)
        logging.getLogger("Client").addHandler(self.log_handler)
        self.parser = RawJSONtoTextParser(ctx)
        self.commandprocessor = ctx.command_processor(ctx)
        self.views = {}
        self.pending_hints = True
        self.refresh_task = None

        toolbar = ttk.Frame(self.root, padding=(14, 4, 4, 4))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Server").pack(side="left")
        self.address = tk.StringVar(value=ctx.suggested_address or "archipelago.gg:")
        self.address_entry = ttk.Entry(toolbar, textvariable=self.address, width=35)
        self.address_entry.pack(side="left", padx=6, fill="x", expand=True)
        self.address_entry.bind("<Return>", lambda _: self.spawn(self.connect()))
        self.connect_button = ttk.Button(toolbar, text="Connect", style="Compact.Primary.TButton", command=lambda: self.spawn(self.connect()))
        self.connect_button.pack(side="left", padx=4)
        self.launch_button = ttk.Button(toolbar, text="Launch KH3", style="Compact.TButton", command=self.launch_game)
        self.launch_button.pack(side="left", padx=(4, 0))
        self.tabs = Tabs(self.root)
        self.tabs.pack(fill="both", expand=True, pady=(1, 0))
        self.pages = {}
        for name in ("Archipelago", "Hints", "KH3 Debug", "Save Manager", "Third Party Mods", "KH3 Config"):
            page = ttk.Frame(self.tabs.viewport, padding=0 if name in {"Archipelago", "Hints"} else (12, 8))
            self.tabs.add(page, text=name)
            self.pages[name] = page
        command_row = self.command_row = ttk.Frame(self.root, padding=(2, 10, 4, 10))
        command_row.pack(side="bottom", fill="x", before=self.tabs)
        self.command = ttk.Entry(command_row)
        self.command.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.command.bind("<Return>", self.submit_command)
        self.command_history = []
        self.history_position = 0
        self.command.bind("<Up>", lambda _: self.browse_history(-1))
        self.command.bind("<Down>", lambda _: self.browse_history(1))
        ttk.Button(command_row, text="Send", style="Compact.Primary.TButton", command=self.submit_command).pack(side="left")
        self.tabs.bind("<<NotebookTabChanged>>", self.select_tab)
        self.tabs.select(self.pages["KH3 Config"])
        self.select_tab()
        self.ctx.warn_about_steam_cloud_if_needed()

    def update_title(self, ue4ss_status, goa_version="Checking…"):
        version = ue4ss_status.partition(" | ")[0]
        self.root.title(
            f"Archipelago Kingdom Hearts III Client (APWorld v{self.apworld_version} | UE4SS {version} | GoA {goa_version})")

    def spawn(self, coroutine):
        if self.closed:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception():
                logging.getLogger("Client").error("Native UI action failed: %s", done.exception())
        task.add_done_callback(finished)
        return task

    def select_tab(self, *_):
        name = self.tabs.tab(self.tabs.select(), "text")
        if name == "Archipelago" and self.log is None:
            from .log import LogView
            self.log_view = LogView(self, self.pages[name])
            self.log = self.log_view.text
            for entry in self.log_lines:
                self.log_view.append(entry)
        elif name == "Hints" and self.hints is None:
            from .hints import HintsView
            self.hint_view = HintsView(self, self.pages[name])
            self.hints = self.hint_view.table
        if name not in self.views and name not in {"Archipelago", "Hints"}:
            if name == "KH3 Config":
                from .config import ConfigView as view_type
            elif name == "KH3 Debug":
                from .debug import DebugView as view_type
            elif name == "Save Manager":
                from .save_manager import SaveManagerView as view_type
            else:
                from .mods import ModsView as view_type
            self.views[name] = view_type(self, self.pages[name])
        self.next_refresh = 0

    async def connect(self):
        if self.action_running or self.ctx.kh3_run_action_running:
            return
        self.ctx.username = None
        self.ctx.password = None
        if self.ctx.server:
            await self.ctx.disconnect()
        else:
            await self.ctx.connect(self.address.get().replace("/connect ", ""))

    def launch_game(self):
        if not self.action_running and not self.ctx.kh3_run_action_running:
            self.ctx.launch_kh3_game()

    def submit_command(self, *_):
        text = self.command.get().strip()
        self.command.delete(0, "end")
        if self.ctx.input_requests:
            self.ctx.input_requests -= 1
            self.ctx.input_queue.put_nowait(text)
        elif text:
            if not self.command_history or self.command_history[-1] != text:
                self.command_history.append(text)
                del self.command_history[:-50]
            self.history_position = len(self.command_history)
            if text.startswith(("/", "!")):
                self.ctx.on_ui_command(text)
            self.commandprocessor(text)
        else:
            self.tabs.select(self.pages["Archipelago"])
            self.ctx.on_ui_command("/help")
            self.commandprocessor("/help")

    def browse_history(self, direction):
        self.history_position = max(0, min(len(self.command_history), self.history_position + direction))
        self.command.delete(0, "end")
        if self.history_position < len(self.command_history):
            self.command.insert(0, self.command_history[self.history_position])
        return "break"

    def print_json(self, data):
        from .log import LogEntry
        spans = []
        item_flags = []
        for node in copy.deepcopy(data):
            text = self.parser([node])
            spans.append((text, node.get("color", "")))
            item_flags.append(node.get("flags", 0) if node.get("type") in {"item_id", "item_name"} else None)
        self.messages.put(LogEntry(tuple(spans), tuple(item_flags)))

    def focus_textinput(self):
        self.command.focus_set()

    def update_address_bar(self, text):
        if not self.closed:
            self.address.set(text)

    def update_hints(self):
        self.pending_hints = True

    def set_new_energy_link_value(self):
        self.messages.put(f"EnergyLink: {self.ctx.current_energy_link_value}")

    def show_error(self, title, text):
        if self.closed:
            return None
        from .widgets import MessageBox
        title = title or "Error"
        text = str(text).replace("[Errno", "\n[Errno").strip()
        parts = title.split(". ", 1)
        if len(parts) == 1:
            parts = title.split(", ", 1)
        if len(parts) > 1:
            text = f"{parts[1]}\n\n{text}" if text else parts[1]
            title = parts[0]
        self.messagebox = MessageBox(self.root, title, text)
        return self.messagebox

    async def form(self, title, message, fields=(), *, submit="OK"):
        if self.closed:
            return None
        from .widgets import Modal
        dialog = Modal(self.root, title)
        body = dialog.body
        ttk.Label(body, text=message, style="Card.TLabel", wraplength=540).pack(fill="x", pady=(0, 12))
        values = {}
        for key, label, initial in fields:
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, style="Card.TLabel", width=18).pack(side="left")
            value = tk.StringVar(value=initial)
            ttk.Entry(row, textvariable=value, width=48).pack(side="left", fill="x", expand=True)
            values[key] = value
        future = asyncio.get_running_loop().create_future()
        self.dialogs[dialog] = future

        def resolve(accepted):
            if not future.done():
                future.set_result({key: value.get().strip() for key, value in values.items()} if accepted else None)
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", style="Card.TButton", command=lambda: resolve(False)).pack(side="right")
        ttk.Button(buttons, text=submit, style="Card.Primary.TButton", command=lambda: resolve(True)).pack(side="right", padx=8)
        dialog.show(resolve)
        try:
            return await future
        finally:
            self.dialogs.pop(dialog, None)
            if not self.closed:
                dialog.close()

    async def prompt_text_input(self, *, title, text, hint_text="", initial_text="", submit_text="OK"):
        result = await self.form(title, text, [("value", hint_text, initial_text)], submit=submit_text)
        return result["value"] if result and result["value"] else None

    async def prompt_steam_cloud_disabled_confirmation(self):
        return await self.form("Confirm Steam Cloud Is Disabled", self.ctx.KH3_STEAM_CLOUD_CONFIRMATION_TEXT) is not None

    async def prompt_patch_game_confirmation(self):
        return await self.form("Patch Game", "Install or update the KH3 Archipelago runtime for this directory?",
                               submit="Patch Game") is not None

    async def prompt_run_connection_profile(self, *, slot_name, server, port, room_url, importing=False):
        return await self.form("Import Run" if importing else "Edit Run", "Connection details for this run.", [
            ("slot_name", "Slot", slot_name), ("server", "Server", server),
            ("port", "Port", port), ("room_url", "Room URL", room_url),
        ], submit="Import" if importing else "Save")

    def show_run_switch_warning(self):
        self.spawn(self.form("Close KH3 Before Switching Runs", "Close KINGDOM HEARTS III, then activate the run again."))

    def show_seed_guard_recovery(self, *, status, seed_id, slot_name):
        if self.closed or getattr(self, "seed_recovery_pending", False):
            return
        self.seed_recovery_pending = True
        self.spawn(self._show_seed_guard_recovery(status, seed_id, slot_name))

    async def _show_seed_guard_recovery(self, status, seed_id, slot_name):
        try:
            await self.form(
                "Automatic Run Activation Paused",
                f"{status}\n\nClose KH3 and reconnect. The client will automatically preserve "
                "the previous run's saves and activate this room's run before reporting checks. "
                "If the error persists, see the client log for the activation failure.",
            )
        finally:
            self.seed_recovery_pending = False

    async def refresh(self):
        connected = bool(self.ctx.server)
        busy = bool(self.ctx.kh3_run_action_running or self.action_running)
        self.connect_button.configure(text="Disconnect" if connected else "Connect")
        # Changing only the disabled bit preserves the active (hover) state.
        self.connect_button.state(["disabled" if busy else "!disabled"])
        self.address_entry.state(["disabled" if connected else "!disabled"])
        self.launch_button.state(["disabled" if busy else "!disabled"])
        name = self.tabs.tab(self.tabs.select(), "text")
        if name in self.views:
            await self.views[name].refresh()
        if name == "Hints" and (self.pending_hints or self.hints.session != self.hints.current_session()):
            self.pending_hints = False
            self.hint_view.refresh(self.ctx.stored_data.get(f"_read_hints_{self.ctx.team}_{self.ctx.slot}", ()))

    async def async_run(self):
        self.next_refresh = 0
        try:
            while not self.closed:
                self.root.update()
                if self.closed:
                    break
                messages = []
                while len(messages) < 100 and not self.messages.empty():
                    messages.append(self.messages.get_nowait())
                if messages:
                    self.log_lines.extend(messages)
                if messages and self.log is not None:
                    for message in messages:
                        self.log_view.append(message)
                if time.monotonic() >= self.next_refresh and (self.refresh_task is None or self.refresh_task.done()):
                    self.next_refresh = time.monotonic() + 1
                    self.refresh_task = self.spawn(self.refresh())
                await asyncio.sleep(0.01)
        finally:
            self.stop()
            # Deliver dismissed dialog results before cancelling other UI work.
            await asyncio.sleep(0)
            pending = tuple(self.tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def stop(self):
        if self.closed:
            return
        self.closed = True
        logging.getLogger("Client").removeHandler(self.log_handler)
        if self.messagebox is not None:
            self.messagebox.dismiss()
        for future in tuple(self.dialogs.values()):
            if not future.done():
                future.set_result(None)
        for _ in range(self.ctx.input_requests):
            self.ctx.input_queue.put_nowait("")
        self.ctx.input_requests = 0
        self.ctx.exit_event.set()
        self.root.destroy()
        self.theme.close()
