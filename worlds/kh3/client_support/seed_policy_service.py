"""Seed, installed-pak, and save-directory policy for the KH3 client."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .save_service import SaveManagementService
from .seed_guard import KH3_SAVE_SEED_GUARD_VERSION, SeedGuardService

KH3_MINIMUM_SEED_PAK_SIZE_BYTES = 5 * 1024


@dataclass(frozen=True)
class InstalledPakDecision:
    """Compatibility result for the expected seed and installed player paks."""

    expected_seed: str | None
    installed_seeds: tuple[str, ...]
    status: str
    compatible: bool


@dataclass(frozen=True)
class PakGuardResult:
    """Result of requiring exactly one installed seed pak before connecting."""

    allowed: bool
    warning: str


@dataclass(frozen=True)
class SaveSeedDecision:
    """Current association decision for active KH3 save directories."""

    expected_seed: str | None
    status: str
    mismatch: bool
    restart_required_seed: str | None
    targets: tuple[Path, ...]


@dataclass(frozen=True)
class SaveGuardResult:
    """Result of applying save-seed guard policy."""

    allowed: bool
    status: str
    mismatch: bool
    warning: str
    restart_required_seed: str | None
    offline_seed_name: str | None


@dataclass(frozen=True)
class DisconnectPolicyResult:
    """State consequences of a seed-guard disconnect."""

    restart_required_seed: str | None
    disconnect_scheduled: bool


@dataclass(frozen=True)
class SeedObservationResult:
    """Result of observing a room seed identity."""

    seed_name: Any
    restart_required_seed: str | None


@dataclass(frozen=True)
class SaveDirectoryDecision:
    """Resolved state for an explicit or automatic save-directory selection."""

    save_dir: Path
    override: bool
    dynamic_root_override: bool
    persisted_value: str
    log_message: str


class SeedPolicyService:
    """Own KH3 seed/save policy while delegating persistence and mutation."""

    def __init__(
        self,
        *,
        seed_guard: SeedGuardService,
        save_management: SaveManagementService,
        mods_dir: Callable[[], Path],
        installed_paks: Callable[[], list[dict[str, Any]]],
        process_running: Callable[[], bool],
        load_persistent_kh3: Callable[[], dict[str, Any]],
        resolve_save_dir_input: Callable[[str | Path], Path],
        resolve_save_dir_for_root: Callable[[Path], Path],
        is_dynamic_save_root: Callable[[str | Path], bool],
        is_auto_save_dir: Callable[[str | Path], bool],
        log_info: Callable[..., None],
        log_warning: Callable[..., None],
    ) -> None:
        self.seed_guard = seed_guard
        self.save_management = save_management
        self.mods_dir = mods_dir
        self.installed_paks = installed_paks
        self.process_running = process_running
        self.load_persistent_kh3 = load_persistent_kh3
        self.resolve_save_dir_input = resolve_save_dir_input
        self.resolve_save_dir_for_root = resolve_save_dir_for_root
        self.is_dynamic_save_root = is_dynamic_save_root
        self.is_auto_save_dir = is_auto_save_dir
        self.log_info = log_info
        self.log_warning = log_warning

    def installed_pak_decision(self) -> InstalledPakDecision:
        expected_seed = self.seed_guard.expected_seed_id()
        if not expected_seed:
            return InstalledPakDecision(None, (), "-", True)
        if not self.mods_dir().exists():
            return InstalledPakDecision(expected_seed, (), f"Mods dir missing; expected {expected_seed}", False)

        installed_paks = self.installed_paks()
        installed_seeds = tuple(sorted({pak["seed"] for pak in installed_paks}))
        undersized_paks = self._undersized_seed_paks(installed_paks)
        if not installed_seeds:
            status = f"Missing AP pak for {expected_seed}"
        elif len(installed_paks) > 1:
            status = f"Multiple AP seed paks installed ({len(installed_paks)}); expected {expected_seed}"
        elif undersized_paks:
            pak, size = undersized_paks[0]
            status = (
                f"Invalid: {pak['name']} is only {size} bytes; "
                f"minimum is {KH3_MINIMUM_SEED_PAK_SIZE_BYTES} bytes"
            )
        else:
            stale_seeds = tuple(seed for seed in installed_seeds if seed != expected_seed)
            if expected_seed in installed_seeds and not stale_seeds:
                status = f"OK {expected_seed}"
            elif expected_seed in installed_seeds:
                status = f"OK {expected_seed}; stale {', '.join(stale_seeds)}"
            else:
                status = f"Mismatch: installed {', '.join(installed_seeds)}, expected {expected_seed}"
        return InstalledPakDecision(
            expected_seed,
            installed_seeds,
            status,
            len(installed_paks) == 1
            and installed_seeds == (expected_seed,)
            and not undersized_paks,
        )

    def guard_installed_pak_before_connect(
        self,
        installed_paks: list[dict[str, Any]],
    ) -> PakGuardResult:
        message = self.build_installed_seed_pak_guard_message(installed_paks)
        if not message:
            return PakGuardResult(True, "")
        self.log_warning(message)
        return PakGuardResult(False, message)

    def build_installed_seed_pak_guard_message(
        self,
        installed_paks: list[dict[str, Any]],
    ) -> str:
        if len(installed_paks) == 1:
            undersized_paks = self._undersized_seed_paks(installed_paks)
            if not undersized_paks:
                return ""
            return (
                "The seed file had an error in generating. Please regenerate the seed file. "
                "The approximate size should be around 860kb."
            )
        if not installed_paks:
            return self.build_missing_installed_seed_pak_message()
        names = ", ".join(sorted(pak["name"] for pak in installed_paks))
        return (
            f"Multiple KH3 AP seed paks were found in {self.mods_dir()}: {names}. "
            "Remove all but the one generated for the current seed before connecting."
        )

    @classmethod
    def _undersized_seed_paks(
        cls,
        installed_paks: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], int]]:
        undersized: list[tuple[dict[str, Any], int]] = []
        for pak in installed_paks:
            size = cls._installed_pak_size(pak)
            if size is not None and size < KH3_MINIMUM_SEED_PAK_SIZE_BYTES:
                undersized.append((pak, size))
        return undersized

    @staticmethod
    def _installed_pak_size(pak: dict[str, Any]) -> int | None:
        raw_size = pak.get("size")
        if isinstance(raw_size, int) and not isinstance(raw_size, bool):
            return max(raw_size, 0)
        try:
            return Path(str(pak["path"])).stat().st_size
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def build_missing_installed_seed_pak_message(self) -> str:
        return (
            f"KH3 AP seed pak is missing from {self.mods_dir()}. "
            "Install the generated KH3 player .pak in Content/Paks/~mods before connecting."
        )

    def warn_if_installed_pak_mismatch(self, last_warning: str) -> str:
        decision = self.installed_pak_decision()
        if decision.compatible or decision.status == last_warning:
            return last_warning
        self.log_warning(
            "KH3 AP pak seed mismatch: %s. Remove stale KH3 AP paks from %s and install the current room pak.",
            decision.status,
            self.mods_dir(),
        )
        return decision.status

    def build_save_seed_decision(
        self,
        *,
        restart_required_seed: str | None,
        offline_seed_name: str | None,
    ) -> SaveSeedDecision:
        expected_seed = self.seed_guard.expected_seed_id()
        if not expected_seed:
            return SaveSeedDecision(None, "-", False, restart_required_seed, ())
        if restart_required_seed and self.process_running():
            status = (
                "Mismatch: KH3 is still running after a seed mismatch; close and restart KH3 "
                f"before reporting checks for {expected_seed}"
            )
            return SaveSeedDecision(expected_seed, status, True, restart_required_seed, ())
        next_restart_seed = None if restart_required_seed else restart_required_seed
        # Cleanup may include old Steam account folders, but the bridge only
        # reads the selected save directory. Siblings must neither block this
        # run nor acquire its seed association during a successful guard check.
        targets = tuple(self.save_management.save_cleanup_targets(include_steam_siblings=False))
        active_targets = tuple(
            target
            for target in targets
            if int(self.save_management.summarize_save_directory(target).get("active_files", 0)) > 0
        )
        if not active_targets:
            return SaveSeedDecision(
                expected_seed,
                f"OK {expected_seed}; no active saves",
                False,
                next_restart_seed,
                targets,
            )

        offline_seed = self.seed_guard.extract_seed_id(offline_seed_name)
        if offline_seed and offline_seed != expected_seed:
            status = f"Mismatch: active KH3 saves may be for {offline_seed}, expected {expected_seed}"
            return SaveSeedDecision(expected_seed, status, True, next_restart_seed, targets)
        status = self._associated_save_status(expected_seed, active_targets)
        return SaveSeedDecision(
            expected_seed,
            status,
            status.startswith(("Mismatch:", "Unverified:")),
            next_restart_seed,
            targets,
        )

    def _associated_save_status(self, expected_seed: str, active_targets: tuple[Path, ...]) -> str:
        persistent_kh3 = self.load_persistent_kh3()
        include_scope = len(active_targets) > 1
        for target in active_targets:
            scope_text = f" in {self.seed_guard.save_seed_scope_label(target)}" if include_scope else ""
            persisted_seed, guard_version = self.seed_guard.save_seed_association_for_dir(target, persistent_kh3)
            if persisted_seed and persisted_seed != expected_seed:
                return (
                    f"Mismatch: active KH3 saves{scope_text} were last associated with "
                    f"{persisted_seed}, expected {expected_seed}"
                )
            if not persisted_seed:
                return (
                    f"Unverified: active KH3 saves{scope_text} are not associated with a seed yet; "
                    f"expected {expected_seed}"
                )
            if guard_version < KH3_SAVE_SEED_GUARD_VERSION:
                return (
                    f"Unverified: active KH3 saves{scope_text} were associated by an older client; "
                    f"expected {expected_seed}"
                )
        return f"OK {expected_seed}"

    def refresh_save_seed_guard(
        self,
        *,
        restart_required_seed: str | None,
        offline_seed_name: str | None,
        connected_seed_name: str | None,
        ignore_unverified_warning: bool = False,
        last_warning: str,
    ) -> SaveGuardResult:
        decision = self.build_save_seed_decision(
            restart_required_seed=restart_required_seed,
            offline_seed_name=offline_seed_name,
        )
        if (
            ignore_unverified_warning
            and decision.mismatch
            and decision.status.startswith("Unverified:")
        ):
            return SaveGuardResult(
                True,
                f"Ignored by setting: {decision.status}",
                False,
                "",
                decision.restart_required_seed,
                offline_seed_name,
            )
        warning = last_warning
        if decision.mismatch:
            if decision.status != last_warning:
                warning = decision.status
                self.log_warning(
                    "KH3 check reporting is paused until the connected run's saves are activated. "
                    "%s. The client activates the matching run automatically while KH3 is closed. "
                    "If KH3 is open, close it and reconnect.",
                    decision.status,
                )
            return SaveGuardResult(
                False,
                decision.status,
                True,
                warning,
                decision.restart_required_seed,
                offline_seed_name,
            )

        if decision.expected_seed:
            self.seed_guard.store_save_seed_associations_for_targets(list(decision.targets), decision.expected_seed)
            offline_seed_name = connected_seed_name
        return SaveGuardResult(
            True,
            decision.status,
            False,
            warning,
            decision.restart_required_seed,
            offline_seed_name,
        )

    def disconnect_for_seed_guard(
        self,
        reason: str,
        status: str,
        *,
        restart_required_seed: str | None,
        set_restart_required_seed: Callable[[str | None], None],
        stop_bridge: Callable[[], None],
        clear_locations: Callable[[], None],
        reset_observations: Callable[[str], None],
        write_bridge_files: Callable[[], None],
        schedule_disconnect: Callable[[], bool],
    ) -> DisconnectPolicyResult:
        self.log_warning(
            "Disconnecting from Archipelago because KH3 seed guard blocked this connection: %s. %s",
            reason,
            status,
        )
        expected_seed = self.seed_guard.expected_seed_id()
        if expected_seed and self.process_running():
            self.log_warning(
                "KH3 is currently running. Close and restart KH3 before reconnecting to this seed; "
                "the running game can still emit runtime checks from the previous save."
            )
        if expected_seed:
            restart_required_seed = expected_seed
            set_restart_required_seed(expected_seed)
        stop_bridge()
        clear_locations()
        reset_observations(f"seed_guard_blocked:{reason}")
        write_bridge_files()
        scheduled = schedule_disconnect()
        if not scheduled:
            self.log_info("KH3 seed guard could not schedule auto-disconnect because no asyncio loop is running.")
        return DisconnectPolicyResult(restart_required_seed, scheduled)

    def observe_room_seed(
        self,
        seed_name: Any,
        restart_required_seed: str | None,
    ) -> SeedObservationResult:
        observed_seed = self.seed_guard.extract_seed_id(seed_name)
        if restart_required_seed and observed_seed and restart_required_seed != observed_seed:
            restart_required_seed = None
        return SeedObservationResult(seed_name, restart_required_seed)

    def select_save_directory(self, save_dir: str | Path) -> SaveDirectoryDecision:
        resolved = self.resolve_save_dir_input(save_dir)
        dynamic_root_override = self.is_dynamic_save_root(save_dir)
        return SaveDirectoryDecision(
            resolved,
            True,
            dynamic_root_override,
            str(Path(save_dir).expanduser().resolve()),
            f"KH3 save directory override set to: {resolved}",
        )

    def clear_save_directory(self, kh3_dir: Path) -> SaveDirectoryDecision:
        resolved = self.resolve_save_dir_for_root(kh3_dir)
        display_path = self.save_management.auto_display_save_root_for_save_dir(resolved)
        return SaveDirectoryDecision(
            resolved,
            False,
            False,
            "",
            f"KH3 save directory override cleared; using detected save directory: {display_path}",
        )

    def select_save_directory_for_game_root(
        self,
        kh3_dir: Path,
        *,
        current_save_dir: Path,
        save_dir_override: bool,
        dynamic_root_override: bool,
    ) -> SaveDirectoryDecision | None:
        save_dir_was_auto = not save_dir_override or (
            self.is_auto_save_dir(current_save_dir) and not dynamic_root_override
        )
        if not save_dir_was_auto:
            return None
        resolved = self.resolve_save_dir_for_root(kh3_dir)
        display_path = self.save_management.auto_display_save_root_for_save_dir(resolved)
        return SaveDirectoryDecision(
            resolved,
            False,
            False,
            "",
            f"KH3 save directory auto-detected from install type: {display_path}",
        )


__all__ = [
    "DisconnectPolicyResult",
    "InstalledPakDecision",
    "PakGuardResult",
    "SaveDirectoryDecision",
    "SaveGuardResult",
    "SaveSeedDecision",
    "SeedObservationResult",
    "SeedPolicyService",
]
