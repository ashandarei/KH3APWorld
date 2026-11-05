"""Authentication, room selection, connected-seed setup, and packet adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..client_packets import ClientPacketService, ConnectedSetupService
from ..connected_run import AutoActivationRequest, ConnectedRunService
from ..connection_flow import ConnectionFlowService, ConnectionState
from ..run_manager import KH3RunInfo, KH3RunSuspensionResult
from ..save_file_policy import checked_root
from ..seed_import_service import KH3SeedPreparationResult

class ClientConnectionMixin:
    """Authentication, room selection, connected-seed setup, and packet adapters."""

    def prepare_for_new_server_connection(self) -> None:
        sync = self.__dict__.get("bridge_sync_service")
        if sync is not None:
            sync.invalidate_pending_observation()
        connection_slot_name = str(getattr(self, "kh3_run_connection_slot_name", "") or "").strip()
        result = self._get_client_transport_service().plan_connection_preparation(
            self._client_api.ConnectionPreparationInput(
                auth=connection_slot_name or self.auth,
                inferred_slot_name=(None if connection_slot_name else self.infer_slot_name_from_installed_pak()),
            )
        )
        self.seed_name = result.seed_name
        self.kh3_check_reporting_enabled = result.check_reporting_enabled
        if result.clear_locations_checked:
            self.locations_checked.clear()
        if result.clear_locations_scouted:
            self.locations_scouted.clear()
        self.goal_status_sent = result.goal_status_sent
        self.last_processed_game_state_seq = result.last_processed_game_state_seq
        self.last_processed_game_state_file_signature = result.last_processed_game_state_file_signature
        self.save_seed_mismatch_active = result.save_seed_mismatch_active
        self.last_save_seed_status_warning = result.last_save_seed_status_warning
        if result.log_inferred_slot_name:
            self._client_api.logger.info("Using KH3 slot name from installed AP pak: %s", result.inferred_slot_name)
        self.auth = result.auth
        self.kh3_auth_inferred_from_installed_pak = bool(
            not connection_slot_name
            and result.inferred_slot_name
            and result.auth
            and result.inferred_slot_name.casefold() == result.auth.casefold()
        )

    def _show_kh3_seed_bootstrap_warning(self, message: str) -> None:
        self.last_pak_status_warning = message
        self._client_api.logger.warning(message)
        if getattr(self, "ui", None):
            self.on_print_json(
                {
                    "cmd": "PrintJSON",
                    "data": [
                        {
                            "text": message,
                            "type": "color",
                            "color": "yellow",
                        }
                    ],
                }
            )

    def _connection_flow_state(self) -> ConnectionState:
        return self._client_api.ConnectionState(
            auth=getattr(self, "auth", None),
            username=getattr(self, "username", None),
            password=getattr(self, "password", None),
            managed_slot_name=str(getattr(self, "kh3_run_connection_slot_name", "") or "").strip(),
            bootstrap_pending=bool(getattr(self, "kh3_seed_bootstrap_pending", False)),
            pre_auth_blocked=bool(getattr(self, "kh3_room_pre_auth_blocked", False)),
        )

    def _reset_kh3_connection_attempt(self) -> None:
        self.kh3_seed_bootstrap_pending = False
        self.kh3_auth_inferred_from_installed_pak = False
        self.kh3_room_pre_auth_blocked = False
        self.kh3_invalid_slot_recovery_pending = False

    def _clear_kh3_slot_prompt_profile(self) -> None:
        self.kh3_active_connection_profile_run_id = None
        self.kh3_run_connection_slot_name = ""
        self.auth = None

    def _apply_kh3_auth_selection(self, auth: str | None, inferred: bool) -> None:
        self.auth = auth
        self.kh3_auth_inferred_from_installed_pak = inferred

    def _create_connection_flow_service(self) -> ConnectionFlowService:
        return self._client_api.ConnectionFlowService(
            self._client_api.ConnectionFlowCallbacks(
                state=self._connection_flow_state,
                confirm_cloud=self.confirm_steam_cloud_disabled_before_connect,
                disconnect=self.disconnect,
                reset_attempt=self._reset_kh3_connection_attempt,
                prepare_seed=lambda: self._prepare_missing_seed_pak(trigger="connecting"),
                has_installed_pak=self.has_installed_kh3_ap_seed_pak,
                game_running=lambda: self._get_seed_import_service().process_running(),
                warn=self._show_kh3_seed_bootstrap_warning,
                prompt_slot=self._prompt_kh3_seed_bootstrap_slot_name,
                set_bootstrap=lambda value: setattr(self, "kh3_seed_bootstrap_pending", value),
                guard_pak=self.guard_installed_seed_pak_before_connect,
                prepare_connection=self.prepare_for_new_server_connection,
                start_server=lambda address: self._get_lifecycle_service().start_server(
                    address, task_name="server loop"
                ),
                clear_prompt_profile=self._clear_kh3_slot_prompt_profile,
                text_prompt=lambda: getattr(getattr(self, "ui", None), "prompt_text_input", None),
                set_auth=self._apply_kh3_auth_selection,
                clear_username=lambda: setattr(self, "username", None),
                get_username=self.get_username,
                infer_slot=self.infer_slot_name_from_installed_pak,
                send_connect=lambda: self.send_connect(game=self.game),
                suspend_run=lambda reason: self._suspend_installed_kh3_run_for_server(reason=reason),
                close_socket=self._close_current_server_socket,
                block_pre_auth=lambda: setattr(self, "kh3_room_pre_auth_blocked", True),
                allow_reconnect=lambda: setattr(self, "disconnected_intentionally", False),
                clear_recovery_pending=lambda: setattr(self, "kh3_invalid_slot_recovery_pending", False),
            )
        )

    async def _prompt_kh3_seed_bootstrap_slot_name(self) -> bool:
        return await self._create_connection_flow_service().prompt_slot()

    def installed_seed_pak_mismatches_room(self, room_seed_name: Any) -> bool:
        room_seed_id = self._client_api.SeedGuardService.extract_seed_id(room_seed_name)
        installed = self.installed_kh3_ap_seed_paks()
        if room_seed_id is None or len(installed) != 1:
            return False
        installed_seed_id = self._client_api.SeedGuardService.extract_seed_id(installed[0].get("seed"))
        return installed_seed_id is not None and installed_seed_id != room_seed_id

    def installed_run_mismatches_connection_affinity(self) -> bool:
        """Detect the same pak/seed being used for a different hostname + login profile."""

        try:
            profile = self._get_run_manager_service().active_connection_profile()
        except Exception:
            return False
        if profile is None:
            return False
        current_slot = str(getattr(self, "auth", None) or profile.effective_slot_name or "").strip()
        previous_affinity = self._client_api.kh3_run_connection_affinity(
            profile.server_address,
            profile.effective_slot_name,
        )
        requested_affinity = self._client_api.kh3_run_connection_affinity(
            getattr(self, "server_address", None),
            current_slot,
        )
        return bool(previous_affinity and requested_affinity and previous_affinity != requested_affinity)

    def _create_connected_run_service(self) -> ConnectedRunService:
        return self._client_api.ConnectedRunService(
            self._get_run_manager_service,
            self._client_api.ConnectedRunCallbacks(
                begin=self._begin_kh3_run_action,
                finish=self._finish_kh3_run_action,
                warn=self._show_kh3_seed_bootstrap_warning,
                connected=lambda: getattr(self, "server", None) is not None and getattr(self, "slot", None) is not None,
                disconnect=self.disconnect,
                ensure_companion=self._ensure_olympus_coliseum_reward_companion,
                cleanup_old_runs=self._auto_delete_old_kh3_runs_after_start,
                finish_setup=self._finish_connected_kh3_setup,
            ),
        )

    def _apply_suspended_kh3_run(self, result: KH3RunSuspensionResult, save_dir: Path) -> None:
        self.bridge_dir = None
        self.offline_seed_name = None
        self.auth = None
        self.username = None
        self.slot_data = {}
        self.kh3_active_connection_profile_run_id = None
        self.kh3_run_connection_slot_name = ""
        self.kh3_auth_inferred_from_installed_pak = False
        save_run = result.save_run or result.run
        self.store_save_seed_associations_for_targets([save_dir], save_run.seed_id, run_id=save_run.run_id)

    async def _suspend_installed_kh3_run_for_server(self, *, reason: str) -> bool:
        return await self._create_connected_run_service().suspend(
            reason=reason,
            suspension=self._client_api.SuspensionCallbacks(
                bridge_dir=lambda: self.bridge_dir,
                saves=self._active_kh3_run_saves,
                game_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
                stop_bridge=self.stop_integrated_bridge,
                wait_bridge=self.wait_for_integrated_bridge_shutdown,
                restore_bridge=self._restore_kh3_run_bridge,
                apply=self._apply_suspended_kh3_run,
            ),
        )

    async def _close_current_server_socket(self) -> None:
        endpoint = getattr(self, "server", None)
        socket = getattr(endpoint, "socket", None)
        if socket is not None and not getattr(socket, "closed", False):
            await socket.close()

    async def _recover_rejected_inferred_kh3_slot(self) -> None:
        await self._create_connection_flow_service().recover_rejected_slot()

    def _reset_connected_kh3_state(self, args: dict[str, Any]) -> None:
        from ...SlotData import decode_slot_data

        runtime_slot_data = decode_slot_data(args.get("slot_data", {}))
        # Connected precedes the new server's index-zero ReceivedItems snapshot.
        self.items_received = []
        self.invalidate_bridge_item_cache()
        self.kh3_check_reporting_enabled = False
        self.kh3_received_items_snapshot_ready = False
        self._get_secret_report_hint_service().reset_for_connection()
        self.locations_checked.clear()
        self.locations_scouted.clear()
        self.goal_status_sent = False
        self._get_poptracker_sync_service().reset_emblems()
        self.kh3_connected_room_url = self._client_api.normalize_kh3_room_page_url(args.get("room_url"))
        self.slot_data = self._client_api.freeze_slot_location_catalog(
            self._client_api.kh3_slot_data_with_connected_room_url(
                runtime_slot_data,
                self.kh3_connected_room_url,
            )
        )

    def _initialize_connected_kh3_deathlink(self) -> None:
        enabled, _ = self._client_api.deathlink_settings.initialize_settings(self.bridge_dir, self.slot_data)
        self._client_api.Utils.async_start(self.update_death_link(enabled), name="KH3DeathLinkUpdate")

    def _schedule_connected_kh3_activation(self, args: dict[str, Any]) -> None:
        self.kh3_auto_activation_pending = True
        self._client_api.Utils.async_start(self._auto_activate_connected_kh3_run_action(args), name="KH3ConnectedRunActivation")

    def _create_connected_setup_service(self) -> ConnectedSetupService:
        return self._client_api.ConnectedSetupService(
            self._client_api.ConnectedSetupCallbacks(
                initialize_bridge=self.initialize_bridge,
                initialize_deathlink=self._initialize_connected_kh3_deathlink,
                record_checks=lambda locations: self.log_location_check_transport_event(
                    "location_checks_server_snapshot", locations=locations, source_cmd="Connected"
                ),
                publish=self.write_bridge_files,
                configure_probe=self.configure_probe_for_current_bridge,
                warn_pak=self.warn_if_installed_pak_mismatch,
                compatible_pak=self.is_installed_pak_compatible,
                pak_status=self.build_installed_pak_status,
                save_guard=self.refresh_save_seed_guard,
                disable_checks=lambda: setattr(self, "kh3_check_reporting_enabled", False),
                activation_pending=lambda: bool(getattr(self, "kh3_auto_activation_pending", False)),
                game_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
                schedule_activation=self._schedule_connected_kh3_activation,
                save_status=self.build_save_seed_status,
                disconnect_guard=self.disconnect_for_seed_guard,
                enroll=self.request_auto_enroll_connected_kh3_run,
                enable_checks=lambda: setattr(self, "kh3_check_reporting_enabled", True),
                start_bridge=self.start_integrated_bridge,
            )
        )

    def _finish_connected_kh3_setup(self, args: dict[str, Any]) -> None:
        self._create_connected_setup_service().complete(args)

    def _connected_auto_activation_request(self) -> AutoActivationRequest:
        selected = self.connected_kh3_seed_pak_path()
        if selected is None:
            raise self._client_api.KH3RunManagerError("The installed seed pak changed before automatic activation.")
        pak_path, expected_seed = selected
        save_dir = checked_root(self.bridge_save_dir)
        save_seed, _guard_version = self.save_seed_association_for_dir(save_dir)
        return self._client_api.AutoActivationRequest(
            pak_path,
            expected_seed,
            self.slot,
            self.normalized_kh3_run_server_address(self.server_address),
            self.auth,
            self.kh3_room_page_url(),
            self._client_api.ActiveRunSaves(save_dir, save_seed, self.save_run_association_for_dir(save_dir)),
        )

    def _apply_auto_activated_connected_run(self, run: KH3RunInfo) -> None:
        self._set_active_kh3_run_profile(run)
        self.offline_seed_name = f"AP_{run.seed_id}"
        self._clear_kh3_save_seed_guard_state()
        self.reset_bridge_observation_state(reason="connected_run_auto_activation")

    async def _auto_activate_connected_kh3_run_action(self, args: dict[str, Any]) -> None:
        server = self.server
        identity = (self.seed_name, self.slot, self.auth)
        await self._create_connected_run_service().auto_activate(
            args,
            importer=self._get_seed_import_service,
            activation=self._client_api.AutoActivationCallbacks(
                still_connected=lambda: (
                    server is not None and self.server is server and identity == (self.seed_name, self.slot, self.auth)
                ),
                disable_checks=lambda: setattr(self, "kh3_check_reporting_enabled", False),
                wait_bridge=self.wait_for_integrated_bridge_shutdown,
                request=self._connected_auto_activation_request,
                remember_save_owner=lambda run, save_dir: self.store_save_seed_associations_for_targets(
                    [save_dir],
                    run.seed_id,
                    run_id=run.run_id,
                ),
                apply=self._apply_auto_activated_connected_run,
                disconnect_guard=lambda error: self.disconnect_for_seed_guard("save_seed_mismatch", error),
                clear_pending=lambda: setattr(self, "kh3_auto_activation_pending", False),
            ),
        )

    def _connected_kh3_platform_room_fallback_url(self) -> str | None:
        """Build a public room URL for metadata accidentally stamped with a Docker-only hostname."""

        return self._client_api.normalize_kh3_room_page_url(
            getattr(self, "kh3_connected_room_url", None)
        ) or self._client_api.kh3_platform_room_fallback_url(
            self.slot_data,
            getattr(self, "server_address", None),
        )

    def kh3_room_page_url(self) -> str | None:
        """Return an exact, validated room page URL published by the connected platform."""

        return self._client_api.normalize_kh3_room_page_url(
            getattr(self, "kh3_connected_room_url", None)
        ) or self._client_api.kh3_room_page_url_from_slot_data(
            self.slot_data,
            getattr(self, "server_address", None),
        )

    def open_kh3_room_page(self, room_url: str | None = None) -> bool:
        """Open an exact managed-run or connected room page in the user's default browser."""

        room_url = self._client_api.normalize_kh3_room_page_url(room_url) if room_url else self.kh3_room_page_url()
        return self._client_api.open_kh3_room_page_url(room_url)

    def _prepare_connected_kh3_seed(self) -> KH3SeedPreparationResult:
        service = self._client_api.ConnectedSeedService(self._get_seed_import_service, self._get_platform_seed_download_service)
        return service.prepare(
            self._client_api.ConnectedSeedRequest(
                slot_data=self.slot_data,
                server_address=str(getattr(self, "server_address", None) or ""),
                slot=getattr(self, "slot", None),
                slot_name=getattr(self, "auth", None),
                seed_name=getattr(self, "seed_name", None),
            ),
            resolve_saves=self._active_kh3_run_saves,
            fallback_room=self._connected_kh3_platform_room_fallback_url,
            progress=self._set_kh3_run_action_status,
        )

    def _apply_connected_seed_preparation(self, prepared: KH3SeedPreparationResult) -> None:
        run = prepared.installed_run
        self._set_active_kh3_run_profile(run)
        self._commit_prepared_seed_save_ownership(prepared)
        self.auth = self._connection_slot_name_for_run(run)
        self.offline_seed_name = f"AP_{run.seed_id}"
        self.kh3_seed_bootstrap_pending = False

    async def _bootstrap_connected_kh3_seed_action(self, args: dict[str, Any]) -> None:
        await self._create_connected_run_service().bootstrap(
            args,
            prepare=self._prepare_connected_kh3_seed,
            apply=self._apply_connected_seed_preparation,
        )

    async def _prepare_connected_olympus_coliseum_companion_action(self, args: dict[str, Any]) -> None:
        await self._create_connected_run_service().prepare_companion(args)

    def _observe_kh3_room_seed(self, seed_name: Any) -> None:
        observation = self._get_seed_policy_service().observe_room_seed(
            seed_name, getattr(self, "kh3_restart_required_seed_id", None)
        )
        self.seed_name = observation.seed_name
        self.kh3_restart_required_seed_id = observation.restart_required_seed

    def _reset_kh3_authenticated_slot_state(self) -> None:
        self.kh3_auth_inferred_from_installed_pak = False
        self.kh3_invalid_slot_recovery_pending = False

    def _create_client_packet_service(self) -> ClientPacketService:
        return self._client_api.ClientPacketService(
            self._client_api.ClientPacketCallbacks(
                observe_room=self._observe_kh3_room_seed,
                reset_auth_recovery=self._reset_kh3_authenticated_slot_state,
                apply_connected=self._reset_connected_kh3_state,
                disable_checks=lambda: setattr(self, "kh3_check_reporting_enabled", False),
                warn=self._show_kh3_seed_bootstrap_warning,
                disconnect=self.disconnect,
                schedule=lambda coro, name: self._client_api.Utils.async_start(coro, name=name),
                bootstrap_pending=lambda: bool(getattr(self, "kh3_seed_bootstrap_pending", False)),
                bootstrap=self._bootstrap_connected_kh3_seed_action,
                companion=lambda: self._reconcile_olympus_coliseum_reward_companion(log_missing=False),
                prepare_companion=self._prepare_connected_olympus_coliseum_companion_action,
                finish_connected=self._finish_connected_kh3_setup,
                record_checks=lambda event, locations, command: self.log_location_check_transport_event(
                    event, locations=locations, source_cmd=command
                ),
                bridge=lambda: self.bridge_dir,
                publish=self.write_bridge_files,
                invalidate_items=self.invalidate_bridge_item_cache,
                mark_receipts_ready=lambda: setattr(self, "kh3_received_items_snapshot_ready", True),
                receive_items=lambda bridge: self._get_item_sync_service().handle_received_items(
                    bridge, refresh_bridge_files=self.write_bridge_files
                ),
            )
        )
