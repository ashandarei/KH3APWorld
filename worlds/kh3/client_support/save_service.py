"""KH3 client save-directory selection, preservation, and vanilla restore."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Protocol

from CommonClient import logger

from .save_file_policy import checked_root
from .save_management import (
    backup_vanilla_save_files as backup_vanilla_saves,
)
from .save_management import (
    delete_save_files as delete_active_save_files,
)
from .save_management import (
    delete_save_files_and_backups as delete_active_save_files_and_backups,
)
from .save_management import (
    find_save_backup_dirs as find_backup_dirs,
)
from .save_management import (
    restore_vanilla_save_files as restore_vanilla_saves,
)
from .save_management import (
    summarize_save_directory as summarize_save_files,
)
from .seed_guard import SeedGuardService


class SaveManagementSource(Protocol):
    """Narrow context surface required by save management."""

    bridge_save_dir: Path
    save_dir_override: bool
    save_dir_dynamic_root_override: bool


class SaveManagementService:
    """Own save-directory selection and save preservation operations."""

    def __init__(self, source: SaveManagementSource, *, process_running: Callable[[], bool]) -> None:
        self.source = source
        self.process_running = process_running

    @staticmethod
    def auto_display_save_root_for_save_dir(save_dir: Path | str) -> Path:
        path = Path(save_dir).expanduser().resolve()
        if path.name.casefold() != "savegames":
            return path
        account_root = path.parent
        account_parent = account_root.parent
        if account_parent.name.casefold() == "steam":
            return account_parent.resolve()
        return path

    def display_kh3_save_dir(self) -> Path:
        save_dir = self.source.bridge_save_dir.expanduser().resolve()
        if getattr(self.source, "save_dir_override", False) and not getattr(
            self.source,
            "save_dir_dynamic_root_override",
            False,
        ):
            return save_dir
        return self.auto_display_save_root_for_save_dir(save_dir)

    @staticmethod
    def delete_save_files(
        save_dir: Path,
        *,
        preserve_root: bool = True,
        archive_label: str | None = None,
        backup_current_save: bool = True,
    ) -> dict[str, Any]:
        return delete_active_save_files(
            save_dir,
            preserve_root=preserve_root,
            archive_label=archive_label,
            backup_current_save=backup_current_save,
        )

    @staticmethod
    def find_save_backup_dirs(save_dir: Path) -> list[Path]:
        return find_backup_dirs(save_dir)

    @staticmethod
    def delete_save_files_and_backups(
        save_dir: Path,
        *,
        preserve_root: bool = True,
        archive_label: str | None = None,
        backup_current_save: bool = True,
    ) -> dict[str, Any]:
        return delete_active_save_files_and_backups(
            save_dir,
            preserve_root=preserve_root,
            archive_label=archive_label,
            backup_current_save=backup_current_save,
        )

    def backup_vanilla_save_files(self) -> dict[str, Any]:
        return backup_vanilla_saves(self.source.bridge_save_dir)

    def restore_vanilla_save_files(self) -> dict[str, Any]:
        return restore_vanilla_saves(self.source.bridge_save_dir)

    @staticmethod
    def summarize_save_directory(save_dir: Path) -> dict[str, int]:
        return summarize_save_files(save_dir)

    @staticmethod
    def steam_sibling_save_dirs(save_dir: Path) -> list[Path]:
        save_dir = checked_root(save_dir)
        steam_root: Path | None = None
        if save_dir.name.casefold() == "savegames" and save_dir.parent.parent.name.casefold() == "steam":
            steam_root = save_dir.parent.parent
        elif save_dir.name.casefold() == "steam":
            steam_root = save_dir
        if steam_root is None or not steam_root.is_dir():
            return [save_dir]

        targets: list[Path] = []
        for account_dir in sorted(steam_root.iterdir(), key=lambda path: path.name.casefold()):
            candidate = account_dir / "SaveGames"
            if account_dir.is_dir() and candidate.is_dir():
                targets.append(checked_root(candidate))
        return targets or [save_dir]

    def should_clean_steam_sibling_save_dirs(self) -> bool:
        return not getattr(self.source, "save_dir_override", False) or getattr(
            self.source,
            "save_dir_dynamic_root_override",
            False,
        )

    def save_cleanup_targets(self, *, include_steam_siblings: bool = False) -> list[Path]:
        primary = checked_root(self.source.bridge_save_dir)
        targets = (
            self.steam_sibling_save_dirs(primary)
            if include_steam_siblings and self.should_clean_steam_sibling_save_dirs()
            else [primary]
        )
        unique: list[Path] = []
        seen: set[str] = set()
        for target in targets:
            key = os.path.normcase(str(target))
            if key in seen:
                continue
            seen.add(key)
            unique.append(target)
        return unique

    def backup_vanilla_saves_before_ap_install(self) -> None:
        save_dir = self.source.bridge_save_dir.expanduser().resolve()
        associated_seed, _guard_version = SeedGuardService(self.source).save_seed_association_for_dir(save_dir)
        if associated_seed:
            # A first-time player may have had no vanilla saves to back up.
            # Reinstalling the runtime must not move their first AP saves into
            # the still-absent vanilla backup and leave the live folder empty.
            logger.info(
                "Keeping active KH3 saves associated with AP seed %s in %s during runtime installation.",
                associated_seed,
                save_dir,
            )
            return
        result = self.backup_vanilla_save_files()
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        if result.get("status") == "exists":
            logger.info(
                "Vanilla KH3 save backup already exists at %s; leaving current saves in place.",
                result.get("backup_dir"),
            )
        elif result.get("status") == "missing":
            logger.info("No KH3 save directory found for vanilla backup: %s", result.get("save_dir"))
        elif errors:
            logger.warning(
                "Vanilla KH3 save backup completed partially: moved %s file(s), kept %s safe file(s), with %s error(s).",
                result.get("moved_files", 0),
                result.get("kept_files", 0),
                len(errors),
            )
            for error in errors[:5]:
                logger.warning("KH3 vanilla save backup error: %s", error)
        elif int(result.get("moved_files", 0)) > 0:
            logger.info(
                "Moved %s vanilla KH3 save file(s) to %s before installing AP; kept %s safe file(s).",
                result.get("moved_files", 0),
                result.get("backup_dir"),
                result.get("kept_files", 0),
            )

    def preserve_unidentified_saves_before_ap_install(self, save_dir: Path) -> dict[str, Any]:
        """Safely clear unassociated saves before the first managed AP run."""

        save_dir = checked_root(save_dir)
        if self.process_running():
            return {
                "status": "game_running",
                "mode": "none",
                "save_dir": str(save_dir),
                "preserved_files": 0,
                "preserved_dir": None,
                "errors": ["Close KINGDOM HEARTS III before preserving its active saves."],
            }

        vanilla_result = backup_vanilla_saves(save_dir)
        vanilla_errors = (
            vanilla_result.get("errors")
            if isinstance(vanilla_result.get("errors"), list)
            else []
        )
        if vanilla_result.get("status") != "exists":
            return {
                "status": str(vanilla_result.get("status") or ""),
                "mode": "vanilla_backup",
                "save_dir": str(save_dir),
                "preserved_files": int(vanilla_result.get("moved_files", 0)),
                "preserved_dir": vanilla_result.get("backup_dir"),
                "errors": vanilla_errors,
            }

        # A protected vanilla backup already exists, so the unidentified live
        # files may be an older AP run. Archive them separately instead of
        # overwriting or mislabelling the known vanilla backup.
        archive_result = delete_active_save_files(
            save_dir,
            preserve_root=True,
            archive_label="Unmanaged",
            backup_current_save=True,
        )
        archive_errors = (
            archive_result.get("errors")
            if isinstance(archive_result.get("errors"), list)
            else []
        )
        return {
            "status": str(archive_result.get("status") or ""),
            "mode": "unmanaged_archive",
            "save_dir": str(save_dir),
            "preserved_files": int(archive_result.get("archived_files", 0)),
            "preserved_dir": archive_result.get("archive_dir"),
            "errors": archive_errors,
        }


__all__ = ["SaveManagementService", "SaveManagementSource"]
