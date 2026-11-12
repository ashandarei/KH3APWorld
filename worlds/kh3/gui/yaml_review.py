# SPDX-License-Identifier: GPL-3.0-only
"""Editable YAML review with all validation problems shown together."""

import tkinter as tk
from tkinter import ttk

import yaml

from ..yaml_options import GAME, PlayerOptions
from .window import configure_window


class YamlReview:
    def __init__(self, app, content, error=None):
        from .yaml_creator import AutoScrollbar
        self.app = app
        self.window = tk.Toplevel(app.root)
        configure_window(self.window)
        self.window.title("KH3 · Review YAML")
        self.window.geometry("1120x740")
        self.window.minsize(820, 520)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill="both", expand=True)
        self.heading = ttk.Label(body, text="Review YAML", style="Section.TLabel")
        self.heading.pack(anchor="w", pady=(0, 12))
        panes = ttk.Panedwindow(body, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes, width=340)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        self.source = tk.Text(left, background="#12171a", foreground="#e1e6dc", font=app.fonts["mono"],
                              wrap="none", undo=True, padx=12, pady=10, borderwidth=0)
        AutoScrollbar(left, self.source)
        AutoScrollbar(left, self.source, orient="horizontal")
        self.source.pack(fill="both", expand=True)
        self.source.insert("1.0", content)
        ttk.Label(right, text="Settings to fix", style="CardTitle.TLabel").pack(anchor="w", padx=16, pady=(0, 8))
        self.details = tk.Text(right, background="#0d1114", foreground="#e1e6dc", font=app.fonts["normal"],
                               wrap="word", width=36, padx=16, pady=12, borderwidth=0, cursor="arrow")
        AutoScrollbar(right, self.details)
        self.details.pack(fill="both", expand=True)
        self.details.tag_configure("title", foreground="#ffb1bb", font=app.fonts["bold"])
        self.hover = app.tooltip(self.source, "")
        self.status = tk.StringVar(self.window)
        footer = ttk.Frame(body, padding=(0, 12, 0, 0))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        app.button(footer, "Apply settings", lambda: self.check(apply=True), primary=True).pack(side="right")
        app.button(footer, "Check YAML", self.check).pack(side="right", padx=8)
        app.button(footer, "Close", self.close).pack(side="right")
        self.issue_tags = []
        self.source.bind("<<Modified>>", self._edited)
        self.source.edit_modified(False)
        if error is None:
            self.check()
        else:
            self.show_issues(error)
        self.window.grab_set()

    def _edited(self, _):
        if self.source.edit_modified():
            self.status.set("Edited · check YAML to update highlights")
            self.source.edit_modified(False)

    def _spans(self):
        result = {}
        try:
            document = yaml.compose(self.source.get("1.0", "end-1c"))
            if isinstance(document, yaml.MappingNode):
                entries = list(document.value)
                for key, value in document.value:
                    if isinstance(key, yaml.ScalarNode) and key.value == GAME and isinstance(value, yaml.MappingNode):
                        entries.extend(value.value)
                for key, value in entries:
                    if not isinstance(key, yaml.ScalarNode):
                        continue
                    result[key.value] = (f"{key.start_mark.line + 1}.0",
                                         f"{value.end_mark.line + 1}.{value.end_mark.column}")
        except yaml.YAMLError:
            pass
        return result

    def show_issues(self, error):
        self.issues = getattr(error, "issues", {"document": str(error)}) if error else {}
        for tag in self.issue_tags:
            self.source.tag_delete(tag)
            self.details.tag_delete(tag)
        self.issue_tags.clear()
        self.hover.hide()
        self.hover.text = ""
        spans = self._spans()
        mark = getattr(error, "problem_mark", None)
        if mark is not None:
            spans["document"] = (f"{mark.line + 1}.0", f"{mark.line + 1}.end")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        for index, (key, message) in enumerate(self.issues.items()):
            tag = f"issue_{index}"
            self.issue_tags.append(tag)
            title = self.app.model.types[key].display_name if key in self.app.model.types else key.replace("_", " ").title()
            self.details.insert("end", title + "\n", ("title", tag))
            self.details.insert("end", message.removeprefix(title + ": ") + "\n\n", tag)
            start, end = spans.get(key, ("1.0", "1.end"))
            self.source.tag_configure(tag, background="#46232c", foreground="#ffc3cb")
            self.source.tag_add(tag, start, end)
            self.source.tag_bind(tag, "<Enter>", lambda _, message=message: self._hover(message))
            self.source.tag_bind(tag, "<Leave>", lambda _: self._hover(""))
            self.details.tag_bind(tag, "<Button-1>", lambda _, start=start: self.source.see(start))
        if self.issues:
            count = len(self.issues)
            self.heading.configure(text=f"Review YAML · {count} {'issue' if count == 1 else 'issues'}")
            self.status.set("Edit the highlighted YAML, then check again")
            self.source.see(spans.get(next(iter(self.issues)), ("1.0",))[0])
        else:
            self.heading.configure(text="Review YAML · ready")
            self.details.insert("end", "No settings errors.\n\nApply settings to return these values to the creator.")
            self.status.set("All settings are valid")
        self.details.configure(state="disabled")

    def _hover(self, message):
        self.hover.hide()
        self.hover.text = message
        if message:
            self.hover.schedule()

    def check(self, *, apply=False):
        try:
            candidate = PlayerOptions.from_text(self.source.get("1.0", "end-1c"))
        except Exception as exc:
            self.show_issues(exc)
            return False
        self.show_issues(None)
        if apply:
            self.app._replace_model(candidate)
            self.app.status.set("Applied reviewed settings")
            self.close()
        return True

    def close(self):
        self.hover.hide()
        self.window.destroy()
        self.app.review = None
