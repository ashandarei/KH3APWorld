from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ENABLE_KH3_PYTHON_PROFILING = os.environ.get("KH3_ENABLE_PYTHON_PROFILING", "0") != "0"
ENABLE_KH3_PYTHON_PROFILE_LOGGING = os.environ.get("KH3_ENABLE_PYTHON_PROFILE_LOGGING", "0") != "0"
KH3_PYTHON_PROFILE_INTERVAL_SECONDS = float(os.environ.get("KH3_PYTHON_PROFILE_INTERVAL_SECONDS", "5.0"))
KH3_PYTHON_PROFILE_FILE_NAME = "python_profile.csv"


@dataclass
class _ProfileBucket:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0
    interval_start: float = 0.0
    thread: str = ""


_profile_lock = threading.Lock()
_profile_buckets: dict[str, _ProfileBucket] = {}
_profile_sink_path: Path | None = None
_profile_log_func: Callable[[str], None] | None = None
_profile_next_flush_at: float | None = None


def configure_python_profiler(
    bridge_dir: Path | str | None = None,
    *,
    log_func: Callable[[str], None] | None = None,
) -> None:
    global _profile_sink_path, _profile_log_func
    if bridge_dir is not None:
        _profile_sink_path = Path(bridge_dir) / KH3_PYTHON_PROFILE_FILE_NAME
    if log_func is not None:
        _profile_log_func = log_func


def _append_profile_rows(rows: list[dict[str, str]]) -> None:
    if _profile_sink_path is None or not rows:
        return
    try:
        _profile_sink_path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not _profile_sink_path.exists()
        with _profile_sink_path.open("a", encoding="utf-8", newline="") as handle:
            if needs_header:
                handle.write(
                    "timestamp_utc,scope,count,avg_ms,max_ms,last_ms,interval_ms,thread\n"
                )
            for row in rows:
                handle.write(
                    ",".join(
                        [
                            row["timestamp_utc"],
                            row["scope"],
                            row["count"],
                            row["avg_ms"],
                            row["max_ms"],
                            row["last_ms"],
                            row["interval_ms"],
                            row["thread"],
                        ]
                    )
                    + "\n"
                )
    except OSError:
        return


def record_python_profile_sample(scope_name: str, elapsed_ms: float) -> None:
    global _profile_next_flush_at
    if not ENABLE_KH3_PYTHON_PROFILING:
        return

    now = time.monotonic()
    rows: list[dict[str, str]] = []
    with _profile_lock:
        bucket = _profile_buckets.get(scope_name)
        if bucket is None:
            bucket = _profile_buckets[scope_name] = _ProfileBucket(interval_start=now)
        bucket.count += 1
        bucket.total_ms += elapsed_ms
        bucket.max_ms = max(bucket.max_ms, elapsed_ms)
        bucket.last_ms = elapsed_ms
        bucket.thread = threading.current_thread().name.replace(",", ";")
        if _profile_next_flush_at is None:
            _profile_next_flush_at = now + KH3_PYTHON_PROFILE_INTERVAL_SECONDS
        if now < _profile_next_flush_at:
            return

        timestamp_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for bucket_scope, pending in _profile_buckets.items():
            if not pending.count:
                continue
            interval_ms = (now - pending.interval_start) * 1000.0
            rows.append({
                "timestamp_utc": timestamp_utc,
                "scope": bucket_scope.replace(",", ";"),
                "count": str(pending.count),
                "avg_ms": f"{pending.total_ms / pending.count:.3f}",
                "max_ms": f"{pending.max_ms:.3f}",
                "last_ms": f"{pending.last_ms:.3f}",
                "interval_ms": f"{interval_ms:.0f}",
                "thread": pending.thread,
            })
            pending.count = 0
            pending.total_ms = 0.0
            pending.max_ms = 0.0
            pending.last_ms = 0.0
            pending.interval_start = now
        _profile_next_flush_at = now + KH3_PYTHON_PROFILE_INTERVAL_SECONDS

    if ENABLE_KH3_PYTHON_PROFILE_LOGGING:
        for row in rows:
            message = (
                "[KH3PythonProfile] "
                f"scope={row['scope']} count={row['count']} avg_ms={row['avg_ms']} "
                f"max_ms={row['max_ms']} last_ms={row['last_ms']} interval_ms={row['interval_ms']} "
                f"thread={row['thread']}"
            )
            if _profile_log_func is not None:
                _profile_log_func(message)
            else:
                print(message)
    _append_profile_rows(rows)


class PythonProfileScope:
    def __init__(self, scope_name: str):
        self.scope_name = scope_name
        self.started_at = 0.0

    def __enter__(self) -> "PythonProfileScope":
        if ENABLE_KH3_PYTHON_PROFILING:
            self.started_at = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not ENABLE_KH3_PYTHON_PROFILING or self.started_at <= 0.0:
            return
        record_python_profile_sample(
            self.scope_name,
            (time.perf_counter() - self.started_at) * 1000.0,
        )
