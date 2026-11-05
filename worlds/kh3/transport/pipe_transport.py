"""Live KH3 IPC over Windows pipes or Linux/Proton local sockets.

Only the registered live session uses this transport. Persistent AP/replay state
is still checkpointed by bridge_support.files; live runtime channels never fall back to
files. The workers own all I/O handles and never call game/client logic.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import threading
from collections import deque
from pathlib import Path

from ..bridge_support.checkpoint import byte_delta, read_bytes
from ..bridge_support.diagnostic_history import NATIVE_HISTORIES, history_for
from ..bridge_support.paths import RUNTIME_KEYBLADE_CHESTS_FILE, RUNTIME_MOM_CLEAR_STATE_FILE, RUNTIME_MUSIC_CHESTS_FILE
from ..bridge_support.state import OWNED, BridgeState
from ..client_support.json_snapshot import JsonArray, JsonObject, encode_json, freeze_json
from .socket_transport import ENDPOINT_FILE, SocketConnection, default_transport, endpoint_from_payload

LOG = logging.getLogger("KH3Pipe")
MAGIC = b"K3P3"
MAX_PACKET = 8 * 1024 * 1024
MAX_RECORDS = 4096
PACKET_PAYLOAD_HEADROOM = 4096
PACKET_FIELDS_LAYOUT = struct.Struct("<QI")
RECORD_HEADER_LAYOUT = struct.Struct("<BHQI")
UINT64_LAYOUT = struct.Struct("<Q")
MAGIC_BYTES = len(MAGIC)
PACKET_HEADER_BYTES = MAGIC_BYTES + PACKET_FIELDS_LAYOUT.size
HANDSHAKE_REPLY_BYTES = MAGIC_BYTES + UINT64_LAYOUT.size
FNV1A_64_OFFSET_BASIS = 14695981039346656037
FNV1A_64_PRIME = 1099511628211
FNV1A_64_MASK = (1 << 64) - 1
ASCII_CASE_OFFSET = ord("a") - ord("A")
HISTORY_PREVIEW_LIMIT = 64
PATCH_MIN_SNAPSHOT_BYTES = 64 * 1024
PATCH_MAX_SIZE_RATIO = 0.75
IO_TIMEOUT_MS = 5000
RECONNECT_DELAY_SECONDS = 0.5
SET, DELETE, ACK, PATCH = 1, 2, 3, 4
# These are durable client-owned snapshots, also sent over the pipe on connect.
PERSISTENT = frozenset({"ap_status.json", "ap_items.json", "game_state.json", RUNTIME_MOM_CLEAR_STATE_FILE})
RECOVERY_COMMANDS = frozenset({"runtime_gameflow_command.json", "runtime_warp_command.json"})
SENSITIVE_COMMANDS = frozenset({"runtime_kh02_launch_response.json"})
COMMANDS = RECOVERY_COMMANDS | SENSITIVE_COMMANDS | frozenset({
    "runtime_item_grant.json", "runtime_item_grant.priority.json",
    "runtime_trap_queue_request.json", "runtime_ap_message.json",
    "runtime_mom_barrier_unlock.json", "runtime_receipt_catalog.json",
    "runtime_deathlink_control.json",
})
EVENTS = frozenset({"runtime_loot_event.json", "runtime_receipt_ack_event.json", "runtime_trap_queue_ack.json",
                    "runtime_notification_event.json"})
JOURNALS = NATIVE_HISTORIES
SNAPSHOTS = frozenset({
    "runtime_kh02_launch_request.json",
    "item_delivery_ledger.json",
    "item_delivery.json", "item_delivery_state.json", "item_delivery_ledger_summary.json", "item_consumer_plan.json",
    "runtime_item_apply_state.json", "runtime_item_grant_state.json", "runtime_item_apply_event.json",
    "runtime_status.json", "runtime_secret_report_hints.json",
    "runtime_item_descriptions.json",
    "runtime_item_grant_readiness.json",
    "runtime_reconciliation_state.json", "runtime_save_receipts.json", "runtime_current_save_slot.json", "runtime_check_state.json",
    "runtime_death_retry_replay_window.json", "runtime_death_retry_baseline.json", "runtime_display_save_slot.json",
    "runtime_space_checks.json", "runtime_vbonus_checks.json", "runtime_event_checks.json", "runtime_chest_checks.json",
    "runtime_gameflow_result.json", "runtime_warp_result.json",
    "runtime_context.json", "runtime_minimap_context.json", "runtime_map_ready.json", "runtime_sora_state.json",
    "runtime_blocking_ui_state.json",
    "runtime_trap_queue_state.json", "runtime_ap_message_result.json",
    "runtime_lucky_emblem_state.json", "runtime_classic_kingdom_state.json",
    "runtime_lucky_mark_checks.json",
    "runtime_level_up_checks.json",
    "runtime_deathlink_state.json",
    "runtime_ingredient_state.json", "runtime_adversary_state.json", "runtime_minigame_rewards.json", "runtime_workshop.json",
    "runtime_olympus_coliseum_state.json",
    "runtime_new_game_staging.json",
    "runtime_kh3_retry_point_event.json", "runtime_kh3_retry_blueprint_event.json",
    "runtime_autosave_probe_save_completed_event.json", "runtime_autosave_probe_file_event.json",
    "runtime_autosave_probe_event.json", "runtime_autosave_probe_auto_save_request_event.json",
    "runtime_mom_barrier_state.json", "runtime_mom_barrier_interaction.json",
    "runtime_music_chests_state.json",
    RUNTIME_MUSIC_CHESTS_FILE, RUNTIME_KEYBLADE_CHESTS_FILE,
})
CHANNELS = PERSISTENT | COMMANDS | EVENTS | SNAPSHOTS | JOURNALS


def pipe_name(root: Path) -> str:
    # Match the native implementation: Windows absolute path, ASCII case fold,
    # FNV-1a over UTF-16LE (including non-ASCII paths without lossy conversion).
    path = str(root.absolute()).replace("/", "\\").rstrip("\\")
    path = "".join(chr(ord(c) + ASCII_CASE_OFFSET) if "A" <= c <= "Z" else c for c in path)
    value = FNV1A_64_OFFSET_BASIS
    for byte in path.encode("utf-16le"):
        value = ((value ^ byte) * FNV1A_64_PRIME) & FNV1A_64_MASK
    return rf"\\.\pipe\KH3AP-v3-{value:016x}"


def encode(cursor: int, records: list[tuple[int, str, int, bytes]]) -> bytes:
    parts = [MAGIC, PACKET_FIELDS_LAYOUT.pack(cursor, len(records))]
    for operation, name, revision, value in records:
        key = name.encode("ascii")
        if name not in CHANNELS or operation not in (SET, DELETE, ACK, PATCH):
            raise ValueError("Invalid KH3 pipe record")
        parts.extend((RECORD_HEADER_LAYOUT.pack(operation, len(key), revision, len(value)), key, value))
    packet = b"".join(parts)
    if len(packet) > MAX_PACKET:
        raise ValueError("KH3 pipe packet exceeds 8 MiB")
    return packet


def decode(packet: bytes) -> tuple[int, list[tuple[int, str, int, bytes]]]:
    if len(packet) < PACKET_HEADER_BYTES or len(packet) > MAX_PACKET or packet[:MAGIC_BYTES] != MAGIC:
        raise ValueError("Incompatible KH3 pipe protocol (expected v3)")
    cursor, count = PACKET_FIELDS_LAYOUT.unpack_from(packet, MAGIC_BYTES)
    if count > MAX_RECORDS:
        raise ValueError("KH3 pipe record limit")
    offset, records = PACKET_HEADER_BYTES, []
    for _ in range(count):
        if offset + RECORD_HEADER_LAYOUT.size > len(packet):
            raise ValueError("Truncated KH3 pipe record")
        operation, key_len, revision, size = RECORD_HEADER_LAYOUT.unpack_from(packet, offset)
        offset += RECORD_HEADER_LAYOUT.size
        end = offset + key_len + size
        if end > len(packet):
            raise ValueError("Truncated KH3 pipe payload")
        name = packet[offset:offset + key_len].decode("ascii")
        if name not in CHANNELS or operation not in (SET, DELETE, ACK):
            raise ValueError("Invalid KH3 pipe channel or operation")
        value = packet[offset + key_len:end]
        if operation == SET:
            json.loads(value)  # reject malformed payloads before publishing any
        records.append((operation, name, revision, value))
        offset = end
    if offset != len(packet):
        raise ValueError("Trailing KH3 pipe bytes")
    return cursor, records


class _WindowsEvent:
    """Manual-reset cancellation event shared by the two overlapped I/O lanes."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        self.api.CreateEventW.restype = wintypes.HANDLE
        self.api.SetEvent.argtypes = (wintypes.HANDLE,)
        self.api.SetEvent.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateEventW(None, True, False, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def set(self):
        self.api.SetEvent(self.handle)

    def is_set(self):
        import _winapi as win
        return win.WaitForSingleObject(self.handle, 0) == 0

    def close(self):
        self.api.CloseHandle(self.handle)


class PipeTransport:
    def __init__(self, root: Path, activity: threading.Event | None = None, notify=None, *, transport=None):
        self.root = root.absolute()
        self.transport = transport or default_transport()
        if self.transport not in {"named_pipe", "local_tcp"}:
            raise ValueError("Unknown KH3 IPC transport")
        self.endpoint = pipe_name(self.root) if self.transport == "named_pipe" else "127.0.0.1 (awaiting probe)"
        self.activity = activity or threading.Event()
        self.notify = notify or (lambda: None)
        self.stop = threading.Event()
        self._outbound = threading.Event()
        self._cancel: _WindowsEvent | SocketConnection | None = None
        self.lock = threading.RLock()
        self.values: dict[str, bytes] = {}
        self._views = {}
        self.revisions: dict[str, int] = {}
        self.events: dict[str, deque[tuple[int | None, int, bytes]]] = {name: deque() for name in EVENTS}
        self.pending: dict[str, tuple[int, str, int, bytes]] = {}
        self.acks: list[tuple[int, str, int, bytes]] = []
        self.connected = False
        self.closed = False
        self.error = "Waiting for updated KH3 probe"
        self.serial = 0
        self.epoch: int | None = None
        self.server_process_id: int | None = None
        self.connection_generation = 0
        self.packets_sent = self.packets_received = 0
        self.history = history_for(self.root)
        self._disconnected_snapshot = None
        self.state = BridgeState(self.root, self._notify)
        self.thread: threading.Thread | None = None
        # Bootstrap only durable AP snapshots. Never ingest old runtime mailboxes.
        for name in PERSISTENT:
            try:
                self.write(name, json.loads(read_bytes(root / name).decode("utf-8-sig")))
            except (OSError, ValueError):
                pass

    def _bump(self, name: str) -> None:
        self.serial += 1
        self.revisions[name] = self.serial

    def _retain_disconnected_snapshots(self):
        """Diagnostic-only last context; never exposed to gameplay readers."""
        from datetime import datetime, timezone
        snapshots = {name: (self.revisions.get(name), value)
                     for name, value in self.values.items() if name in SNAPSHOTS}
        if snapshots:
            self._disconnected_snapshot = (
                datetime.now(timezone.utc).isoformat(), self.epoch, snapshots,
            )

    def diagnostic_snapshot(self):
        """On-demand capture without connecting, draining, or acknowledging IPC."""
        from datetime import datetime, timezone
        with self.lock:
            result = {
                "schema": "kh3ap.pipe_diagnostics.v1",
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "closed" if self.closed else "connected" if self.connected else "disconnected",
                "transport": self.transport, "endpoint": self.endpoint,
                "connected": self.connected, "closed": self.closed,
                "error": self.error, "epoch": self.epoch, "revision": self.serial,
                "packets_sent": self.packets_sent, "packets_received": self.packets_received,
            }
            values = {name: value for name, value in self.values.items() if name not in SENSITIVE_COMMANDS}
            revisions = dict(self.revisions)
            pending = tuple(record for name, record in self.pending.items() if name not in SENSITIVE_COMMANDS)
            acks = tuple(self.acks)
            events = {name: tuple(queue) for name, queue in self.events.items()}
            journals = self.history.snapshot(limit=HISTORY_PREVIEW_LIMIT)
            disconnected = self._disconnected_snapshot
        # All captured payloads are immutable bytes/strings. Decode outside the
        # transport lock so exporting a large history does not stall delivery.
        def decoded(value):
            try:
                return json.loads(value)
            except (ValueError, UnicodeError) as exc:
                return {"decode_error": str(exc), "raw_text": value.decode("utf-8", errors="replace")
                        if isinstance(value, bytes) else value}
        def record(entry):
            operation, name, revision, value = entry
            return {"operation": {SET: "set", DELETE: "delete", ACK: "ack", PATCH: "patch"}.get(operation, operation),
                    "channel": name, "revision": revision, "payload": decoded(value) if value else None}
        result["channels"] = {
            name: {"revision": revisions.get(name), "payload": decoded(value),
                   "kind": "persistent" if name in PERSISTENT else "command" if name in COMMANDS else "snapshot"}
            for name, value in sorted(values.items())
        }
        result["pending_outbound"] = [record(entry) for entry in pending]
        result["pending_acknowledgments"] = [record(entry) for entry in acks]
        result["unacknowledged_events"] = {
            name: [{"epoch": epoch, "revision": revision, "payload": decoded(value)}
                   for epoch, revision, value in queue] for name, queue in events.items()
        }
        result["journal_tails"] = {
            name: {**{key: value for key, value in entry.items() if key != "lines"},
                   "exported_count": len(entry["lines"]), "payloads": [decoded(line) for line in entry["lines"]]}
            for name, entry in journals.items()
        }
        result["last_disconnected_runtime_snapshots"] = None if disconnected is None else {
            "status": "historical_not_live", "captured_at_utc": disconnected[0], "epoch": disconnected[1],
            "channels": {name: {"revision": revision, "payload": decoded(value)}
                         for name, (revision, value) in disconnected[2].items()},
        }
        return result

    def _notify(self):
        self.activity.set()
        self.notify()

    def discard_recovery_commands(self):
        """Operator actions must never be replayed after a transport disconnect."""
        with self.lock:
            for name in RECOVERY_COMMANDS | SENSITIVE_COMMANDS:
                self.values.pop(name, None)
                self.pending.pop(name, None)
                self._views.pop(name, None)
                self._bump(name)

    def read(self, name: str):
        with self.lock:
            value = self.events[name][0][2] if name in EVENTS and self.events[name] else self.values.get(name)
            return json.loads(value) if value is not None else None

    def read_view(self, name: str):
        """Share a parsed immutable snapshot, including the current event head."""
        with self.lock:
            value = self.events[name][0][2] if name in EVENTS and self.events[name] else self.values.get(name)
            cached = self._views.get(name)
            if cached is None or cached[0] is not value:
                cached = self._views[name] = value, freeze_json(json.loads(value)) if value is not None else None
            return cached[1]

    def signature(self, name: str):
        with self.lock:
            if name in JOURNALS:
                return self.history.signature(name)
            value = self.events[name][0][2] if name in EVENTS and self.events[name] else self.values.get(name)
            return (self.revisions.get(name, 0), len(value)) if value is not None else (None, None)

    def write(self, name: str, payload) -> None:
        self.write_encoded(name, encode_json(payload), payload)

    def write_encoded(self, name: str, value: bytes, payload=None) -> None:
        """Publish canonical bytes already encoded by the persistence boundary."""
        if name not in PERSISTENT | COMMANDS:
            raise ValueError(f"KH3 channel is owned by the DLL: {name}")
        if len(value) > MAX_PACKET - PACKET_PAYLOAD_HEADROOM:
            raise ValueError("KH3 pipe payload exceeds packet limit")
        with self.lock:
            if name in SENSITIVE_COMMANDS and (not self.connected or self.closed):
                raise ValueError("Epic launch reply requires a live KH3 connection")
            if self.values.get(name) == value:
                return
            self.values[name] = value
            if isinstance(payload, (JsonObject, JsonArray)):
                self._views[name] = value, payload
            self._bump(name)
            self.pending[name] = (SET, name, self.revisions[name], value)
            self._outbound.set()
            if name == "game_state.json":
                self.notify()

    def delete(self, name: str) -> bool:
        if name in {RUNTIME_MUSIC_CHESTS_FILE, RUNTIME_KEYBLADE_CHESTS_FILE}:
            raise ValueError(f"KH3 channel is owned by the DLL: {name}")
        with self.lock:
            if name in EVENTS:
                if not self.events[name]:
                    return False
                epoch, revision, _ = self.events[name].popleft()
                if epoch == self.epoch:
                    self.acks.append((ACK, name, revision, b""))
                if self.events[name]:
                    self._notify()
            else:
                old = self.values.pop(name, None)
                self.pending[name] = (DELETE, name, 0, old or b"")
            self._bump(name)
            self._outbound.set()
            return True

    def acknowledge(self, name: str, expected=None) -> None:
        with self.lock:
            if expected is None or self.read(name) == expected:
                self.delete(name)

    def accept(self, records) -> None:
        with self.lock:
            for operation, name, revision, value in records:
                if name in JOURNALS:
                    payload = json.loads(value)
                    identity = [self.epoch, revision]
                    recent = self.history.lines(name, 1)
                    previous = json.loads(recent[-1]) if recent else {}
                    if previous.get("_pipe_receipt") != identity:
                        payload["_pipe_receipt"] = identity
                        # Gameplay observers run in the DLL before publication.
                        # Retain diagnostic evidence in RAM and ACK without I/O.
                        self.history.append(name, payload)
                        self._bump(name)
                        self._notify()
                    self.acks.append((ACK, name, revision, b""))
                    self._outbound.set()
                elif name in EVENTS:
                    # The native side retains an event until the bridge has
                    # persisted its effects and explicitly acknowledges it.
                    if not any(epoch == self.epoch and seq == revision for epoch, seq, _ in self.events[name]):
                        self.events[name].append((self.epoch, revision, value))
                        self._bump(name)
                        self._notify()
                elif operation == DELETE:
                    if not value or self.values.get(name) == value:
                        self.values.pop(name, None)
                        self._bump(name)
                        self._notify()
                elif name not in self.pending and self.values.get(name) != value:
                    self.values[name] = value
                    self._bump(name)
                    self._notify()

    def lines(self, name: str, limit: int | None = None) -> tuple[str, ...]:
        return self.history.lines(name, limit)

    def start(self):
        if self.transport == "named_pipe" and os.name != "nt":
            raise RuntimeError("KH3 live IPC requires a Windows named pipe")
        self.state.start()
        self.thread = threading.Thread(target=self._run, name="KH3IPC", daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        with self.lock:
            if self._cancel:
                self._cancel.set()
            self._outbound.set()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                raise RuntimeError("KH3 named pipe worker did not stop")
        self.state.close()
        with self.lock:
            self.connected, self.closed = False, True
            self.server_process_id = None
            self.discard_recovery_commands()
            self._retain_disconnected_snapshots()
            for name in SNAPSHOTS:
                self.values.pop(name, None)
                self._bump(name)

    def _io(self, handle, cancel, data=None, *, timeout=None):
        if isinstance(cancel, SocketConnection):
            return cancel.io(data, timeout=timeout)
        import _winapi as win
        ov, error = (win.ReadFile(handle, MAX_PACKET, overlapped=True) if data is None
                     else win.WriteFile(handle, data, overlapped=True))
        try:
            if error == win.ERROR_IO_PENDING:
                result = win.WaitForMultipleObjects(
                    [cancel.handle, ov.event], False, win.INFINITE if timeout is None else timeout)
                if result == 0:
                    raise OSError("KH3 pipe connection stopped")
                if result != 1:
                    raise TimeoutError("KH3 pipe I/O timed out")
            size, error = ov.GetOverlappedResult(False)
            if error:
                raise OSError(error, "KH3 pipe I/O failed")
            if data is not None and size != len(data):
                raise OSError("Short KH3 pipe write")
            return bytes(ov.getbuffer()) if data is None else None
        finally:
            ov.cancel()
            ov.GetOverlappedResult(True)

    def _send(self, handle, cancel, errors):
        bases = {}  # connection-local; reconnect always begins with full SETs
        try:
            while not self.stop.is_set():
                self._outbound.wait()
                if cancel.is_set():
                    return
                with self.lock:
                    # Clear under the producer lock before inspecting queues so
                    # an update during the write cannot lose its wakeup.
                    self._outbound.clear()
                    sent_acks = self.acks[:MAX_RECORDS]
                    outgoing = list(sent_acks)
                    size = len(encode(0, outgoing))
                    sent = {}
                    for name, record in self.pending.items():
                        wire_record = record
                        if record[0] == SET and name in PERSISTENT and name in bases and len(record[3]) >= PATCH_MIN_SNAPSHOT_BYTES:
                            base_revision, base = bases[name]
                            delta = UINT64_LAYOUT.pack(base_revision) + byte_delta(base, record[3])
                            if len(delta) < len(record[3]) * PATCH_MAX_SIZE_RATIO:
                                wire_record = (PATCH, name, record[2], delta)
                        record_size = RECORD_HEADER_LAYOUT.size + len(name) + len(wire_record[3])
                        if size + record_size > MAX_PACKET or len(outgoing) == MAX_RECORDS:
                            break
                        sent[name] = record
                        outgoing.append(wire_record)
                        size += record_size
                if not outgoing:
                    continue
                self._io(handle, cancel, encode(0, outgoing), timeout=IO_TIMEOUT_MS)
                with self.lock:
                    self.packets_sent += 1
                    for name, record in sent.items():
                        if record[0] == SET and name not in SENSITIVE_COMMANDS:
                            bases[name] = record[2], record[3]
                        elif record[0] == DELETE:
                            bases.pop(name, None)
                        if self.pending.get(name) == record:
                            del self.pending[name]
                            if name in SENSITIVE_COMMANDS:
                                self.values.pop(name, None)
                                self._views.pop(name, None)
                    del self.acks[:len(sent_acks)]
                    if self.pending or self.acks:
                        self._outbound.set()
        except (OSError, ValueError) as exc:
            errors.append(exc)
        finally:
            cancel.set()

    def _run(self):
        if self.transport == "named_pipe":
            import _winapi as win
        while not self.stop.is_set():
            handle = None
            sender = None
            cancel = _WindowsEvent() if self.transport == "named_pipe" else SocketConnection()
            errors = []
            try:
                with self.lock:
                    self._cancel = cancel
                    if self.stop.is_set():
                        cancel.set()
                token = b""
                server_process_id = None
                if self.transport == "named_pipe":
                    handle = win.CreateFile(self.endpoint, win.GENERIC_READ | win.GENERIC_WRITE,
                                            0, 0, win.OPEN_EXISTING, win.FILE_FLAG_OVERLAPPED, 0)
                    win.SetNamedPipeHandleState(handle, win.PIPE_READMODE_MESSAGE, None, None)
                else:
                    descriptor = self.state.read(ENDPOINT_FILE)
                    address, token = endpoint_from_payload(descriptor)
                    server_process_id = descriptor.get("pid")
                    if type(server_process_id) is not int or server_process_id <= 0:
                        raise ValueError("Invalid KH3 probe process identity")
                    self.endpoint = f"{address[0]}:{address[1]}"
                    cancel.connect(address)
                self._io(handle, cancel, MAGIC + token, timeout=IO_TIMEOUT_MS)
                hello = self._io(handle, cancel, timeout=IO_TIMEOUT_MS)
                if len(hello) != HANDSHAKE_REPLY_BYTES or hello[:MAGIC_BYTES] != MAGIC:
                    raise ValueError("KH3 probe does not support pipe protocol v3")
                epoch, = UINT64_LAYOUT.unpack(hello[MAGIC_BYTES:])
                with self.lock:
                    if self.epoch != epoch:
                        self.acks.clear()
                    self.epoch = epoch
                    self.server_process_id = server_process_id
                    self.connection_generation += 1
                    self.pending.update({name: (SET, name, self.revisions[name], value) for name, value in self.values.items()
                                         if name in PERSISTENT | COMMANDS})
                    self.connected, self.error = True, ""
                    self._outbound.set()
                LOG.info("KH3 %s connected: %s", self.transport, self.endpoint)
                self._notify()
                sender = threading.Thread(target=self._send, args=(handle, cancel, errors),
                                          name="KH3IPCSender", daemon=True)
                sender.start()
                while not self.stop.is_set():
                    _, records = decode(self._io(handle, cancel))
                    with self.lock:
                        self.packets_received += 1
                        self.accept(records)
            except (OSError, ValueError) as connection_error:
                exc = errors[0] if errors else connection_error
                if self.connected:
                    LOG.warning("KH3 %s disconnected: %s", self.transport, exc)
                with self.lock:
                    self.connected, self.error = False, str(exc)
                    self.server_process_id = None
                    self.discard_recovery_commands()
                    self._retain_disconnected_snapshots()
                    # Do not grant against a disconnected/stale pawn snapshot.
                    for name in SNAPSHOTS:
                        self.values.pop(name, None)
                        self.pending.pop(name, None)
                        self._bump(name)
                self._notify()
            finally:
                cancel.set()
                self._outbound.set()
                if sender:
                    sender.join()
                if handle is not None:
                    win.CloseHandle(handle)
                with self.lock:
                    self._cancel = None
                    cancel.close()
            self.stop.wait(RECONNECT_DELAY_SECONDS)


_registry: dict[str, PipeTransport] = {}
_registry_lock = threading.RLock()


def state_for(path: Path) -> BridgeState | None:
    if path.name not in OWNED | PERSISTENT:
        return None
    with _registry_lock:
        transport = _registry.get(os.path.normcase(str(path.parent.absolute())))
        return transport.state if transport and not transport.closed else None


def transport_for(path: Path) -> PipeTransport | None:
    if path.name not in CHANNELS:
        return None
    with _registry_lock:
        transport = _registry.get(os.path.normcase(str(path.parent.absolute())))
        # Closed registrations fence stale native mailboxes, but client-owned
        # checkpoints must be read from disk again. A deleted/recreated run can
        # reuse this path before its replacement live transport is registered.
        if transport and transport.closed and path.name in PERSISTENT:
            return None
        return transport


def register(root: Path, activity: threading.Event | None = None, notify=None, *, transport=None) -> PipeTransport:
    key = os.path.normcase(str(root.absolute()))
    with _registry_lock:
        if key in _registry and not _registry[key].closed:
            raise RuntimeError(f"KH3 pipe already registered for {root}")
        transport = PipeTransport(root, activity, notify, transport=transport)
        _registry[key] = transport
        return transport


def unregister(transport: PipeTransport) -> None:
    transport.close()
    # Leave a disconnected registration until the session is replaced. Readers
    # still running during shutdown must never fall back to stale runtime files.
