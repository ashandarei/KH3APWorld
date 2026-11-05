"""Runtime installation, launch preparation, GoA, MoM, and diagnostic export."""

from __future__ import annotations

from typing import Any

from ..command_executor import ClientCommandExecutor
from ..mom_client import MoMClientRoute, MoMClientService
from ..olympus_coliseum_compat import OlympusColiseumCompatResult
from ..runtime_artifact_executor import RuntimeArtifactExecutor
from ..runtime_artifact_service import RuntimeArtifactPlan


class ClientRuntimeMixin:
    """Runtime installation, launch preparation, GoA, MoM, and diagnostic export."""

    def download_goa_mod(self) -> None:
        self._apply_client_facade_plan(
            self._get_client_command_service().plan_facade_command(
                self._client_api.ClientCommandKind.DOWNLOAD_GOA_MOD,
            )
        )

    def goa_mod_status(self) -> dict[str, Any]:
        return self._client_api.goa_mod_install_status(
            self.kh3_mods_dir,
            cache_path=self._client_api.goa_mod_status_cache_path(),
            allow_hash=False,
        )

    def refresh_goa_mod_status(self) -> dict[str, Any]:
        return self._client_api.goa_mod_install_status(
            self.kh3_mods_dir,
            cache_path=self._client_api.goa_mod_status_cache_path(),
            allow_hash=True,
        )

    def has_goa_mod(self) -> bool:
        present = self.kh3_mods_dir.is_dir() and any(self.kh3_mods_dir.glob(self._client_api.KH3_GOA_MOD_PATTERN))
        return (
            self._get_runtime_artifact_service()
            .summarize_artifacts(
                self._client_api.ArtifactStatusInput(
                    goa_present=present,
                )
            )
            .goa_present
        )

    def backup_vanilla_saves_before_ap_install(self) -> None:
        self._get_save_management_service().backup_vanilla_saves_before_ap_install()

    def ensure_goa_mod_installed(self) -> None:
        self._client_api.logger.info("Checking Garden of Assemblage mod before/with KH3 runtime install.")
        self.download_goa_mod()

    def open_kh3_save_dir(self) -> None:
        self._apply_client_facade_plan(
            self._get_client_command_service().plan_facade_command(
                self._client_api.ClientCommandKind.OPEN_KH3_SAVE_DIRECTORY,
            )
        )

    def _reconcile_olympus_coliseum_reward_companion(
        self,
        *,
        log_missing: bool = True,
    ) -> OlympusColiseumCompatResult | None:
        return self._client_api.launch_preparation_support.reconcile_reward_companion(
            getattr(self, "kh3_mods_dir", None),
            getattr(self, "slot_data", None),
            game_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            log_missing=log_missing,
        )

    def _ensure_olympus_coliseum_reward_companion(self) -> OlympusColiseumCompatResult | None:
        return self._client_api.launch_preparation_support.ensure_reward_companion(
            self.kh3_mods_dir,
            self.slot_data,
            game_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            set_status=self._set_kh3_run_action_status,
        )

    async def _prepare_and_launch_kh3_game(self) -> None:
        preparation = await self._prepare_missing_seed_pak(trigger="launching KH3")
        if preparation is None:
            return
        if preparation.installed_run is not None and not self.refresh_save_seed_guard():
            self._client_api.logger.warning(
                "KH3 was not launched because the generated seed pak is not safe for the active save folder: %s",
                self.build_save_seed_status(),
            )
            return
        self._launch_kh3_game_now()

    def launch_kh3_game(self) -> None:
        try:
            self._client_api.asyncio.get_running_loop()
        except RuntimeError:
            if self.has_kh3_seed_container_source() and not self.has_installed_kh3_ap_seed_pak():
                self._client_api.logger.warning(
                    "KH3 could not prepare the seed container because no client event loop is running. "
                    "Launch KH3 from the graphical client or import the seed source from Save Manager first."
                )
                return
            self._launch_kh3_game_now()
            return
        self._client_api.Utils.async_start(self._prepare_and_launch_kh3_game(), name="KH3PrepareAndLaunch")

    def _get_mom_client_service(self) -> MoMClientService:
        service = getattr(self, "_mom_client_service", None)
        if service is None:
            service = self._client_api.MoMClientService()
            self._mom_client_service = service
        return service

    @property
    def mom_launch_running(self) -> bool:
        return self._get_mom_client_service().launch_running

    @mom_launch_running.setter
    def mom_launch_running(self, value: bool) -> None:
        self._get_mom_client_service().launch_running = value

    def _get_mom_challenge_service(self):
        return self._get_mom_client_service().challenge(self.bridge_dir, getattr(self, "slot_data", {}))

    def _mom_client_route(self) -> MoMClientRoute:
        return self._client_api.MoMClientRoute(
            barrier_dir=self._client_api.Path(getattr(self, "mom_barrier_bridge_dir", self.bridge_dir)),
            slot=self.slot,
            seed_name=self.seed_name,
            checked_locations=frozenset(self.checked_locations),
        )

    def launch_mom_challenge(self, location_id: int) -> None:
        self._get_mom_client_service().launch(
            location_id,
            current=lambda: self._get_mom_challenge_service(),
            start_task=lambda task: self._client_api.Utils.async_start(task, name="KH3MoMChallengeLaunch"),
        )

    async def handle_mom_challenge_results(self) -> None:
        await self._get_mom_client_service().poll(
            current=lambda: self._get_mom_challenge_service(),
            route=self._mom_client_route,
            is_connected=self._is_ap_connected,
            launch=self.launch_mom_challenge,
            send=self.send_msgs,
        )

    def install_kh3_runtime_files(self) -> None:
        self._apply_client_facade_plan(
            self._get_client_command_service().plan_facade_command(
                self._client_api.ClientCommandKind.INSTALL_KH3_RUNTIME,
                self._client_api.FacadeCommandInput(game_running=self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME)),
            )
        )

    def _apply_runtime_artifact_plan(self, plan: RuntimeArtifactPlan) -> None:
        self._create_runtime_artifact_executor().apply_plan(plan)

    def _create_runtime_artifact_executor(self) -> RuntimeArtifactExecutor:
        return self._client_api.RuntimeArtifactExecutor(
            self._get_runtime_artifact_service(),
            self._client_api.RuntimeArtifactCallbacks(
                root=lambda: self.kh3_dir,
                mods=lambda: self.kh3_mods_dir,
                bridge=lambda: self.bridge_dir,
                saves=lambda: self.bridge_save_dir,
                invalidate_runtime=lambda: self._get_client_status_presenter().invalidate_runtime_status_cache(),
                invalidate_goa=lambda: self._get_client_status_presenter().invalidate_goa_status_cache(),
                restore_vanilla=self.restore_vanilla_save_files,
                disconnect_after_restore=self.disconnect_from_ap_server_for_vanilla_restore,
                backup_vanilla=self.backup_vanilla_saves_before_ap_install,
                ensure_goa=self.ensure_goa_mod_installed,
                configure_probe=self.configure_probe_for_current_bridge,
            ),
        )

    def install_kh3_runtime_files_in_subprocess(self) -> None:
        self.install_kh3_runtime_files()

    def export_debug_bundle(self, *, include_crash_dumps: bool = False) -> None:
        self._export_debug_bundle(include_crash_dumps=include_crash_dumps)

    def capture_crash_dump(self) -> None:
        self._export_debug_bundle(capture_passive_dump=True)

    def _export_debug_bundle(self, *, include_crash_dumps: bool = False,
                             capture_passive_dump: bool = False) -> None:
        result = self._client_api.export_kh3_debug_bundle(
            kh3_dir=self.kh3_dir,
            bridge_dir=self.bridge_dir,
            kh3_mods_dir=self.kh3_mods_dir,
            save_dir=self.bridge_save_dir,
            ue4ss_log=self.bridge_ue4ss_log,
            include_crash_dumps=include_crash_dumps,
            capture_passive_dump=capture_passive_dump,
        )
        bundle_path = self._client_api.Path(str(result.get("bundle_path") or ""))
        self._client_api.logger.info("KH3 debug bundle ready: %s", bundle_path)
        if result.get("crash_dump_count"):
            self._client_api.logger.info("This bundle contains unredacted crash dumps. Share it privately with trusted maintainers.")
        if bundle_path.parent.is_dir():
            try:
                self._client_api.Utils.open_file(bundle_path.parent)
            except Exception as exc:
                self._client_api.logger.info("Could not open KH3 debug bundle directory: %s", exc)

    def _create_client_command_executor(self) -> ClientCommandExecutor:
        return self._client_api.ClientCommandExecutor(
            self._client_api.CommandExecutionCallbacks(
                bridge=lambda: self.bridge_dir,
                root=lambda: self.kh3_dir,
                mods=lambda: self.kh3_mods_dir,
                saves=lambda: self.bridge_save_dir,
                probe_directory=lambda: self.probe_config_path.parent,
                write_bridge=self.write_bridge_files,
                delete_sidecar=self._delete_bridge_file,
                install_runtime=self._install_kh3_runtime_files,
                invalidate_goa=lambda: self._get_client_status_presenter().invalidate_goa_status_cache(),
                export_debug=self._export_debug_bundle,
                send=self.send_msgs,
                acknowledge=lambda sequence: setattr(self, "last_processed_client_command_seq", sequence),
            )
        )
