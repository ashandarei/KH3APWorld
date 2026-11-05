"""KH3-inspired charcoal, silver, blue and gold styling, without Kivy."""

import os
import tkinter as tk
from pathlib import Path
from tkinter import font, ttk

SURFACE = "#101319"
PANEL = "#1a202a"
HOVER = "#1d2530"
TEXT = "#eef2f7"
MUTED = "#b1bdce"
ACCENT = "#9fd7fa"
ON_ACCENT = "#f5f9ff"
OUTLINE = "#505f73"
SUBTLE = "#303e52"
GOLD = "#ebce8e"
PRIMARY = "#315f7e"
ERROR = "#a93445"
# Low-contrast blue and gold tints retain the charcoal surface and light text.
RUN_PLATFORM_COLORS = {"steam": "#1b2b37", "egs": "#302c23", "": PANEL}
MESSAGE_COLORS = {"red": "#ff97a3", "green": "#9cdec1", "yellow": GOLD, "blue": ACCENT,
                  "magenta": "#e3b6f1", "cyan": "#a7e2ec", "slateblue": "#b6c4ff",
                  "plum": "#dbb9f3", "salmon": "#ffaaa0", "orange": "#efbd8b",
                  "white": TEXT, "black": MUTED}


class Theme:
    def __init__(self, root, *, font_size=12):
        self.root = root
        self.images = []  # Tcl image elements do not retain Python references.
        self.icons = {}
        self.private_fonts = []
        self.family = "Segoe UI" if os.name == "nt" else "sans-serif"
        if os.name == "nt":
            import ctypes
            self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
            self.gdi.AddFontResourceExW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
            self.gdi.RemoveFontResourceExW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
            for path in Path(__file__).with_name("assets").joinpath("fonts").glob("Roboto-*.ttf"):
                if self.gdi.AddFontResourceExW(str(path), 0x10, None):
                    self.private_fonts.append(str(path))
            if any(Path(path).name == "Roboto-Regular.ttf" for path in self.private_fonts):
                self.family = "Roboto"
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name, root=root).configure(family=self.family, size=font_size)
        root.configure(background=SURFACE)
        # Classic Tk widgets (logs, canvases, dialogs) share the ttk colors.
        for key, value in {
            "*background": SURFACE, "*foreground": TEXT, "*insertBackground": ACCENT,
            "*selectBackground": SUBTLE, "*selectForeground": TEXT,
            "*Text.font": (self.family, font_size), "*Text.relief": "flat",
            "*Text.highlightThickness": 0, "*Text.padX": 12, "*Text.padY": 10,
            "*Canvas.background": SURFACE,
        }.items():
            root.option_add(key, value)
        self.style = style = ttk.Style(root)
        style.theme_use("clam")  # Native Windows themes ignore several dark colors.
        style.configure(".", background=SURFACE, foreground=TEXT, font=(self.family, font_size),
                        bordercolor=SUBTLE, lightcolor=SUBTLE, darkcolor=SUBTLE,
                        troughcolor=SURFACE, selectbackground=SUBTLE, selectforeground=TEXT)
        style.configure("TLabel", foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Heading.TLabel", foreground=GOLD, font=(self.family, font_size + 1, "bold"))
        style.configure("Card.TFrame", background=PANEL)
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure("CardTitle.TLabel", background=PANEL, foreground=TEXT, font=(self.family, font_size + 1, "bold"))
        style.configure("TSeparator", background=SUBTLE)
        style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT, insertcolor=ACCENT,
                        padding=(10, 10), borderwidth=1, bordercolor=OUTLINE)
        style.map("TEntry", bordercolor=[("focus", ACCENT)],
                  fieldbackground=[("disabled", SURFACE)], foreground=[("disabled", MUTED)])
        self._buttons("TButton", filled=False)
        self._buttons("Primary.TButton", filled=True)
        self._buttons("Alert.TButton", filled=True, alert=True)
        self._buttons("Compact.TButton", filled=False, radius=2)
        self._buttons("Compact.Primary.TButton", filled=True, radius=2)
        style.configure("Icon.TButton", padding=(8, 6))
        for name in ("TButton", "Primary.TButton", "Alert.TButton", "Icon.TButton"):
            style.configure("Card." + name, background=PANEL)
            style.map("Card." + name, background=[("disabled", PANEL), ("!disabled", PANEL)])
        style.configure("TCheckbutton", padding=(0, 8), foreground=TEXT, indicatormargin=(0, 0, 10, 0))
        style.map("TCheckbutton", background=[("active", SURFACE)],
                  indicatorbackground=[("selected", ACCENT), ("!selected", SURFACE)],
                  indicatorforeground=[("selected", ON_ACCENT)],
                  foreground=[("disabled", MUTED), ("active", ACCENT)])
        style.configure("Card.TCheckbutton", background=PANEL)
        style.map("Card.TCheckbutton", background=[("active", PANEL), ("!active", PANEL)])
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, rowheight=32 + max(0, font_size - 12) * 2, borderwidth=0)
        style.map("Treeview", background=[("selected", SUBTLE)], foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=PANEL, foreground=ACCENT, padding=(8, 10), relief="flat")
        style.map("Treeview.Heading", background=[("active", HOVER)])
        style.configure("TScrollbar", background=SUBTLE, arrowcolor=MUTED, borderwidth=0)
        style.map("TScrollbar", background=[("active", OUTLINE), ("pressed", ACCENT)])
        self._switches()

    def _pill(self, fill, outline, radius=15):
        # A tiny nine-slice image supplies rounded buttons without a canvas per
        # widget, animation loop, external image package, or full-size textures.
        # A broad center avoids thousands of tiny GDI tile copies per paint.
        width, height = 128, 64
        image = tk.PhotoImage(master=self.root, width=width, height=height)
        for y in range(height):
            distance = max(0, radius - y, y - (height - 1 - radius))
            inset = round(radius - (radius * radius - distance ** 2) ** .5)
            image.put(outline, to=(inset, y, width - inset, y + 1))
            if distance <= radius - 1:
                inner = round(radius - ((radius - 1) ** 2 - distance ** 2) ** .5)
                image.put(fill, to=(inner, y, width - inner, y + 1))
        self.images.append(image)
        return image

    def _buttons(self, name, *, filled, alert=False, radius=15):
        foreground = ON_ACCENT if filled else TEXT
        base = ERROR if alert else PRIMARY if filled else "#293342"
        over = "#872b39" if alert else "#264b65" if filled else "#202936"
        down = "#702431" if alert else "#1e3c51" if filled else "#19212c"
        normal = self._pill(base, base, radius)
        hover = self._pill(over, over, radius)
        pressed = self._pill(down, down, radius)
        disabled = self._pill(PANEL, PANEL, radius)
        element = f"KH3.{name}.border"
        self.style.element_create(element, "image", normal, ("disabled", disabled), ("pressed", pressed),
                                  ("active", hover), border=radius, padding=0,
                                  width=32, height=32, sticky="nsew")
        self.style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]}),
        ]})])
        self.style.configure(name, foreground=foreground, padding=(16, 8), anchor="center", width=0)
        self.style.map(name, foreground=[("disabled", MUTED), ("!disabled", foreground)],
                       background=[("disabled", SURFACE), ("!disabled", SURFACE)])

    def icon(self, name):
        if name in self.icons:
            return self.icons[name]
        icon = tk.PhotoImage(master=self.root, width=22, height=22)
        def rect(x1, y1, x2, y2):
            icon.put(TEXT, to=(x1, y1, x2, y2))
        if name == "folder":
            for box in ((3, 5, 10, 7), (9, 7, 19, 9), (2, 7, 4, 18),
                        (3, 18, 20, 20), (19, 9, 21, 19), (4, 10, 19, 12)):
                rect(*box)
        elif name == "trash":
            for box in ((8, 2, 14, 4), (4, 5, 18, 7), (5, 8, 7, 19),
                        (15, 8, 17, 19), (7, 18, 15, 20), (9, 9, 10, 17), (12, 9, 13, 17)):
                rect(*box)
        elif name == "detect":
            for y in range(4, 18):
                for x in range(4, 18):
                    if 26 <= (x - 10.5) ** 2 + (y - 10.5) ** 2 <= 40:
                        rect(x, y, x + 1, y + 1)
            for box in ((10, 1, 12, 7), (10, 15, 12, 21), (1, 10, 7, 12), (15, 10, 21, 12)):
                rect(*box)
        elif name == "more":
            for x in (3, 9, 15):
                rect(x, 9, x + 3, 12)
        else:
            for box in ((10, 4, 12, 6), (10, 9, 12, 17), (7, 17, 15, 19)):
                rect(*box)
        self.icons[name] = icon
        return icon

    def _switches(self):
        def switch(selected):
            image = tk.PhotoImage(master=self.root, width=42, height=24)
            for y in range(2, 22):
                inset = round(10 - (100 - (y - 11.5) ** 2) ** .5)
                image.put(PRIMARY if selected else OUTLINE, to=(inset, y, 42 - inset, y + 1))
            center = 31 if selected else 11
            for y in range(4, 20):
                extent = (64 - (y - 11.5) ** 2) ** .5
                image.put(TEXT, to=(round(center - extent), y, round(center + extent), y + 1))
            self.images.append(image)
            return image
        off, on = switch(False), switch(True)
        self.style.element_create("KH3.Switch", "image", off, ("selected", on), width=42, height=24)
        self.style.layout("Switch.TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("KH3.Switch", {"side": "left"}), ("Checkbutton.label", {"side": "left", "sticky": "w"}),
        ]})])
        self.style.configure("Switch.TCheckbutton", padding=(0, 6), space=10)

    def close(self):
        for path in self.private_fonts:
            self.gdi.RemoveFontResourceExW(path, 0x10, None)
        self.private_fonts.clear()
