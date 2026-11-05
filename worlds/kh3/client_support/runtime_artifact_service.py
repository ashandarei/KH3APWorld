"""Pure planning for KH3 installed runtime artifacts and probe configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class RuntimeArtifactActionKind(Enum):
    """Ordered effects applied by the runtime-artifact executor."""

    LOG_INFO = "log_info"
    LOG_WARNING = "log_warning"
    WRITE_PROBE_CONFIG = "write_probe_config"
    UNINSTALL_RUNTIME_ARTIFACTS = "uninstall_runtime_artifacts"
    RESTORE_VANILLA_SAVES = "restore_vanilla_saves"
    DISCONNECT_AFTER_RESTORE = "disconnect_after_restore"
    BACKUP_VANILLA_SAVES = "backup_vanilla_saves"
    ENSURE_GOA_MOD = "ensure_goa_mod"
    INSTALL_RUNTIME_FILES = "install_runtime_files"


@dataclass(frozen=True, slots=True)
class RuntimeArtifactAction:
    """One explicitly ordered facade effect."""

    kind: RuntimeArtifactActionKind
    path: Path | None = None
    bridge_dir: Path | None = None
    text: str = ""
    log_args: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeArtifactPlan:
    """An immutable sequence of runtime-artifact effects."""

    actions: tuple[RuntimeArtifactAction, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactStatusInput:
    """Already-observed install facts used to derive compatibility summaries."""

    goa_status: Mapping[str, Any] | None = None
    goa_present: bool = False
    seed_pak_present: bool = False
    runtime_status: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class InstalledArtifactSummary:
    """Presence/currentness decisions for all KH3 AP install artifact families."""

    goa_present: bool
    goa_current: bool
    seed_pak_present: bool
    runtime_artifacts_present: bool
    install_artifacts_present: bool


@dataclass(frozen=True, slots=True)
class ProbeConfigPathInput:
    """Inputs preserving explicit-path precedence across KH3 root changes."""

    explicit_path: str | Path | None
    discovered_path: Path | None
    override_active: bool = False
    current_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ProbeConfigurationInput:
    """Facts needed to decide whether the active bridge can configure the probe."""

    bridge_dir: Path | None
    kh3_root: Path
    win64_exists: bool
    config_path: Path | None


@dataclass(frozen=True, slots=True)
class UninstallResult:
    """Normalized result of the existing runtime uninstall executor."""

    removed_files: int
    removed_paks: int
    removed_dirs: int
    updated_mods_txt: int
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VanillaRestoreResult:
    """Normalized result of the existing vanilla-save restore service."""

    status: str
    backup_dir: Any
    archive_dir: Any
    restored_files: int
    archived_current_files: int
    errors: tuple[str, ...]


_RUNTIME_ARTIFACT_KEYS = (
    "probe_dll_installed",
    "probe_config_installed",
    "probe_mod_enabled",
    "probe_single_layout_ready",
)


class RuntimeArtifactService:
    """Plan and normalize runtime artifact, uninstall, and probe operations."""

    @staticmethod
    def summarize_artifacts(source: ArtifactStatusInput) -> InstalledArtifactSummary:
        runtime_present = bool(
            source.runtime_status and any(source.runtime_status.get(key) for key in _RUNTIME_ARTIFACT_KEYS)
        )
        goa_current = bool(source.goa_status and source.goa_status.get("status") == "current")
        install_present = source.goa_present or source.seed_pak_present or runtime_present
        return InstalledArtifactSummary(
            goa_present=source.goa_present,
            goa_current=goa_current,
            seed_pak_present=source.seed_pak_present,
            runtime_artifacts_present=runtime_present,
            install_artifacts_present=install_present,
        )

    @staticmethod
    def select_probe_config_path(source: ProbeConfigPathInput) -> Path | None:
        if source.override_active:
            return source.current_path
        if source.explicit_path:
            return Path(source.explicit_path).expanduser().resolve()
        return source.discovered_path

    @staticmethod
    def plan_probe_configuration(source: ProbeConfigurationInput) -> RuntimeArtifactPlan:
        if source.bridge_dir is None:
            return RuntimeArtifactPlan()
        if not source.win64_exists:
            return RuntimeArtifactPlan(
                (
                    RuntimeArtifactAction(
                        RuntimeArtifactActionKind.LOG_INFO,
                        text=(
                            "KH3 install directory was not found at %s. Set KH3 Dir in the KH3 Config tab "
                            "if runtime chest events do not reach the client."
                        ),
                        log_args=(source.kh3_root,),
                    ),
                )
            )
        if source.config_path is None:
            return RuntimeArtifactPlan(
                (
                    RuntimeArtifactAction(
                        RuntimeArtifactActionKind.LOG_INFO,
                        text=(
                            "KH3APProbeCpp config path was not found. Set KH3_AP_PROBE_CONFIG or --probe-config "
                            "if runtime chest events do not reach the client."
                        ),
                    ),
                )
            )
        return RuntimeArtifactPlan(
            (
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.WRITE_PROBE_CONFIG,
                    path=source.config_path,
                    bridge_dir=source.bridge_dir,
                ),
            )
        )

    @staticmethod
    def plan_runtime_install() -> RuntimeArtifactPlan:
        return RuntimeArtifactPlan(
            tuple(
                RuntimeArtifactAction(kind)
                for kind in (
                    RuntimeArtifactActionKind.BACKUP_VANILLA_SAVES,
                    RuntimeArtifactActionKind.ENSURE_GOA_MOD,
                    RuntimeArtifactActionKind.INSTALL_RUNTIME_FILES,
                )
            )
        )

    @staticmethod
    def plan_runtime_uninstall() -> RuntimeArtifactPlan:
        return RuntimeArtifactPlan(
            tuple(
                RuntimeArtifactAction(kind)
                for kind in (
                    RuntimeArtifactActionKind.UNINSTALL_RUNTIME_ARTIFACTS,
                    RuntimeArtifactActionKind.RESTORE_VANILLA_SAVES,
                    RuntimeArtifactActionKind.DISCONNECT_AFTER_RESTORE,
                )
            )
        )

    @staticmethod
    def normalize_uninstall_result(result: Mapping[str, Any]) -> UninstallResult:
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        return UninstallResult(
            int(result.get("removed_files", 0)),
            int(result.get("removed_paks", 0)),
            int(result.get("removed_dirs", 0)),
            int(result.get("updated_mods_txt", 0)),
            tuple(str(error) for error in errors),
        )

    @staticmethod
    def plan_uninstall_report(result: UninstallResult) -> RuntimeArtifactPlan:
        if result.errors:
            actions = [
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.LOG_WARNING,
                    text=(
                        "Uninstalled KH3 AP files partially: removed %s file(s), %s pak(s), %s folder(s), "
                        "updated %s mods.txt file(s), with %s error(s)."
                    ),
                    log_args=(
                        result.removed_files,
                        result.removed_paks,
                        result.removed_dirs,
                        result.updated_mods_txt,
                        len(result.errors),
                    ),
                )
            ]
            actions.extend(
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.LOG_WARNING,
                    text="KH3 AP uninstall error: %s",
                    log_args=(error,),
                )
                for error in result.errors[:5]
            )
            return RuntimeArtifactPlan(tuple(actions))
        return RuntimeArtifactPlan(
            (
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.LOG_INFO,
                    text=(
                        "Uninstalled KH3 AP files: removed %s file(s), %s pak(s), %s folder(s), "
                        "updated %s mods.txt file(s)."
                    ),
                    log_args=(result.removed_files, result.removed_paks, result.removed_dirs, result.updated_mods_txt),
                ),
            )
        )

    @staticmethod
    def normalize_restore_result(result: Mapping[str, Any]) -> VanillaRestoreResult:
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        return VanillaRestoreResult(
            status=str(result.get("status") or ""),
            backup_dir=result.get("backup_dir"),
            archive_dir=result.get("archive_dir"),
            restored_files=int(result.get("restored_files", 0)),
            archived_current_files=int(result.get("archived_current_files", 0)),
            errors=tuple(str(error) for error in errors),
        )

    @staticmethod
    def plan_restore_report(result: VanillaRestoreResult) -> RuntimeArtifactPlan:
        if result.status == "missing":
            return RuntimeArtifactPlan(
                (
                    RuntimeArtifactAction(
                        RuntimeArtifactActionKind.LOG_INFO,
                        text="No vanilla KH3 save backup found at %s; no saves were restored.",
                        log_args=(result.backup_dir,),
                    ),
                )
            )
        if result.status == "empty":
            return RuntimeArtifactPlan(
                (
                    RuntimeArtifactAction(
                        RuntimeArtifactActionKind.LOG_INFO,
                        text="Vanilla KH3 save backup at %s was empty; no saves were restored.",
                        log_args=(result.backup_dir,),
                    ),
                )
            )
        if result.errors:
            actions = [
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.LOG_WARNING,
                    text=(
                        "Restored vanilla KH3 saves partially: restored %s file(s), archived %s current AP save file(s), "
                        "with %s error(s)."
                    ),
                    log_args=(result.restored_files, result.archived_current_files, len(result.errors)),
                )
            ]
            actions.extend(
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.LOG_WARNING,
                    text="KH3 vanilla save restore error: %s",
                    log_args=(error,),
                )
                for error in result.errors[:5]
            )
            return RuntimeArtifactPlan(tuple(actions))
        return RuntimeArtifactPlan(
            (
                RuntimeArtifactAction(
                    RuntimeArtifactActionKind.LOG_INFO,
                    text=("Restored %s vanilla KH3 save file(s) from %s; archived %s current AP save file(s) to %s."),
                    log_args=(
                        result.restored_files,
                        result.backup_dir,
                        result.archived_current_files,
                        result.archive_dir or "no archive needed",
                    ),
                ),
            )
        )


__all__ = [
    "ArtifactStatusInput",
    "InstalledArtifactSummary",
    "ProbeConfigPathInput",
    "ProbeConfigurationInput",
    "RuntimeArtifactAction",
    "RuntimeArtifactActionKind",
    "RuntimeArtifactPlan",
    "RuntimeArtifactService",
    "UninstallResult",
    "VanillaRestoreResult",
]
