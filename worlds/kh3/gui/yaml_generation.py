# SPDX-License-Identifier: GPL-3.0-only
"""Player selection and generation actions for the YAML creator."""

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

import yaml

import Utils
from ..yaml_generation import generate_archive, start_server
from ..yaml_options import GAME
from .file_drop import bind_file_drop
from .generation_settings import AdvancedSettings
from .window import configure_window


def _display_choice(value):
    # List and weighted choices have no single result until generation.
    if isinstance(value, (list, dict)):
        value = " / ".join(map(str, value))
    return " ".join(str(value).splitlines())


def file_player_label(path):
    labels = []
    for document in Utils.parse_yamls(path.read_text(encoding="utf-8-sig")):
        if document is None:
            continue
        if not isinstance(document, dict):
            raise ValueError("Expected player settings")
        name = _display_choice(document.get("name") or path.stem)
        game = _display_choice(document.get("game") or "Unknown game")
        labels.append(f"{name} ({game})")
    if not labels:
        raise ValueError("Empty player YAML")
    return ", ".join(sorted(labels, key=str.casefold))


class GenerationDialog:
    def __init__(self, app, content, player):
        from .yaml_creator import AutoScrollbar, FIELD, TEXT

        self.content = content
        self.player = player
        self.files = [None]  # The editor snapshot has no on-disk input path.
        self.busy = False
        self.archive = None
        self.window = window = tk.Toplevel(app.root)
        window.title("Start Generation")
        window.transient(app.root)
        configure_window(window)
        self.form = body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        header = ttk.Frame(body)
        header.pack(fill="x", pady=(12, 6))
        hint = ttk.Label(header, text="Player YAMLs")
        hint.pack(side="left")
        self.add = app.button(header, "Add files…", self.browse)
        self.add.pack(side="right")
        self.remove = app.button(header, "Remove", self.remove_selected)
        self.remove.pack(side="right", padx=6)
        frame = ttk.Frame(body)
        frame.pack(fill="both", expand=True)
        self.listing = tk.Listbox(frame, width=64, height=7, selectmode="extended", exportselection=False,
                                  activestyle="none", relief="flat", background=FIELD, foreground=TEXT,
                                  font=app.fonts["normal"])
        self.listing.insert("end", f"{player} ({GAME})")
        AutoScrollbar(frame, self.listing)
        self.listing.pack(fill="both", expand=True)
        self.listing.bind("<<ListboxSelect>>", lambda _: self.refresh_controls())
        self.listing.bind("<Delete>", lambda _: self.remove_selected())
        if bind_file_drop(window, self.add_files):
            hint.configure(text="Drop additional YAMLs into this window")
        self.advanced = AdvancedSettings(body)
        self.advanced.pack(fill="x", pady=(8, 0))
        self.status = tk.StringVar(window)
        ttk.Label(body, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=6)
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.host = app.button(actions, "Start Local Server", lambda: self.start(True), primary=True)
        self.host.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.generate = app.button(actions, "Generate Zip", lambda: self.start(False))
        self.generate.pack(side="right", expand=True, fill="x", padx=(4, 0))
        self.result = ttk.Frame(window, padding=18)
        self.result_title = ttk.Label(self.result, style="Section.TLabel")
        self.result_title.pack(anchor="w", pady=(0, 10))
        details = ttk.Frame(self.result)
        details.pack(fill="both", expand=True)
        self.details = tk.Text(details, width=64, height=9, wrap="word", relief="flat", padx=10, pady=8,
                               background=FIELD, foreground=TEXT, font=app.fonts["normal"])
        AutoScrollbar(details, self.details)
        self.details.pack(fill="both", expand=True)
        self.result_action = app.button(self.result, "Close", self.close, primary=True)
        self.result_action.pack(anchor="e", pady=(10, 0))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda _: self.close())
        self.refresh_controls()
        window.grab_set()
        self.listing.focus_set()

    def close(self):
        if not self.busy:
            self.window.destroy()

    def browse(self):
        paths = filedialog.askopenfilenames(parent=self.window, title="Add player YAMLs",
                                            filetypes=[("Player YAMLs", "*.yaml *.yml")])
        self.add_files(paths)

    def add_files(self, paths):
        if self.busy or not self.form.winfo_manager():
            return
        skipped = []
        for filename in paths:
            path = Path(filename)
            if path.suffix.lower() not in {".yaml", ".yml"} or not path.is_file():
                skipped.append(path.name)
            elif path not in self.files:
                try:
                    label = file_player_label(path)
                except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
                    skipped.append(path.name)
                    continue
                index = bisect_right(self.listing.get(0, "end"), label.casefold(), key=str.casefold)
                self.files.insert(index, path)
                self.listing.insert(index, label)
        self.status.set("Skipped: " + ", ".join(skipped) if skipped else "")
        self.refresh_controls()

    def remove_selected(self):
        if not self.busy:
            for index in reversed(self.listing.curselection()):
                del self.files[index]
                self.listing.delete(index)
            self.refresh_controls()

    def refresh_controls(self):
        self.advanced.set_busy(self.busy)
        self.add.configure(state="disabled" if self.busy else "normal")
        for button in (self.host, self.generate):
            button.configure(state="normal" if self.files and not self.busy else "disabled")
        self.remove.configure(state="normal" if self.listing.curselection() and not self.busy else "disabled")

    def start(self, host):
        if self.busy or not self.files:
            return
        try:
            settings = self.advanced.values()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.busy = True
        self.archive = None
        self.refresh_controls()
        self.status.set("Generating multiworld…")
        content = self.content if None in self.files else None
        paths = tuple(path for path in self.files if path is not None)
        self.run_task(generate_archive, (content, paths, settings), lambda path: self.generated(path, host, settings))

    def run_task(self, function, args, on_success):
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(function, *args)
        executor.shutdown(wait=False)

        def complete():
            try:
                on_success(future.result())
            except Exception as exc:
                archive = f"\n\nZIP: {self.archive}" if self.archive else ""
                self.show_result("Could not finish generation", f"{exc}{archive}", retry=True)

        def notify(_):
            try:
                self.window.after(0, complete)
            except (RuntimeError, tk.TclError):
                pass  # The editor was closed during generation.

        future.add_done_callback(notify)

    def generated(self, archive, host, settings):
        self.archive = archive
        if host:
            self.status.set("Starting local server…")
            self.run_task(start_server, (archive, settings), self.hosted)
        else:
            Utils.open_file(archive.parent)
            self.window.destroy()

    def hosted(self, connection):
        player = f"\nPlayer: {self.player}" if None in self.files else ""
        self.show_result("Local server started", f"{connection}{player}\nZIP: {self.archive}")

    def show_result(self, title, details, *, retry=False):
        self.busy = False
        self.form.pack_forget()
        self.result_title.configure(text=title)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", details)
        self.details.configure(state="disabled")
        self.result_action.configure(text="Back" if retry else "Close", command=self.back if retry else self.close)
        self.result.pack(fill="both", expand=True)
        self.result_action.focus_set()

    def back(self):
        self.result.pack_forget()
        self.form.pack(fill="both", expand=True)
        self.status.set("")
        self.refresh_controls()
        self.listing.focus_set()
