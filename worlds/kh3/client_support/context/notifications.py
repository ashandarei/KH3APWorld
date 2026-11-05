"""Notifications, item synchronization, hints, and DeathLink adapters."""

from __future__ import annotations

from ..deathlink_service import DeathLinkService
from ..item_sync_service import ItemSyncService
from ..notification_service import ClientNotificationService, NotificationState
from ..secret_report_hint_service import SecretReportHintService

class ClientNotificationsMixin:
    """Notifications, item synchronization, hints, and DeathLink adapters."""

    def _notification_state(self) -> NotificationState:
        service = self.__dict__.get("client_notification_service")
        if isinstance(service, self._client_api.ClientNotificationService):
            self._kh3_notification_state = service.state
            return service.state
        state = self.__dict__.get("_kh3_notification_state")
        if state is None:
            state = self._kh3_notification_state = self._client_api.NotificationState()
        return state

    @property
    def last_processed_bridge_notification_seq(self) -> int:
        return self._notification_state().last_bridge_notification_seq

    @last_processed_bridge_notification_seq.setter
    def last_processed_bridge_notification_seq(self, value: int) -> None:
        self._notification_state().last_bridge_notification_seq = int(value or 0)

    def _create_client_notification_service(self) -> ClientNotificationService:
        return self._client_api.ClientNotificationService(
            self,
            present_print_json=self._present_notification_print_json,
            load_optional_json=self._client_api.load_optional_json_file,
            poll_json=lambda path: self._get_client_transport_service().load_sidecar_payload(
                path.parent,
                path.name,
                read_only=True,
            ),
            validate_protocol=self._client_api.validate_protocol_payload,
            protocol_error=self._client_api.BridgeProtocolError,
            log_info=lambda *args: self._client_api.logger.info(*args),
            log_warning=lambda *args: self._client_api.logger.warning(*args),
            state=self._notification_state(),
        )

    def _get_client_notification_service(self) -> ClientNotificationService:
        service = self.__dict__.get("client_notification_service")
        if not isinstance(service, self._client_api.ClientNotificationService):
            service = self._create_client_notification_service()
            self.client_notification_service = service
        return service

    def _create_item_sync_service(self) -> ItemSyncService:
        return self._client_api.ItemSyncService(
            protocol_name=self._client_api.BRIDGE_PROTOCOL_NAME,
            protocol_version=self._client_api.BRIDGE_PROTOCOL_VERSION,
            load_optional_json=self._client_api.load_optional_json_file,
            validate_protocol=self._client_api.validate_protocol_payload,
            protocol_error=self._client_api.BridgeProtocolError,
            resolve_item_display_name=self._client_api.resolve_item_display_name,
            log_warning=lambda *args: self._client_api.logger.warning(*args),
        )

    def _get_item_sync_service(self) -> ItemSyncService:
        service = self.__dict__.get("item_sync_service")
        if not isinstance(service, self._client_api.ItemSyncService):
            service = self._create_item_sync_service()
            self.item_sync_service = service
        return service

    def _get_secret_report_hint_service(self) -> SecretReportHintService:
        service = self.__dict__.get("secret_report_hint_service")
        if not isinstance(service, self._client_api.SecretReportHintService):
            service = self._client_api.SecretReportHintService()
            self.secret_report_hint_service = service
        return service

    def _create_deathlink_service(self) -> DeathLinkService:
        return self._client_api.DeathLinkService(
            send_death=self.send_death,
            deathlink_enabled=lambda: "DeathLink" in getattr(self, "tags", set()),
            has_player=lambda: self.slot is not None and self.slot in getattr(self, "player_names", {}),
            server_connected=lambda: bool(self.server and self.server.socket and not self.server.socket.closed),
            log_info=lambda *args: self._client_api.logger.info(*args),
            log_warning=lambda *args: self._client_api.logger.warning(*args),
            log_exception=lambda *args: self._client_api.logger.exception(*args),
            read_settings=lambda bridge_dir: self._client_api.deathlink_settings.read_settings(
                bridge_dir, getattr(self, "slot_data", {})
            ),
            update_enabled=self.update_death_link,
        )

    def _get_deathlink_service(self) -> DeathLinkService:
        service = self.__dict__.get("deathlink_service")
        if not isinstance(service, self._client_api.DeathLinkService):
            service = self._create_deathlink_service()
            self.deathlink_service = service
        return service

    def mark_existing_runtime_game_over_processed(self) -> None:
        self._get_deathlink_service().mark_existing_game_over_processed(self.bridge_dir)

    async def handle_runtime_deathlink_game_over(self) -> None:
        await self._get_deathlink_service().handle_runtime_game_over(self.bridge_dir)

    def rebuild_location_display_indexes(self) -> None:
        self._get_client_notification_service().rebuild_location_display_indexes()

    def configure_probe_for_current_bridge(self) -> None:
        self._create_runtime_artifact_executor().configure_probe(self.probe_config_path)
