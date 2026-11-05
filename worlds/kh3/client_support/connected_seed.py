"""Choose hosted player artifacts or inline seed data for a connected room."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .platform_seed_download import KH3PlatformSeedDownloadError, KH3PlatformSeedDownloadService
from .run_actions import ActiveRunSaves
from .run_manager import KH3RunManagerError
from .seed_import_service import KH3SeedImportError, KH3SeedImportService, KH3SeedPreparationResult

logger = logging.getLogger("Client")


@dataclass(frozen=True)
class ConnectedSeedRequest:
    slot_data: dict[str, Any]
    server_address: str
    slot: int | None
    slot_name: str | None
    seed_name: str | None


class ConnectedSeedService:
    def __init__(
        self, importer: Callable[[], KH3SeedImportService], downloader: Callable[[], KH3PlatformSeedDownloadService]
    ) -> None:
        self._importer = importer
        self._downloader = downloader

    def prepare(
        self,
        request: ConnectedSeedRequest,
        *,
        resolve_saves: Callable[[], ActiveRunSaves],
        fallback_room: Callable[[], str | None],
        progress: Callable[[str], None],
    ) -> KH3SeedPreparationResult:
        save_options: dict[str, Any] = {}
        try:
            saves = resolve_saves()
            save_options = {
                "save_dir_override": saves.directory,
                "active_save_seed_id": saves.seed_id,
                "active_save_run_id": saves.run_id,
            }
        except Exception:
            logger.debug("Could not resolve the active KH3 save profile during connected seed preparation.")
        options = {
            "server_address": request.server_address,
            "expected_slot": request.slot,
            "expected_slot_name": request.slot_name,
            "expected_seed_name": request.seed_name,
            **save_options,
            "progress": progress,
        }
        downloader = self._downloader()
        hosted_error: Exception | None = None
        if downloader.has_platform_room(request.slot_data):
            logger.info(
                "KH3 connected seed source selection: a hosted player artifact was advertised; "
                "attempting the download for slot %r.",
                str(request.slot_name or ""),
            )
            try:
                downloaded = downloader.download_player_artifact(
                    request.slot_data,
                    slot_name=str(request.slot_name or ""),
                    fallback_room_url=fallback_room(),
                    progress=progress,
                )
                if downloaded is not None:
                    return self._importer().prepare_player_artifact(downloaded.source_path, **options)
            except (KH3PlatformSeedDownloadError, KH3SeedImportError, KH3RunManagerError) as exc:
                hosted_error = exc
        else:
            logger.info(
                "KH3 connected seed source selection: no hosted player artifact was advertised by %s; "
                "using the inline seed payload for local generation.",
                request.server_address or "the connected server",
            )
        if hosted_error is not None:
            inline_seed = request.slot_data.get("kh3_seed")
            if not (isinstance(inline_seed, dict) and inline_seed.get("format") == "kh3_server_seed"):
                raise KH3SeedImportError(str(hosted_error)) from hosted_error
            logger.warning(
                "Could not use the hosted KH3 player file; generating from connected slot data instead: %s",
                hosted_error,
            )
            progress("Hosted player-file download was unavailable; generating the KH3 seed pak locally instead...")
        return self._importer().prepare_server_slot_data(request.slot_data, **options)
