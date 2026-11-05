"""Reusable native tabs, switches, hover details and icon controls."""

import time
import tkinter as tk
from tkinter import font, ttk

from .theme import GOLD, MUTED, PANEL, SUBTLE, TEXT


class Entry(ttk.Entry):
    """An entry with optional placeholder text that never enters its value."""

    def __init__(self, parent, *, textvariable=None, placeholder="", **kwargs):
        self.variable = textvariable if textvariable is not None else tk.StringVar(master=parent)
        super().__init__(parent, textvariable=self.variable, **kwargs)
        self.placeholder_label = tk.Label(
            self, text=placeholder, foreground=MUTED, background=PANEL,
            font=self.cget("font"), anchor="w", padx=0, pady=0, borderwidth=0, cursor="xterm")
        self.placeholder_label.bind("<Button-1>", lambda _: self.focus_set())
        self.bind("<FocusIn>", self.update_placeholder, add="+")
        self.bind("<FocusOut>", self.update_placeholder, add="+")
        self.bind("<Destroy>", self.dispose, add="+")
        self.trace = self.variable.trace_add("write", self.update_placeholder)
        self.update_placeholder()

    def update_placeholder(self, *_):
        if self.placeholder_label.cget("text") and not self.variable.get() and self.focus_get() is not self:
            self.placeholder_label.place(x=11, rely=.5, anchor="w", relwidth=1, width=-22)
        else:
            self.placeholder_label.place_forget()

    def dispose(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self.trace)


class ContextMenu(tk.Frame):
    """Client-drawn menu so Windows cannot override disabled/hover styling."""
    def __init__(self, root, family, *, font_size=15):
        super().__init__(root, background=SUBTLE, padx=1, pady=1)
        self.family = family
        self.font_size = font_size
        self.buttons = []
        self.return_focus = None
        self.bind("<ButtonPress>", lambda event: self.unpost() if event.widget is self else None)
        self.bind("<Escape>", lambda _: self.unpost())
        self.bind("<Down>", lambda _: self.move_focus(1))
        self.bind("<Up>", lambda _: self.move_focus(-1))
        self.bind("<Tab>", lambda _: self.move_focus(1))
        self.bind("<Shift-Tab>", lambda _: self.move_focus(-1))
        self.bind("<ISO_Left_Tab>", lambda _: self.move_focus(-1))

    def clear(self):
        self.unpost()
        for button in self.buttons:
            button.destroy()
        self.buttons.clear()

    def add_command(self, *, label, command, state="normal"):
        def invoke():
            self.unpost()
            command()
        button = tk.Button(self, text=label, command=invoke, state=state, anchor="w",
                           font=(self.family, self.font_size), padx=16, pady=8, relief="flat", borderwidth=0,
                           highlightthickness=0, background=PANEL, foreground=TEXT,
                           disabledforeground="#697382", activebackground=SUBTLE, activeforeground=TEXT)
        button.pack(fill="x")
        button.bind("<Enter>", lambda _: button.configure(background=SUBTLE)
                    if str(button.cget("state")) != "disabled" else None)
        button.bind("<Leave>", lambda _: button.configure(background=PANEL))
        button.bind("<Escape>", lambda _: self.unpost())
        button.bind("<Return>", lambda _: button.invoke())
        for sequence, direction in (("<Down>", 1), ("<Up>", -1), ("<Tab>", 1),
                                    ("<Shift-Tab>", -1), ("<ISO_Left_Tab>", -1)):
            button.bind(sequence, lambda _, step=direction: self.move_focus(step))
        self.buttons.append(button)

    def move_focus(self, direction):
        enabled = [button for button in self.buttons if str(button.cget("state")) != "disabled"]
        if enabled:
            current = self.focus_get()
            index = enabled.index(current) if current in enabled else (-1 if direction > 0 else 0)
            enabled[(index + direction) % len(enabled)].focus_set()
        return "break"

    def entryconfigure(self, index, **kwargs):
        if "label" in kwargs:
            kwargs["text"] = kwargs.pop("label")
        self.buttons[index].configure(**kwargs)
        if kwargs.get("state") == "disabled":
            self.buttons[index].configure(background=PANEL)

    def show(self, x_root, y_root):
        self.return_focus = self.focus_get()
        self.update_idletasks()
        root = self.master
        x = min(x_root - root.winfo_rootx(), root.winfo_width() - self.winfo_reqwidth())
        y = min(y_root - root.winfo_rooty(), root.winfo_height() - self.winfo_reqheight())
        self.place(x=max(0, x), y=max(0, y))
        self.lift()
        self.grab_set()
        self.focus_set()

    def unpost(self):
        if self.grab_current() is self:
            self.grab_release()
            if self.return_focus is not None and self.return_focus.winfo_exists():
                self.return_focus.focus_set()
        self.place_forget()


