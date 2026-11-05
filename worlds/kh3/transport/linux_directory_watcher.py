"""Blocking inotify notifications for bridge checkpoints; no polling timer."""
from __future__ import annotations

import ctypes
import os
import selectors
import struct
import threading


class LinuxDirectoryWatcher:
    def __init__(self, root, invalidate, failed):
        self.invalidate, self.failed = invalidate, failed
        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1.argtypes = (ctypes.c_int,)
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
        libc.inotify_add_watch.restype = ctypes.c_int
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "Could not initialize KH3 inotify")
        self.wake_read = self.wake_write = None
        try:
            # Modify/attrib/close-write, rename/create/delete and directory loss.
            if libc.inotify_add_watch(self.fd, os.fsencode(root), 0xFCE) < 0:
                raise OSError(ctypes.get_errno(), "Could not watch KH3 bridge directory")
            self.wake_read, self.wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            self.thread = threading.Thread(target=self._watch, name="KH3StateChanges", daemon=True)
            self.thread.start()
        except BaseException:
            self._close_fds()
            raise

    def _watch(self):
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.fd, selectors.EVENT_READ)
                selector.register(self.wake_read, selectors.EVENT_READ)
                while True:
                    for key, _ in selector.select():
                        if key.fd == self.wake_read:
                            return
                        try:
                            data = os.read(self.fd, 65536)
                        except BlockingIOError:
                            continue
                        names, overflow = set(), False
                        offset = 0
                        while offset < len(data):
                            _, mask, _, size = struct.unpack_from("=iIII", data, offset)
                            name = os.fsdecode(data[offset + 16:offset + 16 + size].split(b"\0", 1)[0])
                            offset += 16 + size
                            if mask & (0x400 | 0x800 | 0x8000):  # directory removed/moved/watch lost
                                raise OSError("KH3 bridge directory watch was lost")
                            overflow |= bool(mask & 0x4000)
                            if name:
                                names.add(name.removesuffix(".wal"))
                        self.invalidate(names, overflow)
        except (OSError, ValueError):
            self.failed()

    def _close_fds(self):
        for fd in (self.fd, self.wake_read, self.wake_write):
            if fd is not None:
                os.close(fd)

    def close(self):
        os.write(self.wake_write, b"x")
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError("KH3 inotify watcher did not stop")
        self._close_fds()
