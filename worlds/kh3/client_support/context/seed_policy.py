"""Offline startup, installed-seed policy, and save ownership guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..offline_bridge_service import OfflineBridgeInitializationInput, OfflineBridgeInitializationResult
from ..offline_startup import OfflineStartupService
from ..seed_guard import SeedGuardService
from ..seed_policy_service import SeedPolicyService


class ClientSeedPolicyMixin:
    """Offline startup, installed-seed policy, and save ownership guards."""

    def _create_seed_policy_service(self) -> SeedPolicyService:
        return self._client_api.SeedPolicyService(
            seed_guard=self._get_seed_guard_service(),
            save_management=self._get_save_management_service(),
            mods_dir=lambda: self.kh3_mods_dir,
            installed_paks=self.installed_kh3_ap_seed_paks,
            process_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            load_persistent_kh3=lambda: self._client_api.Utils.persistent_load().get("kh3_client", {}),
            resolve_save_dir_input=lambda value: self._client_api.resolve_kh3_save_dir_input(value),
            resolve_save_dir_for_root=lambda root: self._client_api.resolve_kh3_save_dir_for_root(root),
            is_dynamic_save_root=lambda value: self._client_api.is_kh3_save_dir_dynamic_root(value),
            is_auto_save_dir=lambda value: self._client_api.is_kh3_auto_save_dir(value),
            log_info=self._client_api.logger.info,
            log_warning=self._client_api.logger.warning,
        )

    def _get_seed_policy_service(self) -> SeedPolicyService:
        service = self.__dict__.get("seed_policy_service")
        if not isinstance(service, self._client_api.SeedPolicyService):
            service = self._create_seed_policy_service()
            self.seed_policy_service = service
        return service

    def set_ignore_unverified_save_seed_warnings(self, enabled: bool, *, persist: bool = False) -> None:
        self.ignore_unverified_save_seed_warnings = bool(enabled)
        if self.ignore_unverified_save_seed_warnings:
            self.save_seed_mismatch_active = False
            self.last_save_seed_status_warning = ""
        if persist:
            self._client_api.Utils.persistent_store(
                "kh3_client",
                "ignore_unverified_save_seed_warnings",
                self.ignore_unverified_save_seed_warnings,
            )
        self._client_api.logger.info(
            "KH3 unverified save-folder warnings are %s.",
            "ignored" if self.ignore_unverified_save_seed_warnings else "enforced",
        )

    def _offline_bridge_initialization_input(self) -> OfflineBridgeInitializationInput:
        bridge_root = self.kh3_bridge_root()
        return self._client_api.OfflineBridgeInitializationInput(
            current_bridge_dir=self.bridge_dir,
            bridge_root=bridge_root,
            mods_dir=self.kh3_mods_dir,
            expected_seed=self.expected_seed_id(),
            auth=self.auth,
            server_address=self.server_address,
            team=self.team,
            bridge_dir_name=self._active_kh3_bridge_dir_name(),
        )

    def _active_kh3_bridge_dir_name(self) -> str:
        run_id = str(getattr(self, "kh3_active_connection_profile_run_id", "") or "").strip()
        if not run_id:
            return ""
        try:
            run = self._get_run_manager_service().get_run(run_id)
            connected_seed = self._client_api.SeedGuardService.extract_seed_id(getattr(self, "seed_name", None))
            if connected_seed and connected_seed != run.seed_id:
                return ""
            if getattr(self, "slot", None) is not None and self.slot != run.player:
                return ""
            return run.bridge_dir_name
        except Exception:
            return ""

    def _apply_offline_bridge_initialization(self, result: OfflineBridgeInitializationResult | None) -> bool:
        if result is None:
            return False
        # Keep offline pak metadata separate from CommonContext.seed_name.
        # The base client treats seed_name as authoritative and refuses RoomInfo
        # from a different server before KH3 slot_data can be refreshed.
        self.offline_seed_name = result.offline_seed_name
        self.auth = result.auth
        self.slot_data = self._client_api.freeze_slot_location_catalog(result.slot_data)
        self._kh3_bridge_seed_scope = self._client_api.ItemSyncService.seed_scope(self.slot_data).get("kh3_seed")
        self._reconcile_olympus_coliseum_reward_companion()
        self.bridge_dir = result.bridge_dir
        self._get_client_transport_service().clear_payload_cache()
        self._client_api.configure_python_profiler(self.bridge_dir, log_func=self._client_api.logger.info)
        self.location_key_to_id = result.location_key_to_id
        self.location_id_to_bridge_key = result.location_id_to_bridge_key
        self.rebuild_location_display_indexes()
        preserve_bridge_observations = self.offline_bridge_matches_installed_metadata(result.metadata)
        self.write_offline_bridge_files(
            result.metadata,
            result.pak_path,
            preserve_previous_status=preserve_bridge_observations,
        )
        if preserve_bridge_observations:
            self.ensure_bridge_observation_state(reason="offline_bridge_initialized")
            self.mark_existing_bridge_notifications_processed()
        else:
            self.reset_bridge_observation_state(reason="offline_bridge_initialized")
        self.mark_existing_runtime_game_over_processed()
        self.configure_probe_for_current_bridge()
        # Installed pak metadata can belong to a new run while the active
        # save-sidecar directory still belongs to the previous run. Blocking
        # network checks alone is too late: the offline worker would import
        # those observations into the new seed's durable bridge first.
        if self.refresh_save_seed_guard():
            self.start_integrated_bridge()
        else:
            self.kh3_check_reporting_enabled = False
            self._client_api.logger.info("Deferred KH3 offline observations until the installed run's saves are activated.")
        self._client_api.logger.info("Initialized KH3 offline bridge from installed pak metadata: %s", result.pak_path)
        return True

    def try_initialize_offline_bridge_from_installed_pak(self) -> bool:
        result = self._get_offline_bridge_service().initialize(self._offline_bridge_initialization_input())
        return self._apply_offline_bridge_initialization(result)

    def _create_offline_startup_service(self) -> OfflineStartupService:
        return self._client_api.OfflineStartupService(
            self._client_api.OfflineStartupCallbacks(
                metadata_reader=lambda: self._get_offline_bridge_service().initialize,
                profile_reader=lambda: self._get_run_manager_service().active_connection_profile,
                still_current=lambda source: (
                    self.bridge_dir == source.current_bridge_dir
                    and self.seed_name is None
                    and getattr(self, "server", None) is None
                ),
                installed_paths=lambda: frozenset(
                    self._client_api.Path(str(pak.get("path") or "")).expanduser().resolve()
                    for pak in self.installed_kh3_ap_seed_paks()
                    if str(pak.get("path") or "").strip()
                ),
                enroll=lambda result: self._auto_enroll_connected_kh3_run_action(
                    result.pak_path, result.offline_seed_name, None
                ),
                apply_bridge=self._apply_offline_bridge_initialization,
                apply_profile=self._apply_active_kh3_run_connection_profile,
            )
        )

    async def initialize_offline_bridge_after_startup(self) -> bool:
        """Load installed-pak metadata off-thread after the first GUI task can draw."""
        source = self._offline_bridge_initialization_input()
        return await self._create_offline_startup_service().initialize(source)

    def infer_slot_name_from_installed_pak(self) -> str | None:
        return self._get_offline_bridge_service().infer_slot_name(
            self._client_api.InstalledPakDiscoveryInput(
                mods_dir=self.kh3_mods_dir,
                expected_seed=self.expected_seed_id(),
                bridge_dir=self.bridge_dir,
            )
        )

    def expected_seed_id(self) -> str | None:
        return self._get_seed_guard_service().expected_seed_id()

    def _get_seed_guard_service(self) -> SeedGuardService:
        service = getattr(self, "seed_guard_service", None)
        if not isinstance(service, self._client_api.SeedGuardService):
            service = self._client_api.SeedGuardService(self)
            self.seed_guard_service = service
        return service

    def save_seed_association_for_dir(
        self,
        save_dir: Path | str,
        persistent_kh3: dict[str, Any] | None = None,
    ) -> tuple[str | None, int]:
        return self._get_seed_guard_service().save_seed_association_for_dir(save_dir, persistent_kh3)

    def save_run_association_for_dir(
        self,
        save_dir: Path | str,
        persistent_kh3: dict[str, Any] | None = None,
    ) -> str | None:
        return self._get_seed_guard_service().save_run_association_for_dir(save_dir, persistent_kh3)

    def store_save_seed_associations_for_targets(
        self,
        targets: list[Path],
        seed_id: str,
        run_id: str | None = None,
    ) -> None:
        self._get_seed_guard_service().store_save_seed_associations_for_targets(
            targets,
            seed_id,
            run_id=run_id,
        )

    def clear_save_seed_associations_for_targets(
        self,
        targets: list[Path],
        *,
        expected_run_id: str | None = None,
    ) -> None:
        self._get_seed_guard_service().clear_save_seed_associations_for_targets(
            targets,
            expected_run_id=expected_run_id,
        )

    def installed_kh3_ap_seed_paks(self) -> list[dict[str, Any]]:
        return self._get_offline_bridge_service().discover_installed_paks(
            self._client_api.InstalledPakDiscoveryInput(
                mods_dir=self.kh3_mods_dir,
                expected_seed=None,
                bridge_dir=getattr(self, "bridge_dir", None),
            )
        )

    def has_installed_kh3_ap_seed_pak(self) -> bool:
        return bool(self.installed_kh3_ap_seed_paks())

    def build_missing_installed_seed_pak_message(self) -> str:
        return self._get_seed_policy_service().build_missing_installed_seed_pak_message()

    def build_installed_seed_pak_guard_message(self) -> str:
        return self._get_seed_policy_service().build_installed_seed_pak_guard_message(
            self.installed_kh3_ap_seed_paks(),
        )

    def guard_installed_seed_pak_before_connect(self) -> bool:
        result = self._get_seed_policy_service().guard_installed_pak_before_connect(
            self.installed_kh3_ap_seed_paks(),
        )
        if result.warning:
            self.last_pak_status_warning = result.warning
            if getattr(self, "ui", None):
                self.on_print_json(
                    {
                        "cmd": "PrintJSON",
                        "data": [
                            {
                                "text": result.warning,
                                "type": "color",
                                "color": "yellow",
                            }
                        ],
                    }
                )
        return result.allowed

    def build_installed_pak_status(self) -> str:
        return self._get_seed_policy_service().installed_pak_decision().status

    def is_installed_pak_compatible(self) -> bool:
        return self._get_seed_policy_service().installed_pak_decision().compatible

    def warn_if_installed_pak_mismatch(self) -> None:
        self.last_pak_status_warning = self._get_seed_policy_service().warn_if_installed_pak_mismatch(
            self.last_pak_status_warning,
        )

    def build_save_seed_status(self) -> str:
        decision = self._get_seed_policy_service().build_save_seed_decision(
            restart_required_seed=getattr(self, "kh3_restart_required_seed_id", None),
            offline_seed_name=getattr(self, "offline_seed_name", None),
        )
        self.kh3_restart_required_seed_id = decision.restart_required_seed
        if (
            getattr(self, "ignore_unverified_save_seed_warnings", False)
            and decision.mismatch
            and decision.status.startswith("Unverified:")
        ):
            return f"Ignored by setting: {decision.status}"
        return decision.status

    def refresh_save_seed_guard(self) -> bool:
        result = self._get_seed_policy_service().refresh_save_seed_guard(
            restart_required_seed=getattr(self, "kh3_restart_required_seed_id", None),
            offline_seed_name=getattr(self, "offline_seed_name", None),
            connected_seed_name=self.seed_name,
            ignore_unverified_warning=getattr(self, "ignore_unverified_save_seed_warnings", False),
            last_warning=getattr(self, "last_save_seed_status_warning", ""),
        )
        self.save_seed_mismatch_active = result.mismatch
        self.last_save_seed_status_warning = result.warning
        self.kh3_restart_required_seed_id = result.restart_required_seed
        self.offline_seed_name = result.offline_seed_name
        return result.allowed

    def disconnect_for_seed_guard(self, reason: str, status: str) -> None:
        def clear_locations() -> None:
            self.locations_checked.clear()
            self.locations_scouted.clear()

        def reset_observations(reset_reason: str) -> None:
            self.reset_bridge_observation_state(reason=reset_reason)

        def schedule_disconnect() -> bool:
            try:
                self._client_api.asyncio.create_task(self.disconnect(), name="KH3SeedGuardDisconnect")
            except RuntimeError:
                return False
            return True

        result = self._get_seed_policy_service().disconnect_for_seed_guard(
            reason,
            status,
            restart_required_seed=getattr(self, "kh3_restart_required_seed_id", None),
            set_restart_required_seed=lambda seed: setattr(self, "kh3_restart_required_seed_id", seed),
            stop_bridge=self.stop_integrated_bridge,
            clear_locations=clear_locations,
            reset_observations=reset_observations,
            write_bridge_files=self.write_bridge_files,
            schedule_disconnect=schedule_disconnect,
        )
        self.kh3_restart_required_seed_id = result.restart_required_seed

        if reason == "save_seed_mismatch":
            recovery = getattr(getattr(self, "ui", None), "show_seed_guard_recovery", None)
            if callable(recovery):
                recovery(status=status, seed_id=self.expected_seed_id(), slot_name=self.auth)
