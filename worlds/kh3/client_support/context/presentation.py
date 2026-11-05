"""Status presentation and client command adapters."""

from __future__ import annotations

from typing import Any

from ..client_command_service import ClientCommandPlan, ClientCommandService
from ..presentation import ClientStatusPresenter
from ..status_snapshot_service import ClientStatusSnapshotService

class ClientPresentationMixin:
    """Status presentation and client command adapters."""

    def _get_client_command_service(self) -> ClientCommandService:
        service = self.__dict__.get("client_command_service")
        if not isinstance(service, self._client_api.ClientCommandService):
            service = self._create_client_command_service()
            self.client_command_service = service
        return service

    def _create_status_snapshot_service(self) -> ClientStatusSnapshotService:
        return self._client_api.ClientStatusSnapshotService(
            presenter=self._get_client_status_presenter(),
            protocol_payload_reader=self._load_bridge_payload,
            sidecar_payload_reader=self._load_bridge_sidecar_payload,
            latest_save_load_reader=lambda: (self._load_bridge_sidecar_payload("runtime_reconciliation_state.json") or {}).get("last_save_load_event"),
            current_save_slot_file=self._client_api.RUNTIME_CURRENT_SAVE_SLOT_FILE,
            display_save_slot_file=self._client_api.RUNTIME_DISPLAY_SAVE_SLOT_FILE,
        )

    def _get_status_snapshot_service(self) -> ClientStatusSnapshotService:
        service = self.__dict__.get("status_snapshot_service")
        if not isinstance(service, self._client_api.ClientStatusSnapshotService):
            service = self._create_status_snapshot_service()
            self.status_snapshot_service = service
        return service

    def _load_gui_status_payloads(self) -> dict[str, dict[str, Any]]:
        return dict(
            self._get_status_snapshot_service().load_inputs().as_presenter_payloads()
        )

    def _get_client_status_presenter(self) -> ClientStatusPresenter:
        presenter = getattr(self, "client_status_presenter", None)
        if not isinstance(presenter, self._client_api.ClientStatusPresenter):
            presenter = self._client_api.ClientStatusPresenter(self)
            self.client_status_presenter = presenter
        return presenter

    def open_bridge_dir(self) -> None:
        self._apply_client_facade_plan(
            self._get_client_command_service().plan_facade_command(
                self._client_api.ClientCommandKind.OPEN_BRIDGE_DIRECTORY,
                self._client_api.FacadeCommandInput(bridge_initialized=self.bridge_dir is not None),
            )
        )

    def open_kh3_mods_dir(self) -> None:
        self._apply_client_facade_plan(
            self._get_client_command_service().plan_facade_command(
                self._client_api.ClientCommandKind.OPEN_KH3_MODS_DIRECTORY,
            )
        )

    def _apply_client_facade_plan(self, plan: ClientCommandPlan) -> None:
        self._create_client_command_executor().apply_plan(plan)
