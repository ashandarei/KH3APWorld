"""Persist the last published check counts for each connection profile."""

from hashlib import sha256
from pathlib import Path
from typing import Any


def run_check_progress_path(bridge_dir: Path, affinity: str) -> Path:
    key = sha256(affinity.encode("utf-8")).hexdigest()
    return bridge_dir / "check-progress" / f"{key}.json"


def check_counts(payload: Any) -> tuple[int, int] | None:
    if not isinstance(payload, dict) or payload.get("check_reporting_blocked"):
        return None
    checked = payload.get("checked_location_count")
    total = payload.get("total_location_count")
    if type(checked) is int and type(total) is int and checked >= 0 and total >= 0:
        return checked, total
    return None
