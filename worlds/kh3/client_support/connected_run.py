"""Ordered connected-run preservation and seed bootstrap actions.

The run manager and seed importer own file transactions. This coordinator owns
worker waits, failures, and action lifetime; the client owns state updates.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .run_actions import ActiveRunSaves
from .run_manager import KH3RunInfo, KH3RunManagerError, KH3RunManagerService, KH3RunSuspensionResult
from .seed_import_service import KH3SeedImportError, KH3SeedImportService, KH3SeedPreparationResult

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class ConnectedRunCallbacks:
    begin: Callable[[str], bool]
    finish: Callable[[], None]
    warn: Callable[[str], None]
    connected: Callable[[], bool]
    disconnect: Callable[[], Awaitable[None]]
    ensure_companion: Callable[[], object]
    cleanup_old_runs: Callable[[str], Awaitable[None]]
    finish_setup: Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class SuspensionCallbacks:
    bridge_dir: Callable[[], Path | None]
    saves: Callable[[], ActiveRunSaves]
    game_running: Callable[[], bool]
    stop_bridge: Callable[[], None]
    wait_bridge: Callable[[], Awaitable[None]]
    restore_bridge: Callable[[Path], None]
    apply: Callable[[KH3RunSuspensionResult, Path], None]


@dataclass(frozen=True)
class AutoActivationRequest:
    pak_path: Path
    expected_seed: str
    slot: int | None
    server_address: str | None
    slot_name: str | None
    room_url: str | None
    saves: ActiveRunSaves


@dataclass(frozen=True)
class AutoActivationCallbacks:
    still_connected: Callable[[], bool]
    disable_checks: Callable[[], None]
    wait_bridge: Callable[[], Awaitable[None]]
    request: Callable[[], AutoActivationRequest]
    remember_save_owner: Callable[[KH3RunInfo, Path], None]
    apply: Callable[[KH3RunInfo], None]
    disconnect_guard: Callable[[str], None]
    clear_pending: Callable[[], None]


class ConnectedRunService:
    def __init__(self, manager: Callable[[], KH3RunManagerService], callbacks: ConnectedRunCallbacks) -> None:
        self._manager = manager
        self._client = callbacks

    async def suspend(self, *, reason: str, suspension: SuspensionCallbacks) -> bool:
        if not self._client.begin("Preserving the previous KH3 run..."):
            self._client.warn("Could not move the previous seed pak because another KH3 run action is active.")
            return False

        previous_bridge_dir = suspension.bridge_dir()
        bridge_stopped = False
        try:
            if suspension.game_running():
                raise KH3RunManagerError("Close KINGDOM HEARTS III before changing the active seed pak.")
            saves = suspension.saves()
            selected_save_dir, active_save_seed_id, active_save_run_id = saves.directory, saves.seed_id, saves.run_id
            suspension.stop_bridge()
            await suspension.wait_bridge()
            bridge_stopped = True
            suspension_kwargs: dict[str, Any] = {
                "save_dir_override": selected_save_dir,
                "active_save_seed_id": active_save_seed_id,
            }
            if active_save_run_id:
                suspension_kwargs["active_save_run_id"] = active_save_run_id
            result = await asyncio.to_thread(
                self._manager().suspend_installed_run,
                **suspension_kwargs,
            )
            suspension.apply(result, selected_save_dir)
            save_run = result.save_run or result.run
            if save_run.run_id == result.run.run_id:
                logger.info(
                    "Preserved KH3 run %s (seed %s), stored %s current save file(s), and moved its live pak out "
                    "of ~mods because %s.",
                    result.run.slot_name,
                    result.run.seed_id,
                    result.saved_current_files,
                    reason,
                )
            else:
                logger.info(
                    "Preserved installed KH3 run %s (seed %s), stored %s current save file(s) with matching "
                    "managed run %s (seed %s), and moved the stale live pak out of ~mods because %s.",
                    result.run.slot_name,
                    result.run.seed_id,
                    result.saved_current_files,
                    save_run.slot_name,
                    save_run.seed_id,
                    reason,
                )
            for warning in result.warnings:
                logger.warning("KH3 run preservation warning: %s", warning)
            return True
        except Exception as exc:
            self._client.warn(f"Could not preserve the previous KH3 run: {exc}")
            if not isinstance(exc, KH3RunManagerError):
                logger.debug("Unexpected KH3 run-preservation failure", exc_info=True)
            if bridge_stopped and previous_bridge_dir is not None:
                suspension.restore_bridge(previous_bridge_dir)
            return False
        finally:
            self._client.finish()

    async def bootstrap(
        self,
        args: dict[str, Any],
        *,
        prepare: Callable[[], KH3SeedPreparationResult],
        apply: Callable[[KH3SeedPreparationResult], None],
    ) -> None:
        if not self._client.begin("Preparing the connected KH3 seed pak..."):
            self._client.warn("Could not prepare the connected KH3 seed because another KH3 run action is active.")
            await self._client.disconnect()
            return

        prepared: KH3SeedPreparationResult | None = None
        try:
            prepared = await asyncio.to_thread(prepare)
            run = prepared.installed_run
            if run is None:
                raise KH3SeedImportError("The server seed did not produce an installed KH3 run.")
            await asyncio.to_thread(self._client.ensure_companion)
            apply(prepared)
            logger.info(
                "Prepared, managed, and installed the connected KH3 run %s (seed %s, player %s).",
                run.slot_name,
                run.seed_id,
                run.player,
            )
            for warning in prepared.warnings:
                logger.warning("KH3 seed preparation warning: %s", warning)
            if self._client.connected():
                await self._client.cleanup_old_runs(run.run_id)
        except Exception as exc:
            self._client.warn(f"Could not prepare the KH3 seed from the server: {exc}")
            if not isinstance(exc, (KH3SeedImportError, KH3RunManagerError)):
                logger.debug("Unexpected KH3 connected-seed preparation failure", exc_info=True)
        finally:
            self._client.finish()

        if prepared is None:
            await self._client.disconnect()
            return
        if not self._client.connected():
            logger.info("The KH3 seed pak was installed, but the server disconnected before setup completed.")
            return
        self._client.finish_setup(args)

    async def prepare_companion(
        self,
        args: dict[str, Any],
    ) -> None:
        if not self._client.begin("Preparing the Olympus Coliseum AP reward companion..."):
            logger.warning(
                "Could not build the missing Olympus Coliseum AP reward companion because another KH3 run action "
                "is active. Reconnect after that action finishes."
            )
            self._client.finish_setup(args)
            return
        try:
            await asyncio.to_thread(self._client.ensure_companion)
        finally:
            self._client.finish()

        if not self._client.connected():
            logger.info(
                "The Olympus Coliseum AP reward companion was prepared, but the server disconnected before "
                "KH3 setup completed."
            )
            return
        self._client.finish_setup(args)

    async def auto_activate(
        self,
        args: dict[str, Any],
        *,
        importer: Callable[[], KH3SeedImportService],
        activation: AutoActivationCallbacks,
    ) -> None:
        started = self._client.begin("Activating the connected KH3 run...")
        try:
            if not started:
                raise KH3RunManagerError("Another run action is active; reconnect when it finishes.")
            activation.disable_checks()
            await activation.wait_bridge()
            if not activation.still_connected():
                return
            request = activation.request()
            prepared = await asyncio.to_thread(
                importer().activate_installed_run_for_connection,
                request.pak_path,
                expected_seed_id=request.expected_seed,
                expected_slot=request.slot,
                server_address=request.server_address,
                connection_slot_name=request.slot_name,
                room_url=request.room_url,
                save_dir_override=request.saves.directory,
                active_save_seed_id=request.saves.seed_id,
                active_save_run_id=request.saves.run_id,
            )
            run = prepared.installed_run
            if run is None:
                raise KH3RunManagerError("Automatic activation did not produce a managed run.")
            # A completed file transaction retains its save owner after disconnection.
            activation.remember_save_owner(run, request.saves.directory)
            if not activation.still_connected():
                return
            activation.apply(run)
            logger.info(
                "Automatically activated KH3 run %s (seed %s); continuing the connection.", run.slot_name, run.seed_id
            )
            for warning in prepared.warnings:
                logger.warning("KH3 automatic activation: %s", warning)
            self._client.finish()
            started = False
            # Keep pending set through the guard recheck to avoid repeated activation.
            self._client.finish_setup(args)
        except Exception as exc:
            logger.warning("Could not automatically activate the connected KH3 run: %s", exc)
            if activation.still_connected():
                activation.disconnect_guard(str(exc))
        finally:
            if started:
                self._client.finish()
            activation.clear_pending()