class Dropdown(ttk.Button):
    """Themed selector with a client-drawn popup, including keyboard navigation."""
    def __init__(self, parent, *, textvariable, values, family, width=24, font_size=12):
        self.variable = textvariable
        self.values = tuple(values)
        self.family = family
        self.font_size = font_size
        self.popup = None
        self.previous_grab = None
        self.caption = tk.StringVar(master=parent)
        super().__init__(parent, textvariable=self.caption, width=width, command=self.open)
        self.trace = self.variable.trace_add("write", self.update_caption)
        self.update_caption()
        for sequence in ("<Down>", "<Up>", "<Return>"):
            self.bind(sequence, self.open)
        self.bind("<Unmap>", lambda _: self.close())
        self.bind("<Destroy>", self.dispose, add="+")

    def update_caption(self, *_):
        self.caption.set(self.variable.get() + "  \u25be")

    def set(self, value):
        if value not in self.values:
            raise ValueError(f"Unknown dropdown choice: {value}")
        self.variable.set(value)

    def open(self, *_):
        if self.instate(["disabled"]):
            return "break"
        if self.popup is not None:
            self.close()
            return "break"
        self.previous_grab = self.grab_current()
        popup = self.popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        border = tk.Frame(popup, background=SUBTLE, padx=1, pady=1)
        border.pack(fill="both", expand=True)
        self.options = []
        for value in self.values:
            selected = value == self.variable.get()
            button = tk.Button(border, text=value, anchor="w", font=(self.family, self.font_size),
                               command=lambda choice=value: self.choose(choice),
                               background=SUBTLE if selected else PANEL, foreground=GOLD if selected else TEXT,
                               activebackground=SUBTLE, activeforeground=TEXT, relief="flat", borderwidth=0,
                               highlightthickness=1, highlightbackground=PANEL, highlightcolor=GOLD,
                               padx=14, pady=7)
            button.pack(fill="x")
            button.bind("<Enter>", lambda _, control=button: control.focus_set())
            self.options.append(button)
        popup.update_idletasks()
        width = max(self.winfo_width(), popup.winfo_reqwidth())
        height = popup.winfo_reqheight()
        x = min(self.winfo_rootx(), self.winfo_screenwidth() - width)
        y = self.winfo_rooty() + self.winfo_height() + 3
        if y + height > self.winfo_screenheight():
            y = self.winfo_rooty() - height - 3
        popup.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        popup.deiconify()
        popup.lift()
        popup.grab_set()
        selected = self.values.index(self.variable.get()) if self.variable.get() in self.values else 0
        self.options[selected].focus_set()
        popup.bind("<Escape>", lambda _: self.close())
        popup.bind("<Return>", self.confirm)
        popup.bind("<Down>", lambda _: self.move(1))
        popup.bind("<Up>", lambda _: self.move(-1))
        popup.bind("<Tab>", lambda _: self.close())
        popup.bind("<Shift-Tab>", lambda _: self.close())
        popup.bind("<ButtonPress-1>", self.outside_click)
        return "break"

    def move(self, direction):
        focused = self.focus_get()
        index = self.options.index(focused) if focused in self.options else 0
        self.options[(index + direction) % len(self.options)].focus_set()
        return "break"

    def confirm(self, _=None):
        focused = self.focus_get()
        if focused in self.options:
            focused.invoke()
        return "break"

    def outside_click(self, event):
        popup = self.popup
        if popup and not (popup.winfo_rootx() <= event.x_root < popup.winfo_rootx() + popup.winfo_width()
                          and popup.winfo_rooty() <= event.y_root < popup.winfo_rooty() + popup.winfo_height()):
            return self.close()

    def choose(self, value):
        self.set(value)
        self.close()
        self.event_generate("<<DropdownSelected>>")

    def close(self):
        if self.popup is not None:
            popup, self.popup = self.popup, None
            if popup.grab_current() is popup:
                popup.grab_release()
                if self.previous_grab is not None and self.previous_grab.winfo_exists():
                    self.previous_grab.grab_set()
                if self.winfo_exists():
                    self.focus_set()
            popup.destroy()
        return "break"

    def dispose(self, event):
        if event.widget is self:
            self.close()
            self.variable.trace_remove("write", self.trace)


