"""Durable snapshot plus bounded byte-delta journal for large client state.

The base JSON remains a normal checkpoint. Readers must replay its .wal until a
clean shutdown/compaction. Each journal is bound to the exact base digest; an
atomic replacement (including an external reset) makes an older journal inert.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import time
import uuid
from pathlib import Path

MAGIC = b"K3W1"
DIGEST_BYTES = hashlib.sha256().digest_size
HEADER = len(MAGIC) + DIGEST_BYTES
RECORD_LENGTH = struct.Struct("<I")
DELTA_BOUNDS = struct.Struct("<II")
MIN_RECORD_BODY_BYTES = DIGEST_BYTES + DELTA_BOUNDS.size
MAX_WAL = 4 * 1024 * 1024
GENERATION_ID_HEX_CHARS = 32
DELTA_SCAN_CHUNK_BYTES = 64 * 1024
MIN_JOURNAL_SNAPSHOT_BYTES = 64 * 1024
MAX_JOURNAL_DELTA_RATIO = 0.75
MAX_JOURNAL_RECORDS = 64
REPLACE_ATTEMPTS = 10
REPLACE_RETRY_STEP_SECONDS = 0.05
REPLACE_RETRY_MAX_SECONDS = 0.5
INCREMENTAL = frozenset({"ap_items.json"})
GENERATION_PREFIX = b'{"_kh3_checkpoint_id":"'


def snapshot_bytes(path: Path, value: bytes) -> bytes:
    # A new generation even when state returns to the original bytes prevents
    # an old WAL reactivating after a crash between replace and WAL removal.
    if path.name not in INCREMENTAL or not value.startswith(b"{"):
        return value
    marker = GENERATION_PREFIX + uuid.uuid4().hex.encode("ascii") + b'"'
    return marker + (b"," + value[1:] if value != b"{}" else b"}")


def payload_bytes(base: bytes) -> bytes:
    offset = len(GENERATION_PREFIX) + GENERATION_ID_HEX_CHARS
    if base.startswith(GENERATION_PREFIX) and base[offset:offset + 1] == b'"':
        if base[offset + 1:offset + 2] == b",":
            return b"{" + base[offset + 2:]
        if base[offset + 1:] == b"}":
            return b"{}"
    return base


class JournalError(RuntimeError):
    """Committed recovery data is corrupt; do not grant from a partial history."""


def byte_delta(old: bytes, new: bytes) -> bytes:
    """Scan bounded chunks once, then locate a difference inside one chunk.

    Binary searching the whole history repeatedly copies megabytes; chunking
    keeps comparisons linear and bounds temporary allocations to 64 KiB.
    """
    def edge(limit, reverse=False):
        matched = 0
        while matched < limit:
            size = min(DELTA_SCAN_CHUNK_BYTES, limit - matched)
            if reverse:
                left = old[len(old) - matched - size:len(old) - matched]
                right = new[len(new) - matched - size:len(new) - matched]
            else:
                left, right = old[matched:matched + size], new[matched:matched + size]
            if left == right:
                matched += size
                continue
            lo, hi = 0, size - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                same = left[-mid:] == right[-mid:] if reverse else left[:mid] == right[:mid]
                if same:
                    lo = mid
                else:
                    hi = mid - 1
            return matched + lo
        return matched
    prefix = edge(min(len(old), len(new)))
    suffix = edge(min(len(old), len(new)) - prefix, reverse=True)
    return DELTA_BOUNDS.pack(prefix, suffix) + new[prefix:len(new) - suffix if suffix else len(new)]


def apply_delta(old: bytes, delta: bytes) -> bytes:
    if len(delta) < DELTA_BOUNDS.size:
        raise ValueError("Truncated KH3 checkpoint delta")
    prefix, suffix = DELTA_BOUNDS.unpack_from(delta)
    if prefix + suffix > len(old):
        raise ValueError("Invalid KH3 checkpoint delta bounds")
    return old[:prefix] + delta[DELTA_BOUNDS.size:] + (old[-suffix:] if suffix else b"")


def replace_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(min(REPLACE_RETRY_MAX_SECONDS, REPLACE_RETRY_STEP_SECONDS * (attempt + 1)))
    finally:
        temporary.unlink(missing_ok=True)


class Checkpoint:
    @property
    def value(self):
        if self._value is None:
            self._value = json.dumps(self._payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return self._value

    @value.setter
    def value(self, value):
        self._value, self._payload = value, None

    def __init__(self, path: Path):
        self._initialize(path, path.read_bytes())
        if path.name in INCREMENTAL:
            try:
                data = self.wal.read_bytes()
            except FileNotFoundError:
                return
            self._replay(data)

    @classmethod
    def from_bytes(cls, path: Path, base: bytes, journal: bytes | None = None):
        """Recover exactly the captured pair without rereading a changing disk."""
        checkpoint = cls.__new__(cls)
        checkpoint._initialize(path, base)
        if path.name in INCREMENTAL and journal is not None:
            checkpoint._replay(journal)
        return checkpoint

    def _initialize(self, path: Path, base: bytes):
        self.path = path
        self.wal = path.with_suffix(path.suffix + ".wal")
        self.base = base
        self.value = payload_bytes(self.base)
        self.digest = hashlib.sha256(self.base).digest()
        self.count, self.end = 0, HEADER
        self.active = False

    def _replay(self, data: bytes):
        if data[:HEADER] != MAGIC + self.digest:
            return  # checkpoint won the atomic replacement race, or external reset
        self.active = True
        while self.end + RECORD_LENGTH.size <= len(data):
            size, = RECORD_LENGTH.unpack_from(data, self.end)
            if size < MIN_RECORD_BODY_BYTES or size > MAX_WAL:
                raise JournalError(f"Invalid KH3 checkpoint journal: {self.wal}")
            digest_start = self.end + RECORD_LENGTH.size
            body_start = digest_start + DIGEST_BYTES
            end = digest_start + size
            if end > len(data):
                break  # only an incomplete final append may be discarded
            expected = data[digest_start:body_start]
            try:
                body = data[body_start:end]
                value = apply_delta(self.value, body)
                if hashlib.sha256(value).digest() != expected:
                    raise ValueError("Checksum mismatch")
                self.value = value
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise JournalError(f"Invalid KH3 checkpoint journal: {self.wal}") from exc
            self.end = end
            self.count += 1

    def write(self, value: bytes) -> None:
        if value == self.value:
            return
        delta = byte_delta(self.value, value) if len(value) >= MIN_JOURNAL_SNAPSHOT_BYTES else b""
        size = DIGEST_BYTES + len(delta)
        if (self.path.name not in INCREMENTAL or not delta or size > len(value) * MAX_JOURNAL_DELTA_RATIO
                or self.count >= MAX_JOURNAL_RECORDS or self.end + size + RECORD_LENGTH.size > MAX_WAL):
            self.compact(value)
            return
        # truncate any uncommitted tail before appending. ACK/publication occurs
        # only after this fsync returns successfully.
        self._append(RECORD_LENGTH.pack(size) + hashlib.sha256(value).digest() + delta)
        self.value = value
        self.count += 1
        self.end += RECORD_LENGTH.size + size
        self.active = True


    def _append(self, record):
        with self.wal.open("r+b" if self.active else "wb") as output:
            if not self.active:
                output.write(MAGIC + self.digest)
            output.seek(self.end)
            output.truncate()
            try:
                output.write(record)
                output.flush()
                os.fsync(output.fileno())
            except OSError:
                # Do not let a directory invalidation expose a failed append as
                # committed state while the live receipt remains unacknowledged.
                output.seek(self.end)
                output.truncate()
                output.flush()
                raise

    def compact(self, value: bytes | None = None) -> None:
        value = self.value if value is None else value
        base = snapshot_bytes(self.path, value)
        replace_bytes(self.path, base)
        # After replacement an old WAL cannot match the new checkpoint digest.
        self.base, self.value = base, value
        self.digest = hashlib.sha256(base).digest()
        self.count, self.end, self.active = 0, HEADER, False
        if self.path.name in INCREMENTAL:
            self.wal.unlink(missing_ok=True)


def read_bytes(path: Path) -> bytes:
    return Checkpoint(path).value if path.name in INCREMENTAL else path.read_bytes()
