"""Game and save paths, Steam Cloud checks, and save-management delegation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import seed_policy_service as seed_policy_support, steam_cloud_client as steam_cloud_support
from ..client_paths import ClientPaths
from ..save_service import SaveManagementService


class ClientPathsMixin:
    """Game and save paths, Steam Cloud checks, and save-management delegation."""

    def _apply_initial_paths(self, paths: ClientPaths) -> None:
        self.kh3_dir = paths.kh3_dir
        self.bridge_save_dir = paths.save_dir
        self.bridge_ue4ss_log = paths.ue4ss_log
        self.probe_config_path = paths.probe_config
        self.kh3_mods_dir = paths.mods_dir
        self.save_dir_override = paths.save_dir_override
        self.save_dir_dynamic_root_override = paths.dynamic_save_root_override
        self.ue4ss_log_override = paths.ue4ss_log_override
        self.probe_config_override = paths.probe_config_override
        self.kh3_mods_dir_override = paths.mods_dir_override

    def _get_steam_cloud_service(self) -> steam_cloud_support.SteamCloudClientService:
        service = getattr(self, "_steam_cloud_service", None)
        if service is None:
            service = self._client_api.steam_cloud_support.SteamCloudClientService(
                self._client_api.steam_cloud_support.SteamCloudCallbacks(
                    detect=lambda root: self._client_api.detect_kh3_steam_cloud_state(root),
                    disable=lambda root: self._client_api.disable_kh3_steam_cloud(root),
                    load_settings=lambda: self._client_api.Utils.persistent_load().get("kh3_client", {}),
                    store_acknowledgement=lambda value: self._client_api.Utils.persistent_store(
                        "kh3_client",
                        self._client_api.KH3_STEAM_CLOUD_ACKNOWLEDGED_KEY,
                        value,
                    ),
                    print_json=lambda payload: self.on_print_json(payload),
                    warn=lambda: self.warn_about_steam_cloud_if_needed(),
                )
            )
            self._steam_cloud_service = service
        return service

    @property
    def steam_cloud_disabled_acknowledged(self) -> bool:
        return self._get_steam_cloud_service().acknowledged

    @steam_cloud_disabled_acknowledged.setter
    def steam_cloud_disabled_acknowledged(self, value: bool) -> None:
        self._get_steam_cloud_service().acknowledged = value

    def warn_about_steam_cloud_if_needed(self) -> bool:
        return self._get_steam_cloud_service().warn(self.kh3_dir)

    def disable_steam_cloud_during_patch_game(self) -> bool:
        return self._get_steam_cloud_service().disable_for_patch(self.kh3_dir)

    async def confirm_steam_cloud_disabled_before_connect(self) -> bool:
        prompt = getattr(getattr(self, "ui", None), "prompt_steam_cloud_disabled_confirmation", None)
        return await self._get_steam_cloud_service().confirm(getattr(self, "kh3_dir", None), prompt)

    def set_kh3_dir(self, kh3_dir: str | Path, *, persist: bool = False) -> None:
        paths = self._client_api.resolve_game_root_paths(
            kh3_dir,
            ue4ss_log=getattr(self, "bridge_ue4ss_log", None),
            probe_config=getattr(self, "probe_config_path", None),
            mods_dir=getattr(self, "kh3_mods_dir", None),
            ue4ss_log_override=self.ue4ss_log_override,
            probe_config_override=self.probe_config_override,
            mods_dir_override=self.kh3_mods_dir_override,
        )
        new_root = paths.root
        if new_root != getattr(self, "kh3_dir", None):
            self.kh3_active_connection_profile_run_id = None
            self.kh3_run_connection_slot_name = ""
            presenter = self.__dict__.get("client_status_presenter")
            if presenter is not None:
                presenter.invalidate_runtime_status_cache()
                presenter.invalidate_goa_status_cache()
        self.kh3_dir = new_root
        self.bridge_ue4ss_log = paths.ue4ss_log
        self.probe_config_path = paths.probe_config
        self.kh3_mods_dir = paths.mods_dir
        save_dir_decision = self._get_seed_policy_service().select_save_directory_for_game_root(
            new_root,
            current_save_dir=self.bridge_save_dir,
            save_dir_override=self.save_dir_override,
            dynamic_root_override=getattr(self, "save_dir_dynamic_root_override", False),
        )
        if save_dir_decision is not None:
            self._set_save_directory_state(save_dir_decision)
        if persist:
            self._client_api.Utils.persistent_store("kh3_client", "kh3_dir", str(new_root))
            if save_dir_decision is not None:
                self._client_api.Utils.persistent_store("kh3_client", "save_dir", save_dir_decision.persisted_value)
        self._client_api.logger.info("KH3 directory set to: %s", new_root)
        if save_dir_decision is not None:
            self._client_api.logger.info(save_dir_decision.log_message)
        self.warn_about_steam_cloud_if_needed()
        if self.bridge_dir is None:
            self.try_initialize_offline_bridge_from_installed_pak()

    def set_kh3_save_dir(self, save_dir: str | Path, *, persist: bool = False) -> None:
        self._apply_save_directory_decision(
            self._get_seed_policy_service().select_save_directory(save_dir), persist=persist
        )

    def clear_kh3_save_dir_override(self, *, persist: bool = False) -> None:
        self._apply_save_directory_decision(
            self._get_seed_policy_service().clear_save_directory(self.kh3_dir), persist=persist
        )

    def _set_save_directory_state(self, decision: seed_policy_support.SaveDirectoryDecision) -> None:
        self.bridge_save_dir = decision.save_dir
        self.save_dir_override = decision.override
        self.save_dir_dynamic_root_override = decision.dynamic_root_override

    def _apply_save_directory_decision(
        self, decision: seed_policy_support.SaveDirectoryDecision, *, persist: bool
    ) -> None:
        self._set_save_directory_state(decision)
        if persist:
            self._client_api.Utils.persistent_store("kh3_client", "save_dir", decision.persisted_value)
        self._client_api.logger.info(decision.log_message)
        self._schedule_integrated_bridge_restart_for_save_directory_change()

    def _schedule_integrated_bridge_restart_for_save_directory_change(self) -> None:
        self._get_lifecycle_service().request_save_directory_restart(
            self._client_api.SaveDirectoryRestartCallbacks(
                enabled=lambda: self.integrated_bridge_enabled,
                bridge=lambda: self.bridge_dir,
                game_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
                wait_shutdown=self.wait_for_integrated_bridge_shutdown,
                start_bridge=self.start_integrated_bridge,
                save_dir=lambda: self.bridge_save_dir,
                schedule=lambda coro: self._client_api.Utils.async_start(coro, name="KH3SaveDirectoryBridgeRestart"),
            )
        )

    def display_kh3_save_dir(self) -> Path:
        return self._get_save_management_service().display_kh3_save_dir()

    def _get_save_management_service(self) -> SaveManagementService:
        service = getattr(self, "save_management_service", None)
        if not isinstance(service, self._client_api.SaveManagementService):
            service = self._client_api.SaveManagementService(
                self,
                process_running=lambda: self._client_api.is_process_running(self._client_api.KH3_PROCESS_NAME),
            )
            self.save_management_service = service
        return service

    def backup_vanilla_save_files(self) -> dict[str, Any]:
        return self._get_save_management_service().backup_vanilla_save_files()

    def restore_vanilla_save_files(self) -> dict[str, Any]:
        return self._get_save_management_service().restore_vanilla_save_files()

    def should_clean_steam_sibling_save_dirs(self) -> bool:
        return self._get_save_management_service().should_clean_steam_sibling_save_dirs()

    def save_cleanup_targets(self, *, include_steam_siblings: bool = False) -> list[Path]:
        return self._get_save_management_service().save_cleanup_targets(
            include_steam_siblings=include_steam_siblings,
        )

    def uninstall_kh3_ap_files(self) -> None:
        self._apply_runtime_artifact_plan(self._get_runtime_artifact_service().plan_runtime_uninstall())

    def disconnect_from_ap_server_for_vanilla_restore(self) -> None:
        if not self._is_ap_connected():
            return
        self._client_api.logger.info("Disconnecting from Archipelago after restoring vanilla KH3.")
        try:
            self._client_api.asyncio.get_running_loop()
        except RuntimeError:
            self._client_api.logger.info(
                "Could not schedule AP disconnect after restoring vanilla KH3 because no asyncio loop is running."
            )
            return
        self._client_api.Utils.async_start(self.disconnect(), name="KH3RestoreVanillaDisconnect")
