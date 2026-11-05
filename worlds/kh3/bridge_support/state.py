"""Revisioned client-owned state, invalidated by OS directory events.

Native mailboxes are deliberately excluded. No polling thread, and no path stat
on a cache hit. Mutable JSON copies prevent readers from modifying shared state.
"""
from __future__ import annotations

import copy
import json
import struct
import sys
import threading
from pathlib import Path

from ..client_support.json_snapshot import JsonArray, JsonObject, encode_json, freeze_json
from .checkpoint import INCREMENTAL, Checkpoint, replace_bytes, snapshot_bytes

OWNED = frozenset({
    "ap_client_command.json",
    "deathlink_settings.json", "deathlink_network_state.json",
    "bridge_runtime_status.json", "bridge_notifications.json", "slot_data.json",
})


def fingerprint(path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


class BridgeState:
    def __init__(self, root: Path, notify):
        self.root, self.notify = root, notify
        self.lock = threading.RLock()
        self.entries = {}
        self._views = {}
        self.checkpoints = {}
        self.dirty = set()
        self.serial = 0
        self.thread = None
        self.handle = None
        self.stopping = threading.Event()
        self.failed = False
        self._linux_watcher = None

    def _load(self, name):
        entry = self.entries.get(name)
        if name in self.dirty or self.failed:
            self.dirty.discard(name)
            if entry is not None and entry[2] != self._fingerprint(name):
                self.entries.pop(name, None)
                self.checkpoints.pop(name, None)
                entry = None
        if entry is None:
            # Fingerprint before reading so a racing external replacement causes
            # another invalidation, never an old payload with a new identity.
            identity = self._fingerprint(name)
            try:
                checkpoint = Checkpoint(self.root / name)
                value = checkpoint.value
                self.checkpoints[name] = checkpoint
            except FileNotFoundError:
                value = None
            self.serial += 1
            entry = value, self.serial, identity
            self.entries[name] = entry
        return entry

    def _fingerprint(self, name):
        base = fingerprint(self.root / name)
        return (base, fingerprint(self.root / (name + ".wal"))) if name in INCREMENTAL else base

    def read(self, name):
        with self.lock:
            value = self._load(name)[0]
            from ..RuntimeSeed import expand_document
            if isinstance(value, dict):
                return expand_document(name, copy.deepcopy(value))
            return expand_document(name, json.loads(value)) if value is not None else None

    def signature(self, name):
        with self.lock:
            value, revision, _ = self._load(name)
            return (revision, len(value)) if value is not None else (None, None)

    def read_view(self, name):
        with self.lock:
            value, revision, _ = self._load(name)
            cached = self._views.get(name)
            if cached is None or cached[0] != revision:
                from ..RuntimeSeed import expand_document
                view = freeze_json(expand_document(name, value if isinstance(value, dict) else json.loads(value))) if value is not None else None
                cached = self._views[name] = revision, view
            return cached[1]

    def write(self, name, payload):
        value = encode_json(payload)
        with self.lock:
            try:
                old = self._load(name)[0]
            except (UnicodeError, json.JSONDecodeError):
                old = None
                self.checkpoints.pop(name, None)
            if old == value:
                return value
            checkpoint = self.checkpoints.get(name)
            if checkpoint is None:
                replace_bytes(self.root / name, snapshot_bytes(self.root / name, value))
                # An external deletion followed by recreation must not reactivate
                # a journal from the deleted generation.
                if name in INCREMENTAL:
                    (self.root / (name + ".wal")).unlink(missing_ok=True)
                checkpoint = Checkpoint(self.root / name)
                self.checkpoints[name] = checkpoint
            else:
                checkpoint.write(value)
            self.serial += 1
            self.entries[name] = value, self.serial, self._fingerprint(name)
            if isinstance(payload, (JsonObject, JsonArray)):
                self._views[name] = self.serial, payload
            return value

    def delete(self, name):
        with self.lock:
            try:
                (self.root / name).unlink()
            except FileNotFoundError:
                existed = False
            else:
                existed = True
            if name in INCREMENTAL:
                (self.root / (name + ".wal")).unlink(missing_ok=True)
            self.checkpoints.pop(name, None)
            self.serial += 1
            self.entries[name] = None, self.serial, self._fingerprint(name)
            return existed


    def start(self):
        # Opening before starting the thread avoids missing the initial change.
        self.root.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("linux"):
            from ..transport.linux_directory_watcher import LinuxDirectoryWatcher
            self._linux_watcher = LinuxDirectoryWatcher(self.root, self._invalidate, self._watch_failed)
            self.thread = self._linux_watcher.thread
            return
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
        api.CreateFileW.restype = w.HANDLE
        api.ReadDirectoryChangesW.argtypes = (w.HANDLE, w.LPVOID, w.DWORD, w.BOOL, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID, w.LPVOID)
        api.ReadDirectoryChangesW.restype = w.BOOL
        api.CloseHandle.argtypes = (w.HANDLE,)
        api.CancelIoEx.argtypes = (w.HANDLE, w.LPVOID)
        api.GetOverlappedResult.argtypes = (w.HANDLE, w.LPVOID, ctypes.POINTER(w.DWORD), w.BOOL)
        self.handle = api.CreateFileW(str(self.root), 1, 7, None, 3, 0x42000000, None)
        if self.handle == w.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.api = api
        self.thread = threading.Thread(target=self._watch, name="KH3StateChanges", daemon=True)
        self.thread.start()

    def _watch(self):
        import ctypes
        from ctypes import wintypes as w
        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]
        buffer = ctypes.create_string_buffer(65536)
        try:
            while not self.stopping.is_set():
                ov, size = Overlapped(), w.DWORD()
                if not self.api.ReadDirectoryChangesW(self.handle, buffer, len(buffer), False, 0x19,
                                                      None, ctypes.byref(ov), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if self.stopping.is_set():
                    self.api.CancelIoEx(self.handle, ctypes.byref(ov))
                if not self.api.GetOverlappedResult(self.handle, ctypes.byref(ov), ctypes.byref(size), True):
                    if self.stopping.is_set():
                        break
                    raise ctypes.WinError(ctypes.get_last_error())
                names = set()
                position = 0
                while position < size.value:
                    following, _, length = struct.unpack_from("<III", buffer, position)
                    name = buffer[position + 12:position + 12 + length].decode("utf-16le")
                    names.add(name.lower().removesuffix(".wal"))
                    if not following:
                        break
                    position += following
                self._invalidate(names, not size.value)
                # Consumers already wake for native/AP traffic. External state
                # edits are visible on their next normal cycle; own checkpoint
                # writes must not trigger an endless reconciliation wake loop.
        except OSError:
            # Preserve correctness if notifications fail: foreground accesses
            # fall back to metadata validation until the session is restarted.
            self._watch_failed()

    def _invalidate(self, names, overflow=False):
        with self.lock:
            self.dirty.update(self.entries if overflow else names & self.entries.keys())

    def _watch_failed(self):
        with self.lock:
            self.failed = True
        self.notify()

    def close(self):
        self.stopping.set()
        if self._linux_watcher is not None:
            self._linux_watcher.close()
            self._linux_watcher = self.thread = None
        elif self.thread:
            self.api.CancelIoEx(self.handle, None)
            self.thread.join(3)
            if self.thread.is_alive():
                raise RuntimeError("KH3 state watcher did not stop")
            self.api.CloseHandle(self.handle)
            self.thread = None
        with self.lock:
            self.dirty.update(self.entries)
            # A recovered persistent pipe snapshot may never be rewritten during
            # this session. Compact its existing journal on clean shutdown too.
            for name in INCREMENTAL:
                self._load(name)
            for name in tuple(self.checkpoints):
                self._load(name)  # honor external replacement before compaction
                checkpoint = self.checkpoints.get(name)
                if checkpoint and checkpoint.count:
                    checkpoint.compact()
                    value, revision, _ = self.entries[name]
                    self.entries[name] = value, revision, self._fingerprint(name)
