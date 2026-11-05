"""Bridge service construction, worker lifetime, and observation state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bridge_sync_service import BridgeObservationState, BridgeSyncService
from ..lifecycle_service import BridgeLifecycleService, LifecycleState
from ..offline_bridge_service import OfflineBridgeService
import asyncio
import threading


class ClientBridgeLifecycleMixin:
    """Bridge service construction, worker lifetime, and observation state."""

    def _create_lifecycle_service(self) -> BridgeLifecycleService:
        return self._client_api.BridgeLifecycleService(
            self,
            server_loop=self._client_api.server_loop,
            bridge_watcher=self._client_api.bridge_watcher,
            integrated_bridge_worker=self._run_integrated_bridge_worker,
            log_info=self._client_api.logger.info,
            log_warning=self._client_api.logger.warning,
            log_exception=self._client_api.logger.exception,
            state=self._lifecycle_state(),
        )

    def _get_lifecycle_service(self) -> BridgeLifecycleService:
        service = self.__dict__.get("lifecycle_service")
        if not isinstance(service, self._client_api.BridgeLifecycleService):
            service = self._create_lifecycle_service()
            self.lifecycle_service = service
        return service

    def _create_bridge_sync_service(self) -> BridgeSyncService:
        return self._client_api.BridgeSyncService(
            protocol_name=self._client_api.BRIDGE_PROTOCOL_NAME,
            protocol_version=self._client_api.BRIDGE_PROTOCOL_VERSION,
            load_optional_json=self._client_api.load_optional_json_file,
            poll_json=self._client_api.load_optional_json_file_once,
            validate_protocol=self._client_api.validate_protocol_payload,
            validate_slot_data=self._client_api.validate_slot_data_payload,
            protocol_error=self._client_api.BridgeProtocolError,
            atomic_write_json=self._client_api.atomic_write_json,
            safe_name=self._client_api.Utils.get_file_safe_name,
            configure_profiler=lambda bridge_dir: self._client_api.configure_python_profiler(bridge_dir, log_func=self._client_api.logger.info),
            mark_existing_notifications=self.mark_existing_bridge_notifications_processed,
            installed_pak_compatible=self.is_installed_pak_compatible,
            build_installed_pak_status=self.build_installed_pak_status,
            refresh_save_seed_guard=self.refresh_save_seed_guard,
            build_save_seed_status=self.build_save_seed_status,
            record_locations=lambda location_ids: self.locations_checked.update(location_ids),
            send_msgs=self.send_msgs,
            publish_bridge_files=self.write_bridge_files,
            profile_scope=self._client_api.PythonProfileScope,
            log_info=self._client_api.logger.info,
            log_warning=self._client_api.logger.warning,
            goal_status=self._client_api.ClientStatus.CLIENT_GOAL,
            state=self._bridge_observation_state(),
        )

    def _get_bridge_sync_service(self) -> BridgeSyncService:
        service = self.__dict__.get("bridge_sync_service")
        if not isinstance(service, self._client_api.BridgeSyncService):
            service = self._create_bridge_sync_service()
            self.bridge_sync_service = service
        return service

    def _create_offline_bridge_service(self) -> OfflineBridgeService:
        return self._client_api.OfflineBridgeService(
            protocol_name=self._client_api.BRIDGE_PROTOCOL_NAME,
            protocol_version=self._client_api.BRIDGE_PROTOCOL_VERSION,
            world_version=self._client_api.KH3_WORLD_VERSION,
            seed_pak_pattern=self._client_api.KH3_AP_SEED_PAK_PATTERN,
            list_pak_paths=lambda mods_dir: list(mods_dir.glob("*.pak")) if mods_dir.is_dir() else [],
            modified_time=lambda path: path.stat().st_mtime,
            load_installed_metadata=lambda mods_dir, expected_seed: self._client_api.load_installed_kh3_ap_seed_metadata(
                mods_dir,
                expected_seed=expected_seed,
                metadata_cache_path=self._client_api.seed_pak_metadata_cache_path(),
            ),
            infer_slot_name_from_pak=self._client_api.infer_kh3_ap_slot_name_from_pak_name,
            validate_slot_data=self._client_api.validate_slot_data_payload,
            validate_protocol=self._client_api.validate_protocol_payload,
            protocol_error=self._client_api.BridgeProtocolError,
            load_optional_json=self._client_api.load_optional_json_file,
            load_cached_items=lambda bridge_dir: self._get_item_sync_service().load_cached_payload(
                bridge_dir,
                expected_seed=getattr(self, "slot_data", {}).get("kh3_seed"),
            ),
            atomic_write_json=self._client_api.atomic_write_json,
            create_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
            delete_file=self._client_api.safe_unlink,
            safe_name=self._client_api.Utils.get_file_safe_name,
            extract_seed_id=self.extract_seed_id,
            log_info=self._client_api.logger.info,
        )

    def _get_offline_bridge_service(self) -> OfflineBridgeService:
        service = self.__dict__.get("offline_bridge_service")
        if not isinstance(service, self._client_api.OfflineBridgeService):
            service = self._create_offline_bridge_service()
            self.offline_bridge_service = service
        return service

    def _bridge_observation_state(self) -> BridgeObservationState:
        service = self.__dict__.get("bridge_sync_service")
        if isinstance(service, self._client_api.BridgeSyncService):
            self._kh3_observation_state = service.state
            return service.state
        state = self.__dict__.get("_kh3_observation_state")
        if state is None:
            state = self._kh3_observation_state = self._client_api.BridgeObservationState()
        return state

    @property
    def last_processed_game_state_seq(self) -> int:
        return self._bridge_observation_state().last_sequence

    @last_processed_game_state_seq.setter
    def last_processed_game_state_seq(self, value: int) -> None:
        self._bridge_observation_state().last_sequence = int(value)

    @property
    def last_processed_game_state_file_signature(self) -> tuple[int, int] | None:
        return self._bridge_observation_state().last_file_signature

    @last_processed_game_state_file_signature.setter
    def last_processed_game_state_file_signature(self, value: tuple[int, int] | None) -> None:
        self._bridge_observation_state().last_file_signature = value

    @property
    def goal_status_sent(self) -> bool:
        return self._bridge_observation_state().goal_reported

    @goal_status_sent.setter
    def goal_status_sent(self, value: bool) -> None:
        self._bridge_observation_state().goal_reported = bool(value)

    def _lifecycle_state(self) -> LifecycleState:
        service = self.__dict__.get("lifecycle_service")
        if isinstance(service, self._client_api.BridgeLifecycleService):
            self._kh3_lifecycle_state = service.state
            return service.state
        state = self.__dict__.get("_kh3_lifecycle_state")
        if state is None:
            state = self._kh3_lifecycle_state = self._client_api.LifecycleState()
        return state

    @property
    def integrated_bridge_task(self) -> asyncio.Task[None] | None:
        return self._lifecycle_state().integrated_bridge_task

    @integrated_bridge_task.setter
    def integrated_bridge_task(self, value: asyncio.Task[None] | None) -> None:
        self._lifecycle_state().integrated_bridge_task = value

    @property
    def integrated_bridge_stop_event(self) -> threading.Event | None:
        return self._lifecycle_state().integrated_bridge_stop_event

    @integrated_bridge_stop_event.setter
    def integrated_bridge_stop_event(self, value: threading.Event | None) -> None:
        self._lifecycle_state().integrated_bridge_stop_event = value

    @property
    def integrated_bridge_dir(self) -> Path | None:
        return self._lifecycle_state().integrated_bridge_dir

    @integrated_bridge_dir.setter
    def integrated_bridge_dir(self, value: Path | None) -> None:
        self._lifecycle_state().integrated_bridge_dir = value

    @property
    def integrated_bridge_save_dir(self) -> Path | None:
        return self._lifecycle_state().integrated_bridge_save_dir

    @integrated_bridge_save_dir.setter
    def integrated_bridge_save_dir(self, value: Path | None) -> None:
        self._lifecycle_state().integrated_bridge_save_dir = value

    def start_integrated_bridge(self) -> None:
        self._bridge_observation_paused = False
        if not self.integrated_bridge_enabled or self.bridge_dir is None:
            return
        self._get_lifecycle_service().start_integrated_bridge(
            enabled=self.integrated_bridge_enabled,
            bridge_dir=self.bridge_dir,
            save_dir=self.bridge_save_dir,
        )

    def stop_integrated_bridge(self) -> None:
        service = self.__dict__.get("lifecycle_service")
        if isinstance(service, self._client_api.BridgeLifecycleService):
            service.stop_integrated_bridge()
        elif self.integrated_bridge_stop_event is not None:
            self.integrated_bridge_stop_event.set()

    async def wait_for_integrated_bridge_shutdown(self) -> None:
        self._bridge_observation_paused = True
        sync = self.__dict__.get("bridge_sync_service")
        if sync is not None:
            sync.invalidate_pending_observation()
        service = self.__dict__.get("lifecycle_service")
        if isinstance(service, self._client_api.BridgeLifecycleService):
            await service.wait_for_integrated_bridge_shutdown()
            await self.flush_bridge_publications()
            return
        self.stop_integrated_bridge()
        task = self.integrated_bridge_task
        if task is not None:
            try:
                await task
            except self._client_api.asyncio.CancelledError:
                pass
        await self.flush_bridge_publications()

    async def run_integrated_bridge(self, bridge_dir: Path, save_dir: Path, stop_event: threading.Event) -> None:
        await self._get_lifecycle_service().run_integrated_bridge(bridge_dir, save_dir, stop_event)

    def _run_integrated_bridge_worker(self, bridge_dir: Path, save_dir: Path, stop_event: threading.Event) -> int:
        return self._client_api.run_integrated_bridge_worker(
            bridge_dir,
            save_dir,
            stop_event,
            options=self._client_api.BridgeWorkerOptions(
                self.bridge_poll_interval, self.integrated_bridge_activity_event
            ),
            notify_pipe_activity=lambda: getattr(self, "pipe_activity_notify", lambda: None)(),
        )

    def _load_bridge_payload(self, file_name: str) -> dict[str, Any] | None:
        return self._get_client_transport_service().load_protocol_payload(self.bridge_dir, file_name, read_only=True)

    def _load_bridge_sidecar_payload(self, file_name: str) -> dict[str, Any] | None:
        payload = self._get_client_transport_service().load_sidecar_payload(self.bridge_dir, file_name, read_only=True)
        if file_name in self._client_api.NATIVE_RUNTIME_SNAPSHOT_SCHEMAS:
            return self._client_api.bound_runtime_snapshot(
                payload, self._client_api.NATIVE_RUNTIME_SNAPSHOT_SCHEMAS[file_name],
                self._load_bridge_payload("bridge_runtime_status.json"), getattr(self, "slot_data", {}),
            )
        return payload

    def _delete_bridge_file(self, file_name: str) -> None:
        self._get_offline_bridge_service().delete_bridge_file(self.bridge_dir, file_name)
