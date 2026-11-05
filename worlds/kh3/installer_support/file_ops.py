"""KH3 runtime file ops operations."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .resources import (
    _iter_resource_files,
    _resource_bytes_equal,
    _resource_is_dir,
    _resource_is_file,
)


def _unique_backup_path(path: Path, timestamp: str) -> Path:
    backup = path.with_name(f"{path.name}.apbak-{timestamp}")
    index = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.apbak-{timestamp}-{index}")
        index += 1
    return backup


def _backup_existing_file(path: Path, timestamp: str) -> Path:
    backup = _unique_backup_path(path, timestamp)
    shutil.copy2(path, backup)
    return backup


def _copy_tree_contents(
    source_dir, target_dir: Path, timestamp: str, *, exclude: tuple[str, ...] = ()
) -> dict[str, Any]:
    copied: list[str] = []
    skipped: list[str] = []
    backups: list[str] = []
    errors: list[str] = []

    if not _resource_is_dir(source_dir):
        return {
            "copied": copied,
            "skipped": skipped,
            "backups": backups,
            "missing": [str(source_dir)],
            "errors": errors,
        }

    for relative_parts, source in _iter_resource_files(source_dir):
        # Never copy a loose Microsoft DLL from an older bundle, including aliases.
        if relative_parts[-1].casefold() == "xinput1_3.dll":
            continue
        if len(relative_parts) == 1 and relative_parts[0].casefold() in {name.casefold() for name in exclude}:
            continue
        relative = Path(*relative_parts)
        target = target_dir / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if _resource_bytes_equal(source, target):
                skipped.append(str(relative))
                continue
            if target.exists():
                backups.append(str(_backup_existing_file(target, timestamp)))
            _replace_file_bytes(target, source.read_bytes())
            copied.append(str(relative))
        except OSError as exc:
            errors.append(f"{relative}: {exc}")

    return {
        "copied": copied,
        "skipped": skipped,
        "backups": backups,
        "missing": [],
        "errors": errors,
    }


def _replace_file_bytes(target: Path, data: bytes) -> None:
    """Atomically replace a file when its directory is writable."""
    target_mode = target.stat().st_mode if target.exists() else None
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.aptmp-",
            dir=target.parent,
            delete=False,
        ) as output:
            temporary_path = type(target)(output.name)
            output.write(data)
        temporary_path.chmod(target_mode if target_mode is not None else 0o644)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _copy_file(source, target: Path, timestamp: str) -> dict[str, Any]:
    if not _resource_is_file(source):
        return {"copied": False, "skipped": False, "backup": None, "missing": str(source), "error": None}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if _resource_bytes_equal(source, target):
            return {"copied": False, "skipped": True, "backup": None, "missing": None, "error": None}

        backup = str(_backup_existing_file(target, timestamp)) if target.exists() else None
        _replace_file_bytes(target, source.read_bytes())
    except OSError as exc:
        return {"copied": False, "skipped": False, "backup": None, "missing": None, "error": str(exc)}
    return {"copied": True, "skipped": False, "backup": backup, "missing": None, "error": None}
