"""Headless KH3 save planning, backup, restore, and cleanup operations."""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .save_file_policy import (
    SaveFileError, check_save_transfer, checked_path, checked_root, checked_save,
    inspect_save_tree, remove_empty_save_directories, transfer_save,
)

KH3_SAFE_SAVE_FILE_SUFFIXES = {".cfg", ".ini", ".json", ".log", ".txt", ".vdf", ".xml"}
KH3_MODLOADER_SAVE_FILE_NAME = "ModLoaderSaves.sav"
KH3_SAFE_SAVE_FILE_NAMES = {"steam_autocloud.vdf", KH3_MODLOADER_SAVE_FILE_NAME.casefold()}
KH3_VANILLA_SAVE_BACKUP_SUFFIX = "vanilla-backup"
_KH3_ACCOUNT_DIRECTORY_PATTERN = re.compile(r"(?:\d{16,32}|[0-9a-f]{32})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PlannedMove:
    """One explicit source-to-destination move in a save operation."""

    source: Path
    destination: Path


@dataclass(frozen=True, slots=True)
class SaveCleanupPlan:
    """Read-only calculation of every target in a save cleanup or backup."""

    operation: str
    status: str
    save_dir: Path
    archive_dir: Path | None = None
    moves: tuple[PlannedMove, ...] = ()
    deletes: tuple[Path, ...] = ()
    kept_files: tuple[Path, ...] = ()
    dirs_to_remove: tuple[Path, ...] = ()
    remove_root: bool = False
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SaveRestorePlan:
    """Read-only calculation of current-save archival and vanilla restore."""

    status: str
    save_dir: Path
    backup_dir: Path
    current_cleanup: SaveCleanupPlan | None = None
    restores: tuple[PlannedMove, ...] = ()
    deletes: tuple[Path, ...] = ()
    dirs_to_remove: tuple[Path, ...] = ()
    remove_backup_root: bool = False
    errors: tuple[str, ...] = ()


def safe_save_archive_label(value: str | None) -> str:
    label = str(value or "Vanilla").strip() or "Vanilla"
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._-")
    return label or "Vanilla"


def is_safe_kh3_save_file(path: Path) -> bool:
    return path.name.casefold() in KH3_SAFE_SAVE_FILE_NAMES or path.suffix.casefold() in KH3_SAFE_SAVE_FILE_SUFFIXES


def is_modloader_save_file(path: Path) -> bool:
    return path.name.casefold() == KH3_MODLOADER_SAVE_FILE_NAME.casefold()


def modloader_shared_save_path(save_dir: Path) -> Path:
    """Return the persistent ModLoader save path outside a store account-id directory."""

    save_dir = save_dir.expanduser().resolve()
    shared_root = save_dir.parent
    if (
        save_dir.name.casefold() == "savegames"
        and _KH3_ACCOUNT_DIRECTORY_PATTERN.fullmatch(save_dir.parent.name) is not None
    ):
        shared_root = save_dir.parent.parent
    return shared_root / KH3_MODLOADER_SAVE_FILE_NAME


def _legacy_modloader_save_candidates(save_dir: Path) -> list[Path]:
    if not save_dir.parent.is_dir():
        return []
    prefix = f"{save_dir.name}-".casefold()
    candidates: list[Path] = []
    try:
        siblings = tuple(save_dir.parent.iterdir())
    except OSError:
        return []
    for sibling in siblings:
        if not sibling.name.casefold().startswith(prefix):
            continue
        try:
            checked_root(sibling)
        except SaveFileError:
            continue
        if not sibling.is_dir():
            continue
        candidate = sibling / KH3_MODLOADER_SAVE_FILE_NAME
        checked_path(sibling, candidate)
        if candidate.is_file():
            candidates.append(candidate)

    def newest_first(path: Path) -> tuple[int, int, str]:
        archive_timestamp = 0
        timestamp_match = re.search(r"(\d{8})-(\d{6})(?:-\d+)?$", path.parent.name)
        if timestamp_match is not None:
            archive_timestamp = int("".join(timestamp_match.groups()))
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        return archive_timestamp, modified, path.parent.name.casefold()

    return sorted(candidates, key=newest_first, reverse=True)


def _atomic_copy_file(source: Path, destination: Path) -> None:
    checked_path(source.parent, source)
    checked_path(destination.parent, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    checked_path(source.parent, source)
    checked_path(destination.parent, destination)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        checked_path(source.parent, source)
        checked_path(destination.parent, temporary_path)
        shutil.copy2(source, temporary_path)
        checked_path(destination.parent, temporary_path)
        checked_path(destination.parent, destination)
        os.replace(temporary_path, destination)
    finally:
        try:
            checked_path(destination.parent, temporary_path).unlink()
        except OSError:
            pass


def sync_modloader_save_file(save_dir: Path) -> dict[str, Any]:
    """Synchronize ModLoader settings while treating the active SaveGames copy as authoritative."""

    save_dir = checked_root(save_dir)
    active_path = save_dir / KH3_MODLOADER_SAVE_FILE_NAME
    shared_path = modloader_shared_save_path(save_dir)
    copied_to_active = False
    copied_to_shared = False
    source_path: Path | None = None
    status = "missing"
    errors: list[str] = []

    try:
        checked_path(save_dir, active_path)
        checked_path(shared_path.parent, shared_path)
        if active_path.exists():
            if not active_path.is_file():
                raise OSError(f"Active ModLoader save is not a file: {active_path}")
            source_path = active_path
            if shared_path.exists() and not shared_path.is_file():
                raise OSError(f"Shared ModLoader save is not a file: {shared_path}")
            if shared_path.is_file() and filecmp.cmp(active_path, shared_path, shallow=False):
                status = "current"
            else:
                _atomic_copy_file(active_path, shared_path)
                copied_to_shared = True
                status = "updated_shared"
        elif shared_path.exists():
            if not shared_path.is_file():
                raise OSError(f"Shared ModLoader save is not a file: {shared_path}")
            source_path = shared_path
            _atomic_copy_file(shared_path, active_path)
            copied_to_active = True
            status = "restored"
        else:
            candidates = _legacy_modloader_save_candidates(save_dir)
            if candidates:
                source_path = candidates[0]
                _atomic_copy_file(source_path, shared_path)
                copied_to_shared = True
                _atomic_copy_file(shared_path, active_path)
                copied_to_active = True
                status = "migrated"
    except OSError as exc:
        status = "partial" if copied_to_active or copied_to_shared else "error"
        errors.append(f"ModLoader save sync: {exc}")

    return {
        "status": status,
        "active_path": str(active_path),
        "shared_path": str(shared_path),
        "source_path": str(source_path) if source_path is not None else None,
        "copied_to_active": copied_to_active,
        "copied_to_shared": copied_to_shared,
        "errors": errors,
    }


def _attach_modloader_sync_result(result: dict[str, Any], sync_result: dict[str, Any]) -> dict[str, Any]:
    merged = dict(result)
    merged["modloader_save"] = sync_result
    sync_errors = sync_result.get("errors") if isinstance(sync_result.get("errors"), list) else []
    if sync_errors:
        merged["errors"] = [*sync_errors, *list(merged.get("errors", []))]
        merged["status"] = "partial"
    return merged


def _unique_save_archive_dir(save_dir: Path, label: str, *, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = save_dir.parent / f"{save_dir.name}-{safe_save_archive_label(label)}-{timestamp}"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = base.with_name(f"{base.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1


def vanilla_save_backup_dir(save_dir: Path) -> Path:
    save_dir = checked_root(save_dir)
    return save_dir.parent / f"{save_dir.name}-{KH3_VANILLA_SAVE_BACKUP_SUFFIX}"


def delete_confirmed_save_files(save_dir: Path) -> dict[str, Any]:
    """Delete only allowlisted game/randomizer saves in the confirmed directory."""
    root = checked_root(save_dir)
    candidates = inspect_save_tree(root).files
    removed = 0
    errors = []
    for path in candidates:
        try:
            checked_save(root, path)
            path.unlink()
            removed += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    return {"removed_files": removed, "errors": errors}


def plan_delete_save_files(
    save_dir: Path,
    *,
    preserve_root: bool = True,
    archive_label: str | None = None,
    backup_current_save: bool = True,
    now: datetime | None = None,
) -> SaveCleanupPlan:
    """Plan only allowlisted saves. Removing a root never authorizes unknown files."""

    save_dir = checked_root(save_dir)
    if not save_dir.exists():
        return SaveCleanupPlan("delete", "missing", save_dir)
    if not save_dir.is_dir():
        return SaveCleanupPlan("delete", "not_directory", save_dir, errors=(f"Not a directory: {save_dir}",))

    archive_dir = _unique_save_archive_dir(save_dir, archive_label or "Vanilla", now=now)
    moves: list[PlannedMove] = []
    deletes: list[Path] = []
    inventory = inspect_save_tree(save_dir)
    for path in inventory.files:
        if backup_current_save:
            moves.append(PlannedMove(path, archive_dir / path.relative_to(save_dir)))
        else:
            deletes.append(path)
    return SaveCleanupPlan(
        operation="delete",
        status="ready",
        save_dir=save_dir,
        archive_dir=archive_dir if backup_current_save else None,
        moves=tuple(moves),
        deletes=tuple(deletes),
        kept_files=inventory.kept,
        dirs_to_remove=inventory.directories,
        remove_root=not preserve_root,
    )


def _base_delete_result(plan: SaveCleanupPlan, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "save_dir": str(plan.save_dir),
        "removed_files": 0,
        "removed_dirs": 0,
        "archived_files": 0,
        "kept_files": 0,
        "archive_dir": None,
        "errors": list(plan.errors),
    }


def execute_save_cleanup_plan(plan: SaveCleanupPlan) -> dict[str, Any]:
    """Apply a previously calculated cleanup plan and report legacy results."""

    if plan.status != "ready":
        return _base_delete_result(plan, plan.status)
    try:
        checked_root(plan.save_dir)
        for move in plan.moves:
            if plan.archive_dir is None:
                raise SaveFileError("Save cleanup has no archive destination.")
            check_save_transfer(plan.save_dir, plan.archive_dir, move.source, move.destination)
        for path in plan.deletes:
            checked_save(plan.save_dir, path)
    except OSError as exc:
        result = _base_delete_result(plan, "error")
        result["errors"] = [str(exc)]
        return result
    errors: list[str] = []
    archived_files = 0
    removed_files = 0
    removed_dirs = 0
    for move in plan.moves:
        try:
            transfer_save(plan.save_dir, plan.archive_dir, move.source, move.destination, move=True)
            archived_files += 1
        except OSError as exc:
            errors.append(f"{move.source}: {exc}")
    for path in plan.deletes:
        try:
            checked_save(plan.save_dir, path)
            path.unlink()
            removed_files += 1
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    try:
        removed_dirs += remove_empty_save_directories(plan.save_dir)
        checked_root(plan.save_dir)
        if plan.remove_root and plan.save_dir.exists() and not any(plan.save_dir.iterdir()):
            plan.save_dir.rmdir()
            removed_dirs += 1
    except OSError as exc:
        errors.append(f"{plan.save_dir}: {exc}")
    return {
        "status": "ok" if not errors else "partial",
        "save_dir": str(plan.save_dir),
        "removed_files": removed_files,
        "archived_files": archived_files,
        "kept_files": len(plan.kept_files),
        "removed_dirs": removed_dirs,
        "archive_dir": str(plan.archive_dir) if archived_files and plan.archive_dir is not None else None,
        "errors": errors,
    }


def delete_save_files(
    save_dir: Path,
    *,
    preserve_root: bool = True,
    archive_label: str | None = None,
    backup_current_save: bool = True,
) -> dict[str, Any]:
    sync_result = sync_modloader_save_file(save_dir)
    sync_errors = sync_result.get("errors") if isinstance(sync_result.get("errors"), list) else []
    plan = plan_delete_save_files(
        save_dir,
        preserve_root=preserve_root or bool(sync_errors),
        archive_label=archive_label,
        backup_current_save=backup_current_save,
    )
    return _attach_modloader_sync_result(execute_save_cleanup_plan(plan), sync_result)


def plan_backup_vanilla_save_files(save_dir: Path) -> SaveCleanupPlan:
    """Calculate vanilla-save moves to the protected backup directory."""

    save_dir = checked_root(save_dir)
    backup_dir = vanilla_save_backup_dir(save_dir)
    checked_root(backup_dir)
    if not save_dir.exists():
        return SaveCleanupPlan("backup", "missing", save_dir, archive_dir=backup_dir)
    if not save_dir.is_dir():
        return SaveCleanupPlan(
            "backup", "not_directory", save_dir, archive_dir=backup_dir, errors=(f"Not a directory: {save_dir}",)
        )
    if backup_dir.exists() and any(backup_dir.iterdir()):
        return SaveCleanupPlan("backup", "exists", save_dir, archive_dir=backup_dir)

    moves: list[PlannedMove] = []
    inventory = inspect_save_tree(save_dir)
    for path in inventory.files:
        moves.append(PlannedMove(path, backup_dir / path.relative_to(save_dir)))
    return SaveCleanupPlan(
        operation="backup",
        status="ready",
        save_dir=save_dir,
        archive_dir=backup_dir,
        moves=tuple(moves),
        kept_files=inventory.kept,
        dirs_to_remove=inventory.directories,
    )


def execute_vanilla_save_backup_plan(plan: SaveCleanupPlan) -> dict[str, Any]:
    """Apply a vanilla backup plan while preserving the established report."""
    result = execute_save_cleanup_plan(plan)
    backup_dir = plan.archive_dir
    return {
        "status": result["status"],
        "save_dir": str(plan.save_dir),
        "backup_dir": str(backup_dir) if backup_dir is not None else None,
        "moved_files": result["archived_files"],
        "kept_files": result["kept_files"],
        "removed_dirs": result["removed_dirs"],
        "errors": result["errors"],
    }


def backup_vanilla_save_files(save_dir: Path) -> dict[str, Any]:
    sync_result = sync_modloader_save_file(save_dir)
    result = execute_vanilla_save_backup_plan(plan_backup_vanilla_save_files(save_dir))
    return _attach_modloader_sync_result(result, sync_result)


def plan_restore_vanilla_save_files(save_dir: Path, *, now: datetime | None = None) -> SaveRestorePlan:
    """Calculate current-save archival and vanilla restoration without writes."""

    save_dir = checked_root(save_dir)
    backup_dir = vanilla_save_backup_dir(save_dir)
    checked_root(backup_dir)
    if not backup_dir.exists():
        return SaveRestorePlan("missing", save_dir, backup_dir)
    if not backup_dir.is_dir():
        return SaveRestorePlan("not_directory", save_dir, backup_dir, errors=(f"Not a directory: {backup_dir}",))
    inventory = inspect_save_tree(backup_dir)
    restorable_files = inventory.files
    if not restorable_files:
        return SaveRestorePlan(
            "empty",
            save_dir,
            backup_dir,
            dirs_to_remove=inventory.directories,
            remove_backup_root=True,
        )
    current_cleanup = plan_delete_save_files(
        save_dir,
        archive_label="AP_Restore",
        backup_current_save=True,
        now=now,
    )
    restores = tuple(PlannedMove(path, save_dir / path.relative_to(backup_dir)) for path in restorable_files)
    return SaveRestorePlan(
        "ready",
        save_dir,
        backup_dir,
        current_cleanup=current_cleanup,
        restores=restores,
        dirs_to_remove=inventory.directories,
        remove_backup_root=True,
    )


def _empty_restore_result(plan: SaveRestorePlan) -> dict[str, Any]:
    errors = list(plan.errors)
    removed_dirs = 0
    try:
        removed_dirs += remove_empty_save_directories(plan.backup_dir)
        checked_root(plan.backup_dir)
        if plan.remove_backup_root and plan.backup_dir.exists() and not any(plan.backup_dir.iterdir()):
            plan.backup_dir.rmdir()
            removed_dirs += 1
    except OSError as exc:
        errors.append(f"{plan.backup_dir}: {exc}")
    return {
        "status": "empty" if not errors else "partial",
        "save_dir": str(plan.save_dir),
        "backup_dir": str(plan.backup_dir),
        "restored_files": 0,
        "removed_files": 0,
        "removed_dirs": removed_dirs,
        "archived_current_files": 0,
        "archive_dir": None,
        "errors": errors,
    }


def execute_vanilla_save_restore_plan(plan: SaveRestorePlan) -> dict[str, Any]:
    """Apply an explicit restore plan and retain legacy collision behavior."""

    if plan.status == "empty":
        return _empty_restore_result(plan)
    if plan.status != "ready" or plan.current_cleanup is None:
        return {
            "status": plan.status,
            "save_dir": str(plan.save_dir),
            "backup_dir": str(plan.backup_dir),
            "restored_files": 0,
            "removed_dirs": 0,
            "archived_current_files": 0,
            "archive_dir": None,
            "errors": list(plan.errors),
        }
    # Validate the entire restore before archiving the current save. Existing
    # destinations are allowed only when this same plan will first archive them.
    try:
        replaced = {move.source for move in plan.current_cleanup.moves}
        for move in plan.restores:
            source = checked_save(plan.backup_dir, move.source)
            destination = checked_save(plan.save_dir, move.destination)
            if not source.is_file() or source.relative_to(plan.backup_dir) != destination.relative_to(plan.save_dir):
                raise SaveFileError(f"Invalid save restore source: {source}")
            if destination.exists() and destination not in replaced:
                raise SaveFileError(f"Save restore destination already exists: {destination}")
    except OSError as exc:
        return {"status": "error", "save_dir": str(plan.save_dir), "backup_dir": str(plan.backup_dir),
                "restored_files": 0, "removed_dirs": 0, "archived_current_files": 0,
                "archive_dir": None, "errors": [str(exc)]}
    current_result = execute_save_cleanup_plan(plan.current_cleanup)
    errors = list(current_result.get("errors", []))
    if errors:
        return {"status": "partial", "save_dir": str(plan.save_dir), "backup_dir": str(plan.backup_dir),
                "restored_files": 0, "removed_dirs": current_result["removed_dirs"],
                "archived_current_files": current_result["archived_files"],
                "archive_dir": current_result["archive_dir"], "errors": errors}
    restored_files = 0
    removed_files = 0
    removed_dirs = int(current_result.get("removed_dirs", 0))
    for move in plan.restores:
        try:
            transfer_save(plan.backup_dir, plan.save_dir, move.source, move.destination, move=True)
            restored_files += 1
        except OSError as exc:
            errors.append(f"{move.source}: {exc}")
    try:
        removed_dirs += remove_empty_save_directories(plan.backup_dir)
        checked_root(plan.backup_dir)
        if plan.backup_dir.exists() and not any(plan.backup_dir.iterdir()):
            plan.backup_dir.rmdir()
            removed_dirs += 1
    except OSError as exc:
        errors.append(str(exc))
    return {
        "status": "ok" if not errors else "partial",
        "save_dir": str(plan.save_dir),
        "backup_dir": str(plan.backup_dir),
        "restored_files": restored_files,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "archived_current_files": int(current_result.get("archived_files", 0)),
        "archive_dir": current_result.get("archive_dir"),
        "errors": errors,
    }


def restore_vanilla_save_files(save_dir: Path) -> dict[str, Any]:
    sync_result = sync_modloader_save_file(save_dir)
    result = execute_vanilla_save_restore_plan(plan_restore_vanilla_save_files(save_dir))
    return _attach_modloader_sync_result(result, sync_result)


def find_save_backup_dirs(save_dir: Path) -> list[Path]:
    save_dir = checked_root(save_dir)
    if not save_dir.parent.is_dir():
        return []
    prefix = f"{save_dir.name}-"
    result = []
    for path in save_dir.parent.iterdir():
        if path.name.startswith(prefix):
            try:
                checked_root(path)
            except SaveFileError:
                continue
            if path.is_dir():
                result.append(path)
    return sorted(result)


def delete_save_files_and_backups(
    save_dir: Path,
    *,
    preserve_root: bool = True,
    archive_label: str | None = None,
    backup_current_save: bool = True,
) -> dict[str, Any]:
    active_result = delete_save_files(
        save_dir,
        preserve_root=preserve_root,
        archive_label=archive_label,
        backup_current_save=backup_current_save,
    )
    errors = list(active_result.get("errors", []))
    archive_dirs = [active_result["archive_dir"]] if isinstance(active_result.get("archive_dir"), str) else []
    return {
        "status": "ok" if not errors else "partial",
        "save_dir": str(save_dir.expanduser().resolve()),
        "removed_files": int(active_result.get("removed_files", 0)),
        "archived_files": int(active_result.get("archived_files", 0)),
        "kept_files": int(active_result.get("kept_files", 0)),
        "removed_dirs": int(active_result.get("removed_dirs", 0)),
        "removed_backup_dirs": 0,
        "archive_dirs": archive_dirs,
        "archive_dir": archive_dirs[0] if archive_dirs else None,
        "errors": errors,
        "targets": [active_result],
    }


def summarize_save_directory(save_dir: Path) -> dict[str, int]:
    inventory = inspect_save_tree(save_dir)
    return {
        "active_files": len(inventory.files),
        "safe_files": len(inventory.kept),
        "backup_dirs": len(find_save_backup_dirs(save_dir)),
    }


__all__ = [
    "KH3_MODLOADER_SAVE_FILE_NAME",
    "KH3_SAFE_SAVE_FILE_NAMES",
    "KH3_SAFE_SAVE_FILE_SUFFIXES",
    "KH3_VANILLA_SAVE_BACKUP_SUFFIX",
    "PlannedMove",
    "SaveCleanupPlan",
    "SaveRestorePlan",
    "backup_vanilla_save_files",
    "delete_save_files",
    "delete_save_files_and_backups",
    "execute_save_cleanup_plan",
    "execute_vanilla_save_backup_plan",
    "execute_vanilla_save_restore_plan",
    "find_save_backup_dirs",
    "is_modloader_save_file",
    "is_safe_kh3_save_file",
    "modloader_shared_save_path",
    "plan_backup_vanilla_save_files",
    "plan_delete_save_files",
    "plan_restore_vanilla_save_files",
    "restore_vanilla_save_files",
    "safe_save_archive_label",
    "summarize_save_directory",
    "sync_modloader_save_file",
    "vanilla_save_backup_dir",
]
