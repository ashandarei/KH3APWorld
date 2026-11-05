"""Client actions for importing, preparing, and enrolling managed seeds.

File transactions remain in the importer and run manager. The client supplies
its action lock, current slot, preparation effects, and warnings as callbacks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .run_manager import KH3RunEnrollmentResult, KH3RunManagerError, KH3RunManagerService, format_kh3_run_server_address
from .seed_import_service import KH3SeedImportError, KH3SeedImportResult, KH3SeedImportService, KH3SeedPreparationResult
from .seed_paks import infer_kh3_ap_slot_name_from_pak_name

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class SeedPreparationCallbacks:
    begin: Callable[[str], bool]
    finish: Callable[[], None]
    set_status: Callable[[str], None]
    has_installed_pak: Callable[[], bool]
    preferred_slot: Callable[[], str | None]
    import_source: Callable[..., KH3SeedImportResult]
    apply_prepared: Callable[[KH3SeedPreparationResult], bool]
    ensure_companion: Callable[[], object]
    cleanup_old_runs: Callable[[str], Awaitable[None]]
    set_warning: Callable[[str], None]


class SeedPreparationService:
    def __init__(
        self,
        importer: Callable[[], KH3SeedImportService],
        manager: Callable[[], KH3RunManagerService],
        callbacks: SeedPreparationCallbacks,
    ) -> None:
        self._importer = importer
        self._manager = manager
        self._client = callbacks

    async def import_source(
        self,
        pak_path: Path | str,
        *,
        slot_name: str | None = None,
        server: str | None = None,
        port: str | None = None,
        room_url: str | None = None,
    ) -> None:
        source_path = Path(pak_path).expanduser().resolve()
        action_label = (
            "Importing seed pak..."
            if source_path.suffix.casefold() == ".pak"
            else "Generating and importing seed pak..."
        )
        if not self._client.begin(action_label):
            return
        try:
            profile_requested = any(value is not None for value in (slot_name, server, port, room_url))
            profile_kwargs: dict[str, str | None] = {}
            if profile_requested:
                if slot_name is None or server is None or port is None:
                    raise KH3RunManagerError("The KH3 run import connection profile is incomplete.")
                profile_kwargs = {
                    "server_address": format_kh3_run_server_address(server, port),
                    "connection_slot_name": slot_name,
                    "room_url": room_url or "",
                }
            result = await asyncio.to_thread(
                self._client.import_source,
                source_path,
                progress=self._client.set_status,
                **profile_kwargs,
            )
            logger.info(
                "Imported %s KH3 AP run(s) from %s into managed storage.",
                len(result.runs),
                result.source_path,
            )
            for run in result.runs:
                logger.info("KH3 run available: %s (seed %s, player %s).", run.slot_name, run.seed_id, run.player)
            if result.archived_source_path is not None:
                logger.info("Moved processed KH3 seed source out of ~mods to %s.", result.archived_source_path)
            for warning in result.warnings:
                logger.warning("KH3 seed import warning: %s", warning)
        except Exception as exc:
            logger.warning(
                "Could not import KH3 AP seed source: %s",
                exc,
                exc_info=not isinstance(exc, (KH3SeedImportError, KH3RunManagerError)),
            )
        finally:
            self._client.finish()

    async def prepare_missing(self, *, trigger: str) -> KH3SeedPreparationResult | None:
        if self._client.has_installed_pak():
            return KH3SeedPreparationResult("already_installed")
        service = self._importer()
        if not service.has_container_source():
            return KH3SeedPreparationResult("no_source")
        if not self._client.begin(f"Generating seed pak before {trigger}..."):
            return None
        try:
            result = await asyncio.to_thread(
                service.prepare_missing_installed_pak,
                preferred_slot_name=self._client.preferred_slot(),
                progress=self._client.set_status,
            )
            bridge_ready = self._client.apply_prepared(result)
            if result.installed_run is not None:
                await asyncio.to_thread(self._client.ensure_companion)
            if result.installed_run is not None and trigger == "launching KH3" and not bridge_ready:
                logger.warning(
                    "KH3 was not launched because the generated pak could not initialize its offline bridge."
                )
                return None
            if result.installed_run is not None and bridge_ready:
                await self._client.cleanup_old_runs(result.installed_run.run_id)
            return result
        except Exception as exc:
            message = f"Could not prepare a KH3 seed pak before {trigger}: {exc}"
            self._client.set_warning(message)
            logger.warning(message, exc_info=not isinstance(exc, KH3SeedImportError))
            return None
        finally:
            self._client.finish()

    async def enroll(
        self,
        pak_path: Path,
        expected_seed: str,
        server_address: str | None,
        room_url: str | None = None,
        connection_slot_name: str | None = None,
    ) -> None:
        if not self._client.begin("Storing seed in Save Manager..."):
            logger.info(
                "Could not automatically store the KH3 seed pak yet because another run action is active: %s",
                pak_path,
            )
            return
        try:
            resolved_connection_slot = str(connection_slot_name or "").strip()
            if server_address and not resolved_connection_slot:
                resolved_connection_slot = infer_kh3_ap_slot_name_from_pak_name(pak_path) or ""
            result: KH3RunEnrollmentResult = await asyncio.to_thread(
                self._manager().ensure_pak_managed,
                pak_path,
                expected_seed_id=expected_seed,
                server_address=server_address,
                connection_slot_name=(resolved_connection_slot if server_address else None),
                room_url=room_url,
            )
            if result.copied:
                logger.info(
                    "Automatically added KH3 run %s (seed %s, player %s) to managed storage.",
                    result.run.slot_name,
                    result.run.seed_id,
                    result.run.player,
                )
            if server_address:
                logger.info(
                    "Remembered AP server %s for KH3 run %s.",
                    result.run.server_address,
                    result.run.slot_name,
                )
            if room_url:
                logger.info("Remembered room page %s for KH3 run %s.", result.run.room_url, result.run.slot_name)
        except Exception as exc:
            logger.warning(
                "Could not automatically store the KH3 run: %s", exc, exc_info=not isinstance(exc, KH3RunManagerError)
            )
        finally:
            self._client.finish()
