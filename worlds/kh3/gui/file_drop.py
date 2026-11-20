# SPDX-License-Identifier: GPL-3.0-only
"""Receive desktop file drops on a Tk widget."""

import sys
import tkinter as tk


def bind_file_drop(widget, receive):
    if sys.platform != "win32":
        try:
            widget.tk.call("package", "require", "tkdnd")
        except tk.TclError:
            return False
        widget.tk.call("tkdnd::drop_target", "register", widget._w, "DND_Files")

        def drop(data):
            receive(widget.tk.splitlist(data))
            return "copy"

        widget.tk.call("bind", widget._w, "<<Drop:DND_Files>>", (widget.register(drop), "%D"))
        return True

    # Stock Windows Tk has no tkdnd extension. Explorer can deliver WM_DROPFILES
    # directly to the window, including multiple Unicode paths in one drop.
    import ctypes as ct
    from collections import deque
    from ctypes import wintypes as wt
    from threading import Thread

    shell = ct.WinDLL("shell32")
    controls = ct.WinDLL("comctl32")
    proc_type = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                               ct.c_size_t, ct.c_size_t)
    controls.SetWindowSubclass.argtypes = [wt.HWND, proc_type, ct.c_size_t, ct.c_size_t]
    controls.RemoveWindowSubclass.argtypes = [wt.HWND, proc_type, ct.c_size_t]
    controls.DefSubclassProc.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    controls.DefSubclassProc.restype = ct.c_ssize_t
    shell.DragAcceptFiles.argtypes = [wt.HWND, wt.BOOL]
    shell.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
    shell.DragQueryFileW.restype = wt.UINT
    shell.DragFinish.argtypes = [wt.HANDLE]

    pending = deque()

    def dispatch(_):
        while pending:
            receive(pending.popleft())

    def wake_tk():
        # Calling Tk directly from the native window procedure corrupts
        # _tkinter's saved thread state. From a worker, Tkinter marshals the
        # call onto its event loop. Destroying the modal discards its events.
        try:
            widget.event_generate("<<KH3FileDrop>>", when="tail")
        except (tk.TclError, RuntimeError):
            pass  # Window or main loop already closed.

    @proc_type
    def procedure(hwnd, message, wparam, lparam, subclass_id, data):
        if message == 0x0233:  # WM_DROPFILES
            paths = []
            try:
                for index in range(shell.DragQueryFileW(wparam, 0xffffffff, None, 0)):
                    size = shell.DragQueryFileW(wparam, index, None, 0) + 1
                    buffer = ct.create_unicode_buffer(size)
                    if shell.DragQueryFileW(wparam, index, buffer, size):
                        paths.append(buffer.value)
            finally:
                shell.DragFinish(wparam)
            pending.append(paths)
            Thread(target=wake_tk, daemon=True).start()
            return 0
        return controls.DefSubclassProc(hwnd, message, wparam, lparam)

    hwnd = widget.winfo_id()
    if not controls.SetWindowSubclass(hwnd, procedure, 1, 0):
        return False
    shell.DragAcceptFiles(hwnd, True)

    def unbind(event):
        if event.widget is widget:
            shell.DragAcceptFiles(hwnd, False)
            controls.RemoveWindowSubclass(hwnd, procedure, 1)
            pending.clear()

    # Tk retains the callback (and therefore the native procedure) until destroy.
    widget.bind("<Destroy>", unbind, add="+")
    widget.bind("<<KH3FileDrop>>", dispatch, add="+")
    return True
