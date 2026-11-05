"""Ordered run switching and deletion, including recovery after partial work.

The run manager owns file transactions. The client owns AP state and the shared
action lock. This service coordinates those boundaries through explicit callbacks;
it never reaches into the client context or manipulates save files itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .run_manager import (
    KH3RunActivationResult,
    KH3RunDeleteResult,
    KH3RunInfo,
    KH3RunManagerError,
    KH3RunManagerService,
)

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class RunSelection:
    """Client selection captured while its run-action lock is held."""

    bridge_dir: Path | None
    profile_id: str | None


@dataclass(frozen=True)
class ActiveRunSaves:
    directory: Path
    seed_id: str | None
    run_id: str | None


class StopRunBridge(Protocol):
    async def __call__(self, *, disconnect: bool) -> None: ...


@dataclass(frozen=True)
class RunActionCallbacks:
    """Client-side effects; all except delete_run execute on the event loop.

    stop_bridge must return only after the worker has actually stopped. File
    operations cannot proceed merely because cancellation was requested.
    """

    set_status: Callable[[str], None]
    active_saves: Callable[[], ActiveRunSaves]
    stop_bridge: StopRunBridge
    restore_bridge: Callable[[Path], None]
    select_run: Callable[[KH3RunActivationResult, Path], None]
    initialize_run: Callable[[KH3RunActivationResult], bool]
    retain_activated_run: Callable[[KH3RunActivationResult], None]
    cleanup_old_runs: Callable[[str], Awaitable[None]]
    show_switch_warning: Callable[[], None]
    is_connected: Callable[[], bool]
    delete_run: Callable[..., KH3RunDeleteResult]
    clear_deleted_run: Callable[[str, Path | None], None]


@dataclass
class _ActivationProgress:
    bridge_stopped: bool = False
    result: KH3RunActivationResult | None = None


class RunActionService:
    """Execute one action under the caller's existing run-action lock."""

    def __init__(
        self,
        *,
        run_manager: Callable[[], KH3RunManagerService],
        client: RunActionCallbacks,
        game_running: Callable[[], bool],
    ) -> None:
        self._run_manager = run_manager
        self._client = client
        self._game_running = game_running

    async def activate(self, run_id: str, selection: RunSelection) -> None:
        progress = _ActivationProgress()
        try:
            if self._game_running():
                logger.warning("Close KINGDOM HEARTS III before switching managed runs.")
                self._client.show_switch_warning()
                return
            saves = self._client.active_saves()
            self._client.set_status("Stopping the current KH3 save watcher...")
            await self._client.stop_bridge(disconnect=True)
            progress.bridge_stopped = True

            self._client.set_status("Saving the current run and loading the selected saves...")
            result = await self._activate_files(run_id, saves)
            # From here, an error must retain the new identity. Restoring the old
            # watcher would pair the old bridge with the newly activated files.
            progress.result = result
            self._client.select_run(result, saves.directory)
            self._client.set_status("Initializing the selected KH3 run...")
            initialized = self._client.initialize_run(result)
            self._log_activation(result)
            for warning in result.warnings:
                logger.warning("KH3 run switch warning: %s", warning)
            if result.switched and initialized:
                await self._client.cleanup_old_runs(result.run.run_id)
        except Exception as exc:
            logger.warning("Could not switch KH3 run: %s", exc, exc_info=not isinstance(exc, KH3RunManagerError))
            self._recover_activation(selection, progress)

    async def _activate_files(self, run_id: str, saves: ActiveRunSaves) -> KH3RunActivationResult:
        options: dict[str, Any] = {
            "save_dir_override": saves.directory,
            "active_save_seed_id": saves.seed_id,
        }
        if saves.run_id:
            options["active_save_run_id"] = saves.run_id
        return await asyncio.to_thread(self._run_manager().activate, run_id, **options)

    def _recover_activation(self, selection: RunSelection, progress: _ActivationProgress) -> None:
        if progress.result is not None:
            self._client.retain_activated_run(progress.result)
        elif progress.bridge_stopped and selection.bridge_dir is not None:
            self._client.restore_bridge(selection.bridge_dir)

    @staticmethod
    def _log_activation(result: KH3RunActivationResult) -> None:
        logger.info(
            "Activated KH3 run %s (seed %s, player %s): stored %s current save file(s), "
            "restored %s selected save file(s).",
            result.run.slot_name,
            result.run.seed_id,
            result.run.player,
            result.saved_current_files,
            result.restored_target_files,
        )

    async def delete(self, run_id: str, selection: RunSelection) -> bool:
        bridge_stopped = False
        deletion_completed = False
        try:
            run = self._run_manager().get_run(run_id)
            if (run.active or run.installed) and self._game_running():
                raise KH3RunManagerError("Close KINGDOM HEARTS III before deleting an installed run.")
            deleting_selected = self._deletes_selected_profile(run, selection)
            saves = self._client.active_saves() if run.active else None
            if deleting_selected:
                self._client.set_status("Stopping the current KH3 save watcher...")
                await self._client.stop_bridge(disconnect=False)
                bridge_stopped = True
            # Connection state can change while we await worker shutdown.
            if deleting_selected and self._client.is_connected():
                raise KH3RunManagerError("The Archipelago client connected while deletion was being prepared.")
            result = await self._delete_files(run_id, deleting_selected, saves)
            deletion_completed = True
            if result.cleared_active_profile:
                self._client.clear_deleted_run(run_id, saves.directory if saves is not None else None)
            self._log_deletion(result)
            return not result.errors
        except Exception as exc:
            message = (
                "The KH3 run was deleted, but client cleanup did not finish: %s"
                if deletion_completed
                else "Could not delete KH3 run: %s"
            )
            logger.warning(message, exc, exc_info=not isinstance(exc, KH3RunManagerError))
            if not deletion_completed and bridge_stopped and selection.bridge_dir is not None:
                self._client.restore_bridge(selection.bridge_dir)
            return False

    @staticmethod
    def _deletes_selected_profile(run: KH3RunInfo, selection: RunSelection) -> bool:
        # Managed profiles can share a pak and bridge. Only unmanaged runs may
        # use the bridge path alone as evidence that they are selected.
        bridge_matches = (
            selection.bridge_dir is not None and selection.bridge_dir.expanduser().resolve() in run.bridge_dirs
        )
        return bool(run.active or selection.profile_id == run.run_id or (bridge_matches and not run.managed))

    async def _delete_files(
        self,
        run_id: str,
        deleting_selected: bool,
        saves: ActiveRunSaves | None,
    ) -> KH3RunDeleteResult:
        options: dict[str, Any] = {"allow_active": deleting_selected}
        if saves is not None:
            options.update(
                {
                    "save_dir_override": saves.directory,
                    "active_save_seed_id": saves.seed_id,
                    "active_save_run_id": saves.run_id,
                }
            )
        return await asyncio.to_thread(self._client.delete_run, run_id, **options)

    @staticmethod
    def _log_deletion(result: KH3RunDeleteResult) -> None:
        if result.errors:
            logger.warning(
                "Deleted KH3 run %s partially: removed run storage=%s, installed pak=%s, %s active save "
                "file(s), and %s bridge folder(s), with %s error(s).",
                result.run_id,
                result.removed_run,
                result.removed_installed_pak,
                result.removed_active_save_files,
                result.removed_bridge_dirs,
                len(result.errors),
            )
            for error in result.errors[:5]:
                logger.warning("KH3 run deletion error: %s", error)
        else:
            logger.info(
                "Deleted KH3 run %s, installed pak=%s, %s active save file(s), and %s matching bridge folder(s).",
                result.run_id,
                result.removed_installed_pak,
                result.removed_active_save_files,
                result.removed_bridge_dirs,
            )
