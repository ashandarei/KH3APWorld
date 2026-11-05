"""Run catalog, import requests, pruning, and connection profiles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

from ..platform_seed_download import KH3PlatformSeedDownloadService
from ..run_manager import KH3RunConnectionProfile, KH3RunDeleteResult, KH3RunInfo, KH3RunManagerService
from ..run_pruning import RunPruningService
from ..seed_import_service import KH3SeedImportResult, KH3SeedImportService


class ClientRunLibraryMixin:
    """Run catalog, import requests, pruning, and connection profiles."""

    def kh3_run_library_root(self) -> Path:
        return self._client_api.Path(self._client_api.Utils.user_path("kh3", "runs")).resolve()

    def kh3_bridge_root(self) -> Path:
        if self.bridge_root_override is not None:
            return self.bridge_root_override.resolve()
        if sys.platform.startswith("linux") and self.kh3_dir is not None:
            # Heroic/Steam Flatpaks can expose the game while hiding the host's
            # Archipelago data directory. Share files through the game tree.
            return (self.kh3_dir / "Archipelago" / "bridge").resolve()
        return self._client_api.Path(self._client_api.Utils.user_path("kh3", "bridge")).resolve()

    def kh3_generated_pak_root(self) -> Path:
        return self._client_api.Path(self._client_api.Utils.user_path("kh3", "generated-paks")).resolve()

    def _create_run_manager_service(self) -> KH3RunManagerService:
        return self._client_api.KH3RunManagerService(
            run_root=self.kh3_run_library_root,
            bridge_root=self.kh3_bridge_root,
            mods_dir=lambda: self.kh3_mods_dir,
            save_dir=lambda: self.bridge_save_dir,
            process_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            platform=lambda: "steam" if self._client_api.is_likely_steam_kh3_root_dir(self.kh3_dir) else "egs",
            legacy_platforms=lambda: self._client_api.SeedGuardService.legacy_run_platforms(
                self._client_api.Utils.persistent_load().get("kh3_client", {}),
            ),
        )

    def _get_run_manager_service(self) -> KH3RunManagerService:
        service = self.__dict__.get("run_manager_service")
        if not isinstance(service, self._client_api.KH3RunManagerService):
            service = self._create_run_manager_service()
            self.run_manager_service = service
        return service

    def _create_seed_import_service(self) -> KH3SeedImportService:
        return self._client_api.KH3SeedImportService(
            run_manager=self._get_run_manager_service(),
            mods_dir=lambda: self.kh3_mods_dir,
            generated_root=self.kh3_generated_pak_root,
            process_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            unidentified_save_preparer=(
                self._get_save_management_service().preserve_unidentified_saves_before_ap_install
            ),
        )

    def _get_seed_import_service(self) -> KH3SeedImportService:
        service = self.__dict__.get("seed_import_service")
        if not isinstance(service, self._client_api.KH3SeedImportService):
            service = self._create_seed_import_service()
            self.seed_import_service = service
        return service

    def _create_platform_seed_download_service(self) -> KH3PlatformSeedDownloadService:
        return self._client_api.KH3PlatformSeedDownloadService(
            download_root=lambda: self.kh3_generated_pak_root() / "hosted-downloads",
        )

    def _get_platform_seed_download_service(self) -> KH3PlatformSeedDownloadService:
        service = self.__dict__.get("platform_seed_download_service")
        if not isinstance(service, self._client_api.KH3PlatformSeedDownloadService):
            service = self._create_platform_seed_download_service()
            self.platform_seed_download_service = service
        return service

    def list_kh3_runs(self, *, force_refresh: bool = False) -> list[KH3RunInfo]:
        runs = self._get_run_manager_service().list_runs(force_refresh=force_refresh)
        self._observe_active_kh3_run_connection_profile(runs)
        return runs

    def import_kh3_run_source(
        self,
        source_path: Path | str,
        *,
        progress: Callable[[str], None] | None = None,
        server_address: str | None = None,
        connection_slot_name: str | None = None,
        room_url: str | None = None,
    ) -> KH3SeedImportResult:
        return self._get_seed_import_service().import_source(
            source_path,
            progress=progress,
            server_address=server_address,
            connection_slot_name=connection_slot_name,
            room_url=room_url,
        )

    def delete_kh3_run(
        self,
        run_id: str,
        *,
        allow_active: bool = False,
        save_dir_override: Path | str | None = None,
        active_save_seed_id: str | None = None,
        active_save_run_id: str | None = None,
    ) -> KH3RunDeleteResult:
        return self._get_run_manager_service().delete_run(
            run_id,
            active_bridge_dir=self.bridge_dir,
            allow_active=allow_active,
            save_dir_override=save_dir_override,
            active_save_seed_id=active_save_seed_id,
            active_save_run_id=active_save_run_id,
        )

    def open_kh3_run_library(self) -> None:
        self._client_api.open_path(self.kh3_run_library_root(), description="KH3 managed run library", create_directory=True)

    def request_import_kh3_run_pak(
        self,
        pak_path: Path | str,
        *,
        slot_name: str | None = None,
        server: str | None = None,
        port: str | None = None,
        room_url: str | None = None,
    ) -> None:
        self._client_api.Utils.async_start(
            self._import_kh3_run_pak_action(
                pak_path,
                slot_name=slot_name,
                server=server,
                port=port,
                room_url=room_url,
            ),
            name="KH3RunImport",
        )

    def has_kh3_seed_container_source(self) -> bool:
        try:
            return self._get_seed_import_service().has_container_source()
        except OSError:
            return False

    def request_activate_kh3_run(self, run_id: str) -> None:
        self._client_api.Utils.async_start(self._activate_kh3_run_action(run_id), name="KH3RunActivate")

    def request_update_kh3_run_connection_profile(
        self,
        run_id: str,
        *,
        slot_name: str,
        server: str,
        port: str,
        room_url: str,
        show_activity: bool = True,
    ) -> None:
        self._client_api.Utils.async_start(
            self._update_kh3_run_connection_profile_action(
                run_id,
                slot_name=slot_name,
                server=server,
                port=port,
                room_url=room_url,
                show_activity=show_activity,
            ),
            name="KH3RunConnectionProfile",
        )

    def request_delete_kh3_run(self, run_id: str) -> None:
        self._client_api.Utils.async_start(self._delete_kh3_run_action(run_id), name="KH3RunDelete")

    def request_delete_kh3_runs(self, run_ids: tuple[str, ...]) -> None:
        self._client_api.Utils.async_start(self._delete_kh3_runs_action(run_ids), name="KH3RunSelectedDelete")

    def request_delete_inactive_kh3_runs(self, run_ids: tuple[str, ...], active_run_id: str) -> None:
        self._client_api.Utils.async_start(
            self._delete_inactive_kh3_runs_action(run_ids, active_run_id),
            name="KH3RunBulkDelete",
        )

    def set_auto_delete_old_kh3_runs(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == getattr(self, "auto_delete_old_kh3_runs", False):
            return
        self.auto_delete_old_kh3_runs = enabled
        self._client_api.Utils.persistent_store("kh3_client", "auto_delete_old_kh3_runs", enabled)

    def _create_run_pruning_service(self) -> RunPruningService:
        return self._client_api.RunPruningService(
            manager=lambda: self._get_run_manager_service(),
            callbacks=self._client_api.RunPruneCallbacks(
                selection=self._kh3_run_selection,
                set_status=self._set_kh3_run_action_status,
                auto_enabled=lambda: self.auto_delete_old_kh3_runs,
            ),
        )

    async def _auto_delete_old_kh3_runs_after_start(self, active_run_id: str) -> None:
        """Prune stored runs after a successful start, while its action lock is held."""
        if not getattr(self, "auto_delete_old_kh3_runs", False):
            return
        if not getattr(self, "kh3_run_action_running", False):
            return
        await self._create_run_pruning_service().automatic(active_run_id)

    async def _delete_inactive_kh3_runs_action(
        self, run_ids: tuple[str, ...], active_run_id: str, *, current_platform_only: bool = True,
    ) -> None:
        if not run_ids or not self._begin_kh3_run_action("Deleting inactive KH3 runs..."):
            return
        try:
            await self._create_run_pruning_service().selected(
                run_ids, active_run_id, current_platform_only=current_platform_only,
            )
        finally:
            self._finish_kh3_run_action()

    def _observe_active_kh3_run_connection_profile(self, runs: list[KH3RunInfo]) -> None:
        active_run = next((run for run in runs if run.active and run.managed), None)
        if active_run is None:
            if getattr(self, "kh3_active_connection_profile_run_id", None) is not None:
                self.kh3_active_connection_profile_run_id = None
                self.kh3_run_connection_slot_name = ""
            return
        if getattr(self, "kh3_active_connection_profile_run_id", None) == active_run.run_id:
            return
        self._apply_active_kh3_run_connection_profile(active_run)

    def _set_active_kh3_run_profile(self, profile: KH3RunInfo | KH3RunConnectionProfile) -> None:
        self.kh3_active_connection_profile_run_id = profile.run_id
        self.kh3_run_connection_slot_name = str(profile.connection_slot_name or "").strip()

    def _apply_active_kh3_run_connection_profile(
        self,
        profile: KH3RunInfo | KH3RunConnectionProfile,
    ) -> None:
        self._set_active_kh3_run_profile(profile)
        if getattr(self, "server", None) is None:
            self.auth = profile.effective_slot_name
            self._apply_kh3_run_server_address(profile)

    def _apply_edited_kh3_run_connection_profile(self, updated: KH3RunInfo) -> None:
        self._apply_active_kh3_run_connection_profile(updated)
        if getattr(self, "server", None) is not None:
            ui = getattr(self, "ui", None)
            if ui is not None:
                ui.update_address_bar(updated.server_address)
            self._client_api.logger.info("The edited KH3 connection profile will be used on the next reconnect.")

    async def _update_kh3_run_connection_profile_action(
        self,
        run_id: str,
        *,
        slot_name: str,
        server: str,
        port: str,
        room_url: str,
        show_activity: bool = True,
    ) -> None:
        service = self._client_api.RunConnectionService(
            manager=lambda: self._get_run_manager_service(),
            callbacks=self._client_api.RunConnectionCallbacks(
                begin=self._begin_kh3_run_action,
                finish=self._finish_kh3_run_action,
                is_busy=lambda: bool(getattr(self, "kh3_run_action_running", False)),
                busy_label=lambda: getattr(self, "kh3_run_action_label", ""),
                apply_active=self._apply_edited_kh3_run_connection_profile,
            ),
        )
        await service.update(
            run_id, slot_name=slot_name, server=server, port=port, room_url=room_url, show_activity=show_activity
        )

    def connected_kh3_seed_pak_path(self) -> tuple[Path, str] | None:
        expected_seed = self.expected_seed_id()
        installed = self.installed_kh3_ap_seed_paks()
        if expected_seed is None or len(installed) != 1:
            return None
        pak = installed[0]
        installed_seed = self._client_api.SeedGuardService.extract_seed_id(pak.get("seed"))
        raw_path = str(pak.get("path") or "").strip()
        if installed_seed != expected_seed or not raw_path:
            return None
        return self._client_api.Path(raw_path).expanduser().resolve(), expected_seed
