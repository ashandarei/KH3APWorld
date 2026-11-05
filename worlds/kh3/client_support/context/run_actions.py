"""Seed preparation and run activation/deletion orchestration."""

from __future__ import annotations

from pathlib import Path

from ..run_actions import ActiveRunSaves, RunActionService, RunSelection
from ..run_manager import KH3RunActivationResult, KH3RunConnectionProfile, KH3RunInfo
from ..save_file_policy import checked_root
from ..seed_import_service import KH3SeedPreparationResult
from ..seed_preparation import SeedPreparationService


class ClientRunActionsMixin:
    """Seed preparation and run activation/deletion orchestration."""

    def request_auto_enroll_connected_kh3_run(self) -> None:
        selected = self.connected_kh3_seed_pak_path()
        if selected is None:
            return
        pak_path, expected_seed = selected
        server_address = self.normalized_kh3_run_server_address(self.server_address)
        room_url = self.kh3_room_page_url()
        connection_slot_name = str(getattr(self, "auth", None) or "").strip()
        self._client_api.Utils.async_start(
            self._auto_enroll_connected_kh3_run_action(
                pak_path,
                expected_seed,
                server_address,
                room_url,
                connection_slot_name,
            ),
            name="KH3RunAutoEnrollment",
        )

    def _begin_kh3_run_action(self, label: str) -> bool:
        if bool(getattr(self, "kh3_run_action_running", False)):
            self._client_api.logger.info("KH3 run action is already running: %s", getattr(self, "kh3_run_action_label", ""))
            return False
        self.kh3_run_action_running = True
        self._set_kh3_run_action_status(label)
        return True

    def _set_kh3_run_action_status(self, label: str) -> None:
        if not bool(getattr(self, "kh3_run_action_running", False)):
            return
        self.kh3_run_action_label = label
        self._client_api.logger.info("KH3 seed/run manager: %s", label)

    def _finish_kh3_run_action(self) -> None:
        self.kh3_run_action_running = False
        self.kh3_run_action_label = ""

    def _create_seed_preparation_service(self) -> SeedPreparationService:
        return self._client_api.SeedPreparationService(
            importer=self._get_seed_import_service,
            manager=self._get_run_manager_service,
            callbacks=self._client_api.SeedPreparationCallbacks(
                begin=self._begin_kh3_run_action,
                finish=self._finish_kh3_run_action,
                set_status=self._set_kh3_run_action_status,
                has_installed_pak=self.has_installed_kh3_ap_seed_pak,
                preferred_slot=lambda: getattr(self, "auth", None),
                import_source=self.import_kh3_run_source,
                apply_prepared=self._apply_prepared_seed_pak,
                ensure_companion=self._ensure_olympus_coliseum_reward_companion,
                cleanup_old_runs=self._auto_delete_old_kh3_runs_after_start,
                set_warning=lambda message: setattr(self, "last_pak_status_warning", message),
            ),
        )

    async def _import_kh3_run_pak_action(
        self,
        pak_path: Path | str,
        *,
        slot_name: str | None = None,
        server: str | None = None,
        port: str | None = None,
        room_url: str | None = None,
    ) -> None:
        await self._create_seed_preparation_service().import_source(
            pak_path,
            slot_name=slot_name,
            server=server,
            port=port,
            room_url=room_url,
        )

    def _apply_prepared_seed_pak(self, result: KH3SeedPreparationResult) -> bool:
        run = result.installed_run
        if run is None:
            return True
        self._commit_prepared_seed_save_ownership(result)
        connection_slot_name = self._connection_slot_name_for_run(run)
        self._set_active_kh3_run_profile(run)
        self.seed_name = None
        self.offline_seed_name = f"AP_{run.seed_id}"
        self.auth = connection_slot_name
        try:
            initialized = self.try_initialize_offline_bridge_from_installed_pak()
        except Exception as exc:
            initialized = False
            self._client_api.logger.warning("Generated KH3 seed pak was installed, but offline bridge setup failed: %s", exc)
        self.auth = connection_slot_name
        if not initialized:
            self.offline_seed_name = f"AP_{run.seed_id}"
        self._client_api.logger.info(
            "Generated, managed, and installed KH3 run %s (seed %s, player %s) from %s.",
            run.slot_name,
            run.seed_id,
            run.player,
            result.source_path,
        )
        if result.archived_source_path is not None:
            self._client_api.logger.info("Moved processed KH3 seed source out of ~mods to %s.", result.archived_source_path)
        for warning in result.warnings:
            self._client_api.logger.warning("KH3 seed preparation warning: %s", warning)
        return initialized

    def _clear_kh3_save_seed_guard_state(self) -> None:
        self.save_seed_mismatch_active = False
        self.last_save_seed_status_warning = ""
        self.kh3_restart_required_seed_id = None

    def _commit_prepared_seed_save_ownership(self, result: KH3SeedPreparationResult) -> None:
        """Commit the save-profile handoff performed by an automatic run activation."""

        run = result.installed_run
        save_dir = result.activated_save_dir
        if run is None or save_dir is None:
            return
        self.store_save_seed_associations_for_targets(
            [save_dir],
            run.seed_id,
            run_id=run.run_id,
        )
        self._clear_kh3_save_seed_guard_state()
        self._client_api.logger.info(
            "Associated the active KH3 save profile %s with prepared run %s (seed %s).",
            save_dir,
            run.slot_name,
            run.seed_id,
        )

    async def _prepare_missing_seed_pak(self, *, trigger: str) -> KH3SeedPreparationResult | None:
        return await self._create_seed_preparation_service().prepare_missing(trigger=trigger)

    async def _auto_enroll_connected_kh3_run_action(
        self,
        pak_path: Path,
        expected_seed: str,
        server_address: str | None,
        room_url: str | None = None,
        connection_slot_name: str | None = None,
    ) -> None:
        await self._create_seed_preparation_service().enroll(
            pak_path,
            expected_seed,
            server_address,
            room_url,
            connection_slot_name,
        )

    def _create_run_action_service(self) -> RunActionService:
        return self._client_api.RunActionService(
            run_manager=self._get_run_manager_service,
            game_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            client=self._client_api.RunActionCallbacks(
                set_status=self._set_kh3_run_action_status,
                active_saves=self._active_kh3_run_saves,
                stop_bridge=self._stop_kh3_run_bridge,
                restore_bridge=self._restore_kh3_run_bridge,
                select_run=self._select_activated_kh3_run,
                initialize_run=self._initialize_activated_kh3_run,
                retain_activated_run=self._retain_activated_run_identity_after_error,
                cleanup_old_runs=self._auto_delete_old_kh3_runs_after_start,
                show_switch_warning=self._show_kh3_run_switch_warning,
                is_connected=self._is_ap_connected,
                delete_run=self.delete_kh3_run,
                clear_deleted_run=self._clear_deleted_kh3_run,
            ),
        )

    def _kh3_run_selection(self) -> RunSelection:
        return self._client_api.RunSelection(self.bridge_dir, getattr(self, "kh3_active_connection_profile_run_id", None))

    def _active_kh3_run_saves(self) -> ActiveRunSaves:
        active_save_dir, _source = self._get_client_status_presenter().active_runtime_save_directory()
        checked_root(self.bridge_save_dir)
        save_dir = checked_root(active_save_dir or self.bridge_save_dir)
        seed_id, _guard_version = self.save_seed_association_for_dir(save_dir)
        return self._client_api.ActiveRunSaves(save_dir, seed_id, self.save_run_association_for_dir(save_dir))

    async def _stop_kh3_run_bridge(self, *, disconnect: bool) -> None:
        if disconnect:
            await self.disconnect()
        else:
            self.stop_integrated_bridge()
        await self.wait_for_integrated_bridge_shutdown()

    def _restore_kh3_run_bridge(self, bridge_dir: Path) -> None:
        self.bridge_dir = bridge_dir
        self.start_integrated_bridge()

    def _show_kh3_run_switch_warning(self) -> None:
        warning = getattr(getattr(self, "ui", None), "show_run_switch_warning", None)
        if callable(warning):
            warning()

    def _select_activated_kh3_run(self, result: KH3RunActivationResult, save_dir: Path) -> None:
        self._apply_kh3_run_activation(result, save_dir)
        self._keep_kh3_run_selection_disconnected()

    def _initialize_activated_kh3_run(self, result: KH3RunActivationResult) -> bool:
        initialized = self.try_initialize_offline_bridge_from_installed_pak()
        self.auth = self._connection_slot_name_for_run(result.run)
        if not initialized:
            self.offline_seed_name = f"AP_{result.run.seed_id}"
            self._client_api.logger.warning(
                "Activated KH3 run %s, but its pak metadata could not initialize the offline bridge. "
                "Connecting to the matching AP room can initialize it later.",
                result.run.slot_name,
            )
        return initialized

    def _clear_deleted_kh3_run(self, run_id: str, save_dir: Path | None) -> None:
        if save_dir is not None:
            try:
                self.clear_save_seed_associations_for_targets([save_dir], expected_run_id=run_id)
            except Exception as exc:
                self._client_api.logger.warning("The run was deleted, but its saved profile association could not be cleared: %s", exc)
        self._apply_deleted_active_kh3_run()

    async def _activate_kh3_run_action(self, run_id: str) -> None:
        if not self._begin_kh3_run_action("Switching KH3 run..."):
            return
        try:
            await self._create_run_action_service().activate(run_id, self._kh3_run_selection())
        finally:
            self._finish_kh3_run_action()

    def _clear_kh3_run_location_state(self) -> None:
        self.location_key_to_id = {}
        self.location_id_to_bridge_key = {}
        self.location_id_to_display_name = {}
        self.location_name_to_display_name = {}
        self.locations_checked.clear()
        self.locations_scouted.clear()
        self.checked_locations.clear()
        self.missing_locations.clear()
        self.items_received = []
        self.kh3_check_reporting_enabled = False
        self.goal_status_sent = False
        self.save_seed_mismatch_active = False
        self.last_save_seed_status_warning = ""
        self.last_pak_status_warning = ""
        self.kh3_restart_required_seed_id = None
        self._get_client_transport_service().clear_payload_cache()

    def _apply_kh3_run_activation(self, result: KH3RunActivationResult, save_dir: Path) -> None:
        self.reset_server_state()
        self._set_active_kh3_run_profile(result.run)
        self._apply_kh3_run_server_address(result.run)
        self.bridge_dir = None
        self.offline_seed_name = None
        self.seed_name = None
        self.auth = self._connection_slot_name_for_run(result.run)
        self.slot_data = {}
        self._clear_kh3_run_location_state()
        self.store_save_seed_associations_for_targets(
            [save_dir],
            result.run.seed_id,
            run_id=result.run.run_id,
        )

    def _apply_kh3_run_server_address(self, run: KH3RunInfo | KH3RunConnectionProfile) -> None:
        remembered_address = str(run.server_address or "").strip()
        self.server_address = remembered_address or None
        self.username = None
        self.password = None
        ui = getattr(self, "ui", None)
        if ui is not None:
            ui.update_address_bar(remembered_address)
        if remembered_address:
            self._client_api.logger.info("Selected remembered AP server for %s: %s", run.slot_name, remembered_address)

    def _keep_kh3_run_selection_disconnected(self) -> None:
        """Prevent a run activation from inheriting or scheduling a server reconnect."""

        self.disconnected_intentionally = True
        if self.cancel_autoreconnect():
            self._client_api.logger.info("Cancelled AP auto-reconnect after selecting a KH3 run.")

    def _retain_activated_run_identity_after_error(self, result: KH3RunActivationResult) -> None:
        self._keep_kh3_run_selection_disconnected()
        self.bridge_dir = None
        self._set_active_kh3_run_profile(result.run)
        self.auth = self._connection_slot_name_for_run(result.run)
        self.offline_seed_name = f"AP_{result.run.seed_id}"
        self._apply_kh3_run_server_address(result.run)
        self.kh3_check_reporting_enabled = False
        self._client_api.logger.warning(
            "The KH3 files were switched successfully, but client reinitialization did not finish. "
            "Restart the KH3 client before connecting or launching the game."
        )

    async def _delete_kh3_run_action(self, run_id: str) -> None:
        await self._delete_kh3_runs_action((run_id,))

    async def _delete_kh3_runs_action(self, run_ids: tuple[str, ...]) -> None:
        run_ids = tuple(dict.fromkeys(run_ids))
        if not run_ids:
            return
        if self._is_ap_connected():
            try:
                runs = self._get_run_manager_service().list_runs(force_refresh=True)
            except Exception as exc:
                self._client_api.logger.warning("Could not read KH3 runs for deletion: %s", exc)
                return
            active = [run.run_id for run in runs if run.active]
            if len(active) != 1 or active[0] in run_ids:
                self._client_api.logger.warning("Disconnect from Archipelago before deleting the active KH3 run.")
                return
            # Explicit selections may span storefronts. Automatic cleanup and
            # Delete All Except Active retain their current-platform scope.
            await self._delete_inactive_kh3_runs_action(run_ids, active[0], current_platform_only=False)
            return
        if not self._begin_kh3_run_action("Deleting selected KH3 runs..."):
            return
        try:
            service = self._create_run_action_service()
            for index, run_id in enumerate(run_ids, 1):
                self._set_kh3_run_action_status(f"Deleting KH3 run {index}/{len(run_ids)}...")
                if not await service.delete(run_id, self._kh3_run_selection()):
                    if len(run_ids) > 1:
                        self._client_api.logger.warning("Stopped deleting selected KH3 runs after %s completed run(s).", index - 1)
                    break
        finally:
            self._finish_kh3_run_action()

    def _apply_deleted_active_kh3_run(self) -> None:
        """Clear only active AP runtime state after its local run has been permanently deleted."""

        self._keep_kh3_run_selection_disconnected()
        self.reset_server_state()
        self.bridge_dir = None
        self.offline_seed_name = None
        self.seed_name = None
        self.auth = None
        self.username = None
        self.slot_data = {}
        self.kh3_active_connection_profile_run_id = None
        self.kh3_run_connection_slot_name = ""
        self.kh3_auth_inferred_from_installed_pak = False
        self._clear_kh3_run_location_state()
