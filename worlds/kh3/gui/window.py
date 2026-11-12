"""Packaged window icons and optional OS title-bar styling for the native client."""

import os
import tkinter as tk
from contextlib import contextmanager
from pkgutil import get_data

from .theme import SURFACE, TEXT


def configure_window(root):
    # Package reads work from both a source tree and a zipped APWorld.
    root.kh3_icon_images = [tk.PhotoImage(master=root, data=get_data(__package__, f"assets/{name}"))
                            for name in ("kh3-icon.png", "kh3-icon-32.png", "kh3-icon-16.png")]
    root.iconphoto(True, *root.kh3_icon_images)
    if os.name == "nt":
        root.bind("<Map>", lambda event: apply_dark_titlebar(root) if event.widget is root else None, add="+")


def apply_dark_titlebar(root):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        dwm.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT: Tk's decorated wrapper window.
        dark = wintypes.BOOL(True)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        # Explicit caption/text colors also keep Windows 11 light-mode desktops dark.
        # Unsupported attributes on older Windows versions simply return an HRESULT.
        for attribute, color in ((35, SURFACE), (36, TEXT)):
            red, green, blue = (int(color[offset:offset + 2], 16) for offset in (1, 3, 5))
            value = wintypes.DWORD(red | green << 8 | blue << 16)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        # Desktop decoration support must never prevent the client from opening.
        pass


@contextmanager
def suspend_redraw(widget):
    """Keep the old Windows panel visible until its new layout is complete."""
    if os.name != "nt" or not widget.winfo_ismapped():
        yield False
        return
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.SendMessageW.restype = ctypes.c_ssize_t
        user32.RedrawWindow.argtypes = (wintypes.HWND, ctypes.c_void_p, wintypes.HRGN, wintypes.UINT)
        user32.RedrawWindow.restype = wintypes.BOOL
        hwnd = widget.winfo_id()
    except (AttributeError, OSError):
        yield False
        return
    user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW
    try:
        yield True
    finally:
        user32.SendMessageW(hwnd, 0x000B, 1, 0)
        # Invalidate and paint this panel and its children together, including
        # native borders; never lock or suspend unrelated desktop windows.
        user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100 | 0x0400)