class Modal(tk.Frame):
    """Centered, non-draggable dialog contained by the client window."""
    def __init__(self, root, title):
        super().__init__(root, background=SUBTLE, padx=1, pady=1)
        self.body = ttk.Frame(self, style="Card.TFrame", padding=22)
        self.body.pack(fill="both", expand=True)
        if title:
            ttk.Label(self.body, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 14))
        self.return_focus = root.focus_get()
        self.previous_grab = root.grab_current()
        self.bindings = []
        self.animation = None

    def show(self, resolve):
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        started = time.monotonic()

        def step():
            progress = min(1, (time.monotonic() - started) / .16)
            scale = .94 + .06 * (1 - (1 - progress) ** 2)
            self.place(relx=.5, rely=.5, anchor="center", width=round(width * scale), height=round(height * scale))
            self.animation = self.after(16, step) if progress < 1 else None
        step()
        self.lift()
        self.grab_set()
        controls = []

        def collect(widget):
            for child in widget.winfo_children():
                if child.winfo_class() in {"TEntry", "TButton", "TCheckbutton"}:
                    controls.append(child)
                collect(child)
        collect(self)
        (controls[0] if controls else self).focus_set()

        def key(event):
            if self.grab_current() is not self:
                return
            if event.keysym == "Escape":
                resolve(False)
            elif event.keysym == "Return":
                if event.widget.winfo_class() == "TButton":
                    event.widget.invoke()
                else:
                    resolve(True)
            elif controls:
                index = controls.index(event.widget) if event.widget in controls else -1
                controls[(index + (-1 if event.state & 1 or event.keysym == "ISO_Left_Tab" else 1)) % len(controls)].focus_set()
            return "break"
        for sequence in ("<Escape>", "<Return>", "<Tab>", "<Shift-Tab>", "<ISO_Left_Tab>"):
            self.bindings.append((sequence, self.master.bind(sequence, key, add="+")))

    def close(self):
        if self.animation is not None:
            self.after_cancel(self.animation)
        for sequence, binding in self.bindings:
            self.master.unbind(sequence, binding)
        if self.grab_current() is self:
            self.grab_release()
            if self.previous_grab is not None and self.previous_grab.winfo_exists():
                self.previous_grab.grab_set()
            if self.return_focus is not None and self.return_focus.winfo_exists():
                self.return_focus.focus_set()
        self.destroy()


class MessageBox(Modal):
    """Nonblocking client error with CommonClient's repeatable dismiss contract."""
    def __init__(self, root, title, text):
        super().__init__(root, title)
        self.dismissed = False
        ttk.Label(self.body, text=text, style="Card.TLabel", wraplength=540).pack(fill="x", pady=(0, 12))
        ttk.Button(self.body, text="OK", style="Card.Primary.TButton", command=self.dismiss).pack(anchor="e")
        self.show(lambda _: self.dismiss())

    def dismiss(self):
        if not self.dismissed:
            self.dismissed = True
            self.close()


def wrapped_line_count(text, text_font, width):
    """Measure before mapping: withdrawn Tk Text widgets report stale wrapping."""
    count = 0
    for line in text.split("\n"):
        count += 1
        while text_font.measure(line) > width:
            low, high = 1, len(line)
            while low < high:
                middle = (low + high + 1) // 2
                if text_font.measure(line[:middle]) <= width:
                    low = middle
                else:
                    high = middle - 1
            end = max(line.rfind(" ", 0, low + 1), line.rfind("\t", 0, low + 1))
            line = line[end + 1 if end > 0 else low:].lstrip()
            if line:
                count += 1
    return count


