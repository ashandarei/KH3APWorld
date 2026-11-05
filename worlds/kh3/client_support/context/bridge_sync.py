"""Bridge publication, transport, observations, and tracker synchronization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bridge_publication import BridgePublicationService
from ..bridge_publisher import OrderedBridgePublisher
from ..client_command_service import ClientCommandPlan, ClientCommandService
from ..client_transport_service import ClientTransportService
from ..poptracker import PopTrackerSyncService, TrackerCallbacks, TrackerRoute
from ..runtime_artifact_service import RuntimeArtifactService
import asyncio


class ClientBridgeSyncMixin:
    """Bridge publication, transport, observations, and tracker synchronization."""

    def _get_runtime_artifact_service(self) -> RuntimeArtifactService:
        service = self.__dict__.get("runtime_artifact_service")
        if not isinstance(service, self._client_api.RuntimeArtifactService):
            service = self._client_api.RuntimeArtifactService()
            self.runtime_artifact_service = service
        return service

    def _create_client_command_service(self) -> ClientCommandService:
        return self._client_api.ClientCommandService(self._load_bridge_payload)

    def _create_client_transport_service(self) -> ClientTransportService:
        return self._client_api.ClientTransportService(
            protocol_name=self._client_api.BRIDGE_PROTOCOL_NAME,
            protocol_version=self._client_api.BRIDGE_PROTOCOL_VERSION,
            payload_reader=self._client_api.load_optional_json_view,
            validate_protocol=self._client_api.validate_protocol_payload,
            protocol_error=self._client_api.BridgeProtocolError,
        )

    def _get_client_transport_service(self) -> ClientTransportService:
        service = self.__dict__.get("client_transport_service")
        if not isinstance(service, self._client_api.ClientTransportService):
            service = self._create_client_transport_service()
            self.client_transport_service = service
        return service

    def _get_bridge_publisher(self) -> OrderedBridgePublisher:
        publisher = self.__dict__.get("bridge_publisher")
        if publisher is None:
            publisher = self._client_api.OrderedBridgePublisher(self._get_bridge_sync_service().publish, self._client_api.logger.warning)
            self.bridge_publisher = publisher
        return publisher

    async def flush_bridge_publications(self) -> None:
        publisher = self.__dict__.get("bridge_publisher")
        if publisher is not None:
            await publisher.flush()

    def invalidate_bridge_item_cache(self) -> None:
        self._get_item_sync_service().invalidate_items()
        publisher = self.__dict__.get("bridge_publisher")
        if publisher is not None:
            publisher.invalidate_slot_data()

    def log_location_check_transport_event(
        self,
        event: str,
        *,
        location_check_msgs: list[dict[str, Any]] | None = None,
        locations: Any = None,
        reason: str | None = None,
        error: str | None = None,
        source_cmd: str | None = None,
    ) -> None:
        if self.bridge_dir is None:
            return
        self._get_client_transport_service().write_location_transport_diagnostic(
            self.bridge_dir,
            self._client_api.LocationTransportDiagnosticInput(
                event=event,
                recorded_at_utc=self._client_api.datetime.now(self._client_api.timezone.utc),
                location_id_to_bridge_key=self.location_id_to_bridge_key,
                last_processed_game_state_seq=self.last_processed_game_state_seq,
                check_reporting_enabled=bool(getattr(self, "kh3_check_reporting_enabled", False)),
                connected=self._is_ap_connected(),
                server_address=self.server_address,
                team=self.team,
                slot=self.slot,
                location_check_messages=tuple(location_check_msgs) if location_check_msgs is not None else None,
                locations=locations,
                reason=reason,
                error=error,
                source_cmd=source_cmd,
            ),
        )

    def offline_bridge_matches_installed_metadata(self, metadata: dict[str, Any]) -> bool:
        if self.bridge_dir is None:
            return False
        return self._get_offline_bridge_service().offline_bridge_matches(metadata, self.bridge_dir)

    def write_offline_bridge_files(
        self,
        metadata: dict[str, Any],
        pak_path: Path,
        *,
        preserve_previous_status: bool = False,
    ) -> None:
        if self.bridge_dir is None:
            return
        source = self._client_api.OfflineBridgeInitializationInput(
            current_bridge_dir=None,
            bridge_root=self.bridge_dir.parent,
            mods_dir=self._client_api.Path(),
            expected_seed=None,
            auth=self.auth,
            server_address=self.server_address,
            team=self.team,
        )
        self._get_offline_bridge_service().write_offline_files(
            source=source,
            metadata=metadata,
            pak_path=pak_path,
            bridge_dir=self.bridge_dir,
            seed_name=self.offline_seed_name or metadata.get("seed"),
            slot_data=self.slot_data,
            location_count=len(self.location_key_to_id),
            preserve_previous_status=preserve_previous_status,
        )

    def reset_bridge_observation_state(self, *, reason: str, goal_complete: bool = False) -> None:
        if self.bridge_dir is None:
            return
        publisher = self.__dict__.get("bridge_publisher")
        if publisher is not None:
            publisher.invalidate_slot_data()
        self._get_bridge_sync_service().reset_observation_state(
            self._client_api.ObservationResetInput(
                bridge_dir=self.bridge_dir,
                reason=reason,
                checked_locations=self.checked_locations,
                locations_checked=self.locations_checked,
                location_id_to_bridge_key=self.location_id_to_bridge_key,
                goal_complete=goal_complete,
            )
        )

    def mark_existing_bridge_notifications_processed(self) -> None:
        self._get_client_notification_service().mark_existing_bridge_notifications_processed()

    def ensure_bridge_observation_state(self, *, reason: str) -> None:
        if self.bridge_dir is None:
            return
        self._get_bridge_sync_service().ensure_observation_state(
            self._client_api.ObservationResetInput(
                bridge_dir=self.bridge_dir,
                reason=reason,
                checked_locations=getattr(self, "checked_locations", set()),
                locations_checked=getattr(self, "locations_checked", set()),
                location_id_to_bridge_key=self.location_id_to_bridge_key,
            )
        )

    def initialize_bridge(self) -> None:
        bridge_root = self.kh3_bridge_root()
        result = self._get_bridge_sync_service().initialize(
            self._client_api.BridgeInitializationInput(
                slot_data=self.slot_data,
                bridge_root=bridge_root,
                seed_name=self.seed_name,
                player_name=self.auth or self.player_names.get(self.slot, "kh3"),
                previous_bridge_dir=self.bridge_dir,
                checked_locations=self.checked_locations,
                locations_checked=self.locations_checked,
                finished_game=self.finished_game,
                bridge_dir_name=self._active_kh3_bridge_dir_name(),
            )
        )
        self.bridge_dir = result.bridge_dir
        self._get_poptracker_sync_service().reset()
        self._kh3_bridge_seed_scope = self._client_api.ItemSyncService.seed_scope(self.slot_data).get("kh3_seed")
        self._get_client_transport_service().clear_payload_cache()
        self.location_key_to_id = result.location_key_to_id
        self.location_id_to_bridge_key = result.location_id_to_bridge_key
        self.locations_checked = result.locations_checked
        self.rebuild_location_display_indexes()
        self.mark_existing_bridge_notifications_processed()
        self.mark_existing_runtime_game_over_processed()

    def _create_bridge_publication_service(self) -> BridgePublicationService:
        return self._client_api.BridgePublicationService(
            self._client_api.BridgeRefreshCallbacks(
                build_items=self.build_items_payload,
                select_items=lambda items, bridge: self._get_item_sync_service().payload_for_refresh(
                    items,
                    bridge_dir=bridge,
                    ap_connected=self._is_ap_connected(),
                    received_items_snapshot_ready=bool(getattr(self, "kh3_received_items_snapshot_ready", False)),
                ),
                build_status=self.build_status_payload,
                success_callback=lambda: (
                    event.set
                    if (event := getattr(self, "integrated_bridge_activity_event", None)) is not None
                    else None
                ),
                publisher=self._get_bridge_publisher,
                profile_scope=self._client_api.PythonProfileScope,
            )
        )

    def write_bridge_files(self) -> asyncio.Future[None] | None:
        if self.bridge_dir is None:
            return None
        return self._create_bridge_publication_service().refresh(
            self._client_api.BridgeRefreshInput(
                bridge_dir=self.bridge_dir,
                slot_data=getattr(self, "slot_data", {}),
                owner_seed=getattr(self, "_kh3_bridge_seed_scope", None),
                room_seed=getattr(self, "seed_name", None),
            )
        )

    def _is_ap_connected(self) -> bool:
        return bool(self.server and self.server.socket and not self.server.socket.closed)

    def build_items_payload(self) -> dict[str, Any]:
        source = self._client_api.ItemSyncInput(
            received_items=self.items_received,
            game=self.game,
            player_names=self.player_names,
            location_id_to_bridge_key=getattr(self, "location_id_to_bridge_key", {}),
            slot_data=getattr(self, "slot_data", {}),
            bridge_save_dir=getattr(self, "bridge_save_dir", None),
            lookup_item_name=lambda item_id, game: self.item_names.lookup_in_game(item_id, game),
            lookup_location_name=lambda location_id, slot: self.location_names.lookup_in_slot(location_id, slot),
        )
        return self._get_item_sync_service().cached_items_payload(source)

    def build_slot_location_index(self) -> dict[str, dict[str, Any]]:
        return self._get_item_sync_service().build_slot_location_index(getattr(self, "slot_data", {}))

    async def handle_game_state_file(self) -> None:
        if self.bridge_dir is None or getattr(self, "_bridge_observation_paused", False):
            return
        await self._get_bridge_sync_service().synchronize_game_state(
            self._client_api.GameStateSyncInput(
                bridge_dir=self.bridge_dir,
                location_key_to_id=self.location_key_to_id,
                check_reporting_enabled=bool(getattr(self, "kh3_check_reporting_enabled", False)),
            )
        )

    async def handle_bridge_notifications_file(self) -> None:
        self._get_client_notification_service().handle_bridge_notifications_file()

    async def handle_secret_report_hints(self) -> None:
        await self._get_secret_report_hint_service().publish_pending(
            self._client_api.SecretReportHintCallbacks(
                ready=lambda: self.bridge_dir is not None and self._is_ap_connected(),
                payload=self._load_bridge_sidecar_payload,
                send=self.send_msgs,
            )
        )

    async def handle_bridge_client_command_file(self) -> None:
        plan = self._get_client_command_service().load_bridge_command(
            self._client_api.BridgeCommandInput(
                last_processed_sequence=self.last_processed_client_command_seq,
                ap_connected=bool(self.server and self.server.socket and not self.server.socket.closed),
            )
        )
        await self._apply_bridge_client_command_plan(plan)

    async def _apply_bridge_client_command_plan(self, plan: ClientCommandPlan) -> None:
        await self._create_client_command_executor().apply_bridge_plan(plan)

    def _get_poptracker_sync_service(self) -> PopTrackerSyncService:
        service = getattr(self, "poptracker_sync_service", None)
        if service is None:
            service = self._client_api.PopTrackerSyncService()
            self.poptracker_sync_service = service
        return service

    def _poptracker_route(self) -> TrackerRoute:
        return self._client_api.TrackerRoute(
            team=int(getattr(self, "team", 0) or 0),
            slot=self.slot,
            slot_name=self.player_names.get(self.slot, self.auth),
            seed_name=self.seed_name or getattr(self, "offline_seed_name", None),
        )

    def _poptracker_callbacks(self) -> TrackerCallbacks:
        return self._client_api.TrackerCallbacks(
            ready=lambda: self.bridge_dir is not None and self.slot is not None and self._is_ap_connected(),
            payload=self._load_bridge_sidecar_payload,
            route=self._poptracker_route,
            send=self.send_msgs,
        )

    async def handle_poptracker_runtime_context_bounce(self) -> None:
        await self._get_poptracker_sync_service().publish_map(self._poptracker_callbacks())

    async def handle_poptracker_lucky_emblem_state(self) -> None:
        await self._get_poptracker_sync_service().publish_emblems(self._poptracker_callbacks())
