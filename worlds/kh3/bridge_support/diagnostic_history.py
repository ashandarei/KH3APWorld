"""Bounded, process-local evidence; written only when exporting a debug bundle.

These histories are not recovery checkpoints. Never replay old disk traces into
live state or require a successful disk write before processing a runtime event.
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from functools import lru_cache
from itertools import islice
from pathlib import Path

NATIVE_HISTORIES = frozenset({
    "runtime_fight_events.jsonl", "current_state_write_polyhook_events.jsonl",
    "runtime_autosave_probe_file_events.jsonl", "runtime_autosave_probe_auto_save_request_events.jsonl",
    "runtime_autosave_probe_save_completed_events.jsonl", "lucky_mark_native_ap_resolver_diagnostics.jsonl",
})
CLIENT_HISTORIES = frozenset({
    "location_check_transport.jsonl", "runtime_grant_profile.jsonl", "runtime_item_latency_trace.jsonl",
})
HISTORIES = NATIVE_HISTORIES | CLIENT_HISTORIES
MAX_RECORDS = 4096
MAX_BYTES = 2 * 1024 * 1024  # Per history, including large location-check snapshots.
_registry_lock = threading.Lock()


class DiagnosticHistory:
    def __init__(self):
        self.lock = threading.RLock()
        self.records = {name: deque() for name in HISTORIES}
        self.sizes = dict.fromkeys(HISTORIES, 0)
        self.revisions = dict.fromkeys(HISTORIES, 0)
        self.dropped = dict.fromkeys(HISTORIES, 0)

    def append(self, name: str, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        size = len(line.encode("utf-8"))
        with self.lock:
            self.revisions[name] += 1
            if size > MAX_BYTES:
                self.dropped[name] += 1
                return
            records = self.records[name]
            while records and (len(records) >= MAX_RECORDS or self.sizes[name] + size > MAX_BYTES):
                self.sizes[name] -= records.popleft()[1]
                self.dropped[name] += 1
            records.append((line, size))
            self.sizes[name] += size

    def lines(self, name: str, limit: int | None = None) -> tuple[str, ...]:
        with self.lock:
            records = self.records[name]
            if limit:
                return tuple(line for line, _ in reversed(tuple(islice(reversed(records), limit))))
            return tuple(line for line, _ in records)

    def signature(self, name: str) -> tuple[int, int]:
        with self.lock:
            return self.revisions[name], self.sizes[name]

    def snapshot(self, limit: int | None = None) -> dict:
        with self.lock:
            return {name: {"revision": self.revisions[name], "retained_count": len(self.records[name]),
                           "retained_bytes": self.sizes[name], "dropped_count": self.dropped[name],
                           "lines": self.lines(name, limit)} for name in sorted(HISTORIES)}


@lru_cache(maxsize=8)
def _history(key: str) -> DiagnosticHistory:
    return DiagnosticHistory()


def history_for(root: Path) -> DiagnosticHistory:
    # No resolve/stat/open here: logging must also work during storage failures.
    with _registry_lock:
        return _history(os.path.normcase(str(root.absolute())))