class Tooltip:
    def __init__(self, widget, text="", *, bind_hover=True, at_pointer=False):
        self.widget, self.text = widget, text
        self.at_pointer = at_pointer
        self.window = None
        self.timer = None
        if bind_hover:
            widget.bind("<Enter>", self.schedule, add="+")
            widget.bind("<Leave>", self.defer_hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")
        widget.bind("<Unmap>", self.hide, add="+")

    def cancel_timer(self):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None

    def schedule(self, *_):
        self.cancel_timer()
        self.timer = self.widget.after(400, self.show)

    def defer_hide(self, *_):
        self.cancel_timer()
        self.timer = self.widget.after(180, self.hide)

    def show(self):
        self.timer = None
        text = self.text() if callable(self.text) else self.text
        if self.window is not None or not text or not self.widget.winfo_ismapped():
            return
        window = self.window = tk.Toplevel(self.widget)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        body = tk.Frame(window, background=SUBTLE, padx=1, pady=1)
        body.pack(fill="both", expand=True)
        text_font = font.nametofont("TkDefaultFont")
        # Size to actual glyph widths, with a readable cap for long paths/lists.
        width = min(440, max(text_font.measure(line) for line in text.splitlines())) + 26
        details = tk.Text(body, wrap="word", background=PANEL, foreground=TEXT, font=text_font, width=1,
                          height=1,
                          padx=12, pady=10, borderwidth=0, highlightthickness=0)
        details.pack(side="left", fill="both", expand=True)
        details.insert("1.0", text)
        details.configure(state="disabled")
        lines = wrapped_line_count(text, text_font, width - 26)
        height = min(lines * text_font.metrics("linespace") + 22,
                     440, max(100, window.winfo_screenheight() - 80))
        if lines * text_font.metrics("linespace") + 22 > height:
            scroll = ttk.Scrollbar(body, command=details.yview)
            details.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            width += scroll.winfo_reqwidth()
        window.bind("<Enter>", lambda _: self.cancel_timer())
        window.bind("<Leave>", self.defer_hide)
        window.update_idletasks()
        if self.at_pointer:
            anchor_x, anchor_y = self.widget.winfo_pointerxy()
            y = anchor_y + 18
        else:
            anchor_x, anchor_y = self.widget.winfo_rootx(), self.widget.winfo_rooty()
            y = anchor_y + self.widget.winfo_height() + 6
        x = min(anchor_x, window.winfo_screenwidth() - width - 12)
        if y + height > window.winfo_screenheight():
            y = anchor_y - height - 6
        window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        window.deiconify()

    def hide(self, *_):
        self.cancel_timer()
        if self.window is not None:
            window = self.window
            self.window = None
            window.destroy()
            self.cancel_timer()


class Switch(ttk.Checkbutton):
    def __init__(self, parent, *, text, variable, command=None, **kwargs):
        super().__init__(parent, text="  " + text, variable=variable, command=command,
                         style="Switch.TCheckbutton", **kwargs)


class Tabs(ttk.Frame):
    """Clipped sliding pages with equal-width, static-color navigation."""
    transition_seconds = .4  # Kivy SlideTransition default, with out_quad easing.
    def __init__(self, parent):
        super().__init__(parent)
        self.bar = tk.Frame(self, background=SUBTLE)
        self.bar.pack(fill="x")
        self.viewport = ttk.Frame(self)
        self.viewport.pack(fill="both", expand=True)
        self.buttons = {}
        self.pages = {}
        self.active = None
        self.animation = None
        self.viewport.bind("<Configure>", lambda _: self.finish_transition())
        self.bind("<Destroy>", lambda event: self.finish_transition() if event.widget is self else None)

    def add(self, page, *, text):
        index = len(self.buttons)
        self.pages[str(page)] = (page, text)
        button = tk.Button(self.bar, text=text, command=lambda: self.select(page),
                           relief="flat", borderwidth=0, highlightthickness=0, pady=13,
                           font="TkDefaultFont", cursor="hand2", takefocus=True)
        button.configure(background=PANEL, activebackground=PANEL, foreground=TEXT, activeforeground=TEXT)
        button.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 1, 0))
        self.bar.columnconfigure(index, weight=1, uniform="tabs")
        self.buttons[str(page)] = button

    def changed(self):
        for key, button in self.buttons.items():
            color, foreground = (SUBTLE, GOLD) if key == self.active else (PANEL, TEXT)
            button.configure(background=color, activebackground=color, foreground=foreground,
                             activeforeground=foreground)
        self.event_generate("<<NotebookTabChanged>>")

    def select(self, page=None):
        if page is None:
            return self.active
        target = str(page)
        if target == self.active:
            self.finish_transition()  # A second click completes the slide immediately.
            return self.active
        self.finish_transition()
        previous = self.active
        self.active = target
        self.pages[target][0].place(x=0, y=0, relwidth=1, relheight=1)
        self.changed()  # Construct lazy content before its first animated frame.
        if previous and self.winfo_ismapped() and self.viewport.winfo_width() > 1:
            order = list(self.pages)
            direction = 1 if order.index(target) > order.index(previous) else -1
            started = time.monotonic()
            width = self.viewport.winfo_width()

            def step():
                progress = min(1, (time.monotonic() - started) / self.transition_seconds)
                offset = round(width * (1 - progress) ** 2) * direction
                self.pages[previous][0].place(x=offset - width * direction, y=0, relwidth=1, relheight=1)
                self.pages[target][0].place(x=offset, y=0, relwidth=1, relheight=1)
                if progress < 1:
                    self.animation = self.after(16, step)
                else:
                    self.animation = None
                    self.finish_transition()
            step()
        else:
            self.finish_transition()
        return self.active

    def finish_transition(self):
        if self.animation is not None:
            self.after_cancel(self.animation)
            self.animation = None
        for key, (page, _) in self.pages.items():
            if page.winfo_exists():
                if key == self.active:
                    page.place(x=0, y=0, relwidth=1, relheight=1)
                else:
                    page.place_forget()

    def tab(self, page, option):
        if option != "text":
            raise ValueError(option)
        return self.pages[str(page)][1]
