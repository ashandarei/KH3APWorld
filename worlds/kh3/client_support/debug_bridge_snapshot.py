"""On-demand, read-only bridge capture for connected and offline debug bundles."""
from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from ..bridge_support.checkpoint import INCREMENTAL, Checkpoint, JournalError
from ..bridge_support.native_delivery import SCHEMAS as NATIVE_RECEIPT_SNAPSHOTS
from ..bridge_support.state import OWNED
from ..transport.pipe_transport import COMMANDS, EVENTS, PERSISTENT, SNAPSHOTS, transport_for

CHECKPOINT_NAMES = OWNED | PERSISTENT | INCREMENTAL | set(NATIVE_RECEIPT_SNAPSHOTS) | {
    "item_delivery_ledger.json",
    "vbonus_progress.json", "event_progress.json", "chest_progress.json", "lucky_mark_progress.json",
    "lucky_emblem_progress.json",
    "level_up_progress.json",
    "runtime_native_reconciliation.json",
    "runtime_native_deathlink.json",
    "runtime_seed_settings.json", "runtime_seed_hints.json",
}
CHECKPOINT_FILES = CHECKPOINT_NAMES | {name + ".wal" for name in INCREMENTAL}
LEGACY_MAILBOX_NAMES = COMMANDS | EVENTS | SNAPSHOTS


def _identity(path: Path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def _capture_checkpoint(root: Path, name: str):
    path = root / name
    paths = (path, root / (name + ".wal")) if name in INCREMENTAL else (path,)
    raw, identities, error = {}, (), None
    for _ in range(3):
        raw = {}
        try:
            if any(candidate.is_symlink() for candidate in paths):
                raise OSError("Checkpoint symlinks are excluded")
            identities = tuple(_identity(candidate) for candidate in paths)
            for candidate, identity in zip(paths, identities):
                if identity is not None:
                    raw[candidate.name] = candidate.read_bytes()
            if identities != tuple(_identity(candidate) for candidate in paths):
                error = "Checkpoint changed during capture"
                continue
            error = None
            break
        except OSError as exc:
            error = str(exc)
    details = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
               "raw_files": list(raw), "status": "unavailable"}
    if error is not None:
        details["error"] = error
        return raw, None, details
    if name not in raw:
        details["status"] = "missing" if not raw else "orphan_journal"
        return raw, None, details
    try:
        journal = raw.get(name + ".wal")
        checkpoint = Checkpoint.from_bytes(path, raw[name], journal)
        value = checkpoint.value
        json.loads(value)  # refuse to label invalid JSON as recovered state
        details.update(status="recovered", recovered_records=checkpoint.count,
                       recovered_bytes=len(value), journal_status=(
                           "absent" if journal is None else "stale_ignored" if not checkpoint.active
                           else "incomplete_tail_ignored" if checkpoint.end < len(journal) else "replayed"))
        return raw, value, details
    except (JournalError, ValueError, UnicodeError) as exc:
        details.update(status="invalid", error=str(exc))
        return raw, None, details


def capture_bridge_debug_state(bridge_dir: Path | None):
    """Capture without starting a pipe, acknowledging events, or compacting files.

    A registered writer's state lock protects each checkpoint pair. Offline or
    external writers are checked with before/after identities and bounded retry.
    Pipe and checkpoint captures have separate timestamps; this is not a paused
    game or a transaction spanning every subsystem.
    """
    report = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "files": {},
              "consistency": "per_checkpoint_pair; pipe snapshot captured separately"}
    pipe = transport_for(bridge_dir / "runtime_context.json") if bridge_dir is not None else None
    if pipe is not None:
        pipe_snapshot = pipe.diagnostic_snapshot()
    else:
        pipe_snapshot = {"schema": "kh3ap.pipe_diagnostics.v1", "captured_at_utc": report["captured_at_utc"],
                         "status": "unavailable", "connected": False,
                         "reason": "no_bridge_directory" if bridge_dir is None else "no_registered_session",
                         "note": "Disk mailboxes do not establish live pipe state."}
    raw, recovered = {}, {}
    if bridge_dir is not None:
        for name in sorted(CHECKPOINT_NAMES):
            with pipe.state.lock if pipe is not None else nullcontext():
                files, value, details = _capture_checkpoint(bridge_dir, name)
            raw.update(files)
            # Presentation views are reconstructed by the DLL and live only in
            # IPC. Prefer the captured view over any stale pre-upgrade disk copy.
            snapshot = pipe_snapshot.get("channels", {}).get(name) if name in SNAPSHOTS else None
            if snapshot is not None:
                value = json.dumps(snapshot["payload"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                details.update(status="captured", source="pipe_snapshot", recovered_bytes=len(value))
            report["files"][name] = details
            if value is not None:
                recovered[name] = value
    return pipe_snapshot, report, raw, recovered
