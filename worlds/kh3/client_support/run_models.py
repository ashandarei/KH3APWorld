"""Value objects shared by run storage, activation, and the client UI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class KH3RunManagerError(RuntimeError):
    """A user-actionable run-library or activation failure."""


@dataclass(frozen=True, slots=True)
class KH3RunInfo:
    """One imported or currently installed KH3 AP run."""

    run_id: str
    seed_id: str
    player: int
    slot_name: str
    pak_name: str
    managed: bool
    active: bool
    installed: bool
    pak_available: bool
    has_save: bool
    run_dir: Path | None
    managed_pak_path: Path | None
    installed_pak_path: Path | None
    save_snapshot_dir: Path | None
    bridge_dirs: tuple[Path, ...]
    added_at_utc: str = ""
    last_activated_at_utc: str = ""
    server_address: str = ""
    connection_slot_name: str = ""
    room_url: str = ""
    connection_host: str = ""
    bridge_dir_name: str = ""
    platform: str = ""

    @property
    def effective_slot_name(self) -> str:
        """Return the editable AP login name without changing the pak identity."""

        return self.connection_slot_name or self.slot_name


@dataclass(frozen=True, slots=True)
class KH3RunConnectionProfile:
    """Fast startup view of the active run's editable AP connection fields."""

    run_id: str
    slot_name: str
    connection_slot_name: str = ""
    server_address: str = ""
    room_url: str = ""
    connection_host: str = ""
    bridge_dir_name: str = ""

    @property
    def effective_slot_name(self) -> str:
        return self.connection_slot_name or self.slot_name


@dataclass(frozen=True, slots=True)
class KH3RunActivationResult:
    """Completed run activation and the files moved into each side."""

    run: KH3RunInfo
    previous_run_id: str | None
    switched: bool
    saved_current_files: int
    restored_target_files: int
    save_dir: Path
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KH3RunSuspensionResult:
    """An active run preserved in managed storage and removed from the live mods folder."""

    run: KH3RunInfo
    saved_current_files: int
    save_dir: Path
    warnings: tuple[str, ...] = ()
    save_run: KH3RunInfo | None = None


@dataclass(frozen=True, slots=True)
class KH3RunEnrollmentResult:
    """A managed-run enrollment and whether its pak copy changed."""

    run: KH3RunInfo
    copied: bool


@dataclass(frozen=True, slots=True)
class KH3RunDeleteResult:
    """Completed full-run deletion report."""

    run_id: str
    removed_run: bool
    removed_bridge_dirs: int
    removed_installed_pak: bool = False
    removed_active_save_files: int = 0
    cleared_active_profile: bool = False
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PakIdentity:
    run_id: str
    seed_id: str
    player: int
    slot_name: str
    pak_name: str
    path: Path
