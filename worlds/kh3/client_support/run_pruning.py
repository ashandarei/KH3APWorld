"""Prune inactive runs with live selection checks between file transactions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from .run_actions import RunSelection
from .run_manager import KH3RunManagerError, KH3RunManagerService

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class RunPruneCallbacks:
    selection: Callable[[], RunSelection]
    set_status: Callable[[str], None]
    auto_enabled: Callable[[], bool]


class RunPruningService:
    def __init__(self, manager: Callable[[], KH3RunManagerService], callbacks: RunPruneCallbacks) -> None:
        self._manager = manager
        self._client = callbacks

    async def automatic(self, active_run_id: str) -> None:
        deleted = 0
        try:
            manager = self._manager()
            runs = await asyncio.to_thread(manager.list_runs, force_refresh=True)
            if [run.run_id for run in runs if run.active] != [active_run_id]:
                raise KH3RunManagerError("The newly started KH3 run is no longer the active run.")
            targets = [run.run_id for run in runs if run.managed and not run.active and manager.matches_platform(run)]
            for run_id in targets:
                if not self._client.auto_enabled():
                    break
                self._check_selection(active_run_id, "run")
                self._client.set_status(f"Deleting old run {deleted + 1} of {len(targets)}...")
                await self._delete(manager, run_id, active_run_id)
                deleted += 1
            if deleted:
                logger.info("Automatically deleted %s old KH3 run(s); the active run was kept.", deleted)
        except Exception as exc:
            logger.warning("Automatic old-run deletion stopped after %s deletion(s): %s", deleted, exc)

    async def selected(
        self, run_ids: tuple[str, ...], active_run_id: str, *, current_platform_only: bool = True,
    ) -> None:
        deleted = 0
        try:
            manager = self._manager()
            for run_id in dict.fromkeys(run_ids):
                self._check_selection(active_run_id, "profile")
                self._client.set_status(f"Deleting inactive run {deleted + 1} of {len(run_ids)}...")
                await self._delete(manager, run_id, active_run_id, current_platform_only=current_platform_only)
                deleted += 1
                logger.info("Deleted inactive KH3 run %s.", run_id)
            logger.info("Deleted %s inactive KH3 run(s). The active run was kept.", deleted)
        except Exception as exc:
            logger.warning("Bulk KH3 run deletion stopped after %s completed deletion(s): %s", deleted, exc)

    def _check_selection(self, active_run_id: str, label: str) -> None:
        selected = self._client.selection().profile_id
        if selected and selected != active_run_id:
            raise KH3RunManagerError(f"The selected KH3 {label} changed; remaining runs were kept.")

    async def _delete(
        self, manager: KH3RunManagerService, run_id: str, active_run_id: str, *, current_platform_only: bool = True,
    ) -> None:
        result = await asyncio.to_thread(
            manager.delete_inactive_run,
            run_id,
            expected_active_run_id=active_run_id,
            active_bridge_dir=self._client.selection().bridge_dir,
            current_platform_only=current_platform_only,
        )
        if result.errors:
            raise KH3RunManagerError(f"Run {run_id} was partially deleted: {'; '.join(result.errors)}")
