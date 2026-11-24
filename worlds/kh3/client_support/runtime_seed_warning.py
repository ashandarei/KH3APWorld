"""Recognize a running game's seed compatibility result from its startup log."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..platform_support.game_discovery import kh3_ue4ss_log_from_root

WARNING_TITLE = "KH3 update required"
WARNING_MESSAGE = "KH3 version mismatch. Close KH3, click Patch Game, then relaunch."

_SEED_READY = b"[KH3APProbeCpp] Native seed settings/catalog loaded from pak="
_SEED_REJECTED = b"[KH3APProbeCpp] Native seed settings unavailable: "
_CATALOG_MISMATCH = _SEED_REJECTED + b"seed_location_catalog_mismatch"
_STARTUP_LOG_LIMIT = 512 * 1024


def read_seed_startup_mismatch(log_path: Path, process_started_at: datetime) -> bool | None:
    """Return None while a result for this launch is missing, or whether it is a mismatch."""
    # UE4SS writes UTC timestamps with second precision. Ignore lines from an earlier launch.
    started = process_started_at.astimezone(timezone.utc).strftime("[%Y-%m-%d %H:%M:%S").encode("ascii")
    try:
        with log_path.open("rb") as log:
            startup = log.read(_STARTUP_LOG_LIMIT)
    except OSError:
        return None
    for line in startup.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        timestamp, _, message = line.partition(b"] ")
        if not timestamp.startswith(b"[") or len(timestamp) != len(started) or timestamp < started:
            continue
        message = message.rstrip(b"\r\n")
        if message.startswith((_SEED_READY, _SEED_REJECTED)):
            return message == _CATALOG_MISMATCH
    return None


async def wait_for_seed_startup_mismatch(
    game_dir: Path,
    log_override: Path | None,
    process_started_at: datetime,
    still_current: Callable[[], bool],
) -> bool | None:
    def read_startup() -> bool | None:
        try:
            log_path = log_override or kh3_ue4ss_log_from_root(game_dir)
        except OSError:
            return None
        return read_seed_startup_mismatch(log_path, process_started_at)

    # The pipe opens just before the DLL loads the seed. Only retry during that startup window.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while still_current() and loop.time() < deadline:
        result = await asyncio.to_thread(read_startup)
        if result is not None:
            return result
        await asyncio.sleep(0.5)
    return None
