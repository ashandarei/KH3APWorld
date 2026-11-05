"""Framed local TCP for native Linux clients talking to the probe under Proton.

The startup descriptor is discovery metadata, cached via directory events. All
live traffic uses the same v3 messages and receipt rules as Windows named pipes.
"""
from __future__ import annotations

import errno
import os
import select
import socket
import struct
import threading
import time

ENDPOINT_FILE = "ipc_endpoint.json"
ENDPOINT_LOCK = "ipc_endpoint.lock"
MAX_PACKET = 8 * 1024 * 1024


def default_transport():
    if os.name != "nt":
        return "local_tcp"
    # Windows Python under Wine must select the same transport as the probe.
    import ctypes
    return "local_tcp" if hasattr(ctypes.WinDLL("ntdll"), "wine_get_version") else "named_pipe"


def endpoint_from_payload(payload):
    if not isinstance(payload, dict) or payload.get("protocol") != "KH3AP-v3":
        raise ValueError("Waiting for updated KH3 Proton probe endpoint")
    port, token = payload.get("port"), payload.get("token")
    if (payload.get("host") != "127.0.0.1" or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(token, str) or len(token) != 64
            or any(c not in "0123456789abcdef" for c in token)):
        raise ValueError("Invalid KH3 local endpoint")
    return ("127.0.0.1", port), token.encode("ascii")


class SocketConnection:
    """Independent reader/writer lanes with interruptible waits and bounded frames."""
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setblocking(False)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.wake_read, self.wake_write = socket.socketpair()
        self.wake_write.setblocking(False)
        self.cancelled = threading.Event()

    def set(self):
        self.cancelled.set()
        try:
            self.wake_write.send(b"x")
        except (BlockingIOError, OSError):
            pass

    def is_set(self):
        return self.cancelled.is_set()

    def _wait(self, write, deadline):
        if self.is_set():
            raise OSError("KH3 local connection stopped")
        timeout = None if deadline is None else max(0, deadline - time.monotonic())
        read, writable, _ = select.select(
            [self.wake_read] if write else [self.wake_read, self.socket],
            [self.socket] if write else [], [], timeout)
        if self.wake_read in read or self.is_set():
            raise OSError("KH3 local connection stopped")
        if not read and not writable:
            raise TimeoutError("KH3 local connection I/O timed out")

    def connect(self, address):
        result = self.socket.connect_ex(address)
        if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035):
            raise OSError(result, "KH3 probe is not listening")
        if result:
            self._wait(True, time.monotonic() + 5)
            error = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if error:
                raise OSError(error, "KH3 probe connection failed")

    def _receive(self, size, deadline):
        parts = bytearray()
        while len(parts) < size:
            self._wait(False, deadline)
            try:
                part = self.socket.recv(size - len(parts))
            except BlockingIOError:
                continue
            if not part:
                raise OSError("KH3 probe closed the local connection")
            parts.extend(part)
            # Idle reads have no timer; once a frame begins it must finish.
            if deadline is None:
                deadline = time.monotonic() + 5
        return bytes(parts), deadline

    def io(self, data=None, *, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout / 1000
        if data is None:
            header, deadline = self._receive(4, deadline)
            size, = struct.unpack("<I", header)
            if not 1 <= size <= MAX_PACKET:
                raise ValueError("KH3 local frame exceeds size limit")
            return self._receive(size, deadline)[0]
        if not 1 <= len(data) <= MAX_PACKET:
            raise ValueError("KH3 local frame exceeds size limit")
        frame = memoryview(struct.pack("<I", len(data)) + data)
        while frame:
            self._wait(True, deadline)
            try:
                sent = self.socket.send(frame)
            except BlockingIOError:
                continue
            if not sent:
                raise OSError("KH3 local connection closed during write")
            frame = frame[sent:]

    def close(self):
        # Called only after both lanes have drained their cancellation wakeup.
        self.socket.close()
        self.wake_read.close()
        self.wake_write.close()
