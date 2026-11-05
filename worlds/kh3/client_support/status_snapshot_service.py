"""Status-sidecar loading and GUI snapshot composition for the KH3 client."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .presentation import (
    ClientStatusPresenter,
    ClientStatusSnapshot,
    describe_highest_ap_index,
    display_pending_item_count,
)
from .replay_slot_presentation import (
    ReplaySlotSelectionInput,
    ReplaySlotSummary,
    build_replay_slot_summaries,
    describe_last_save_loaded,
    describe_save_slot,
    slot_replay_index,
)

Payload = Mapping[str, Any]
PayloadReader = Callable[[str], Any]
LatestSaveLoadReader = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class StatusSnapshotInputs:
    """Normalized bridge payloads consumed by ``ClientStatusPresenter``."""

    item_delivery_payload: Payload
    native_save_receipts: Payload
    runtime_current_save_slot: Payload
    runtime_display_save_slot: Payload
    last_runtime_save_load_event: Payload
    runtime_status: Payload
    runtime_ingredient_state: Payload = field(default_factory=dict)

    def as_presenter_payloads(self) -> dict[str, Payload]:
        payloads = {
            "item_delivery_payload": self.item_delivery_payload,
            "native_save_receipts": self.native_save_receipts,
            "runtime_current_save_slot": self.runtime_current_save_slot,
            "runtime_display_save_slot": self.runtime_display_save_slot,
            "last_runtime_save_load_event": self.last_runtime_save_load_event,
            "runtime_status": self.runtime_status,
            "runtime_ingredient_state": self.runtime_ingredient_state,
        }
        return payloads

    def as_replay_slot_selection_input(self) -> ReplaySlotSelectionInput:
        return ReplaySlotSelectionInput(
            native_save_receipts=self.native_save_receipts,
            runtime_current_save_slot=self.runtime_current_save_slot,
            runtime_display_save_slot=self.runtime_display_save_slot,
            last_runtime_save_load_event=self.last_runtime_save_load_event,
        )


class ClientStatusSnapshotService:
    """Load status sidecars and compose presenter-owned GUI models."""

    def __init__(
        self,
        *,
        presenter: ClientStatusPresenter,
        protocol_payload_reader: PayloadReader,
        sidecar_payload_reader: PayloadReader,
        latest_save_load_reader: LatestSaveLoadReader,
        current_save_slot_file: str,
        display_save_slot_file: str,
    ) -> None:
        self.presenter = presenter
        self.protocol_payload_reader = protocol_payload_reader
        self.sidecar_payload_reader = sidecar_payload_reader
        self.latest_save_load_reader = latest_save_load_reader
        self.current_save_slot_file = current_save_slot_file
        self.display_save_slot_file = display_save_slot_file

    @staticmethod
    def _normalize_payload(payload: Any) -> Payload:
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def inputs_from_payloads(cls, payloads: Mapping[str, Any]) -> StatusSnapshotInputs:
        def normalized(key: str) -> Payload:
            return cls._normalize_payload(payloads.get(key))

        return StatusSnapshotInputs(
            item_delivery_payload=normalized("item_delivery_payload"),
            native_save_receipts=normalized("native_save_receipts"),
            runtime_current_save_slot=normalized("runtime_current_save_slot"),
            runtime_display_save_slot=normalized("runtime_display_save_slot"),
            last_runtime_save_load_event=normalized("last_runtime_save_load_event"),
            runtime_status=normalized("runtime_status"),
            runtime_ingredient_state=normalized("runtime_ingredient_state"),
        )

    def load_inputs(self) -> StatusSnapshotInputs:
        protocol = self.protocol_payload_reader
        sidecar = self.sidecar_payload_reader
        delivery = protocol("item_delivery.json")
        payloads = {
            "item_delivery_payload": delivery,
            "native_save_receipts": sidecar("runtime_save_receipts.json"),
            "runtime_current_save_slot": sidecar(self.current_save_slot_file),
            "runtime_display_save_slot": sidecar(self.display_save_slot_file),
            "last_runtime_save_load_event": self.latest_save_load_reader(),
            "runtime_status": sidecar("runtime_status.json"),
            "runtime_ingredient_state": sidecar("runtime_ingredient_state.json"),
        }
        return self.inputs_from_payloads(payloads)

    def build_snapshot_from_inputs(self, inputs: StatusSnapshotInputs) -> ClientStatusSnapshot:
        return self.presenter.build_status_snapshot_from_payloads(inputs.as_presenter_payloads())

    def build_snapshot(self) -> ClientStatusSnapshot:
        return self.build_snapshot_from_inputs(self.load_inputs())

    @staticmethod
    def build_replay_slots_from_inputs(inputs: StatusSnapshotInputs) -> tuple[ReplaySlotSummary, ...]:
        return build_replay_slot_summaries(inputs.as_replay_slot_selection_input())

    @staticmethod
    def describe_slot_replay_entries(
        native_save_receipts: Any,
        runtime_current_save_slot: Any = None,
        runtime_display_save_slot: Any = None,
        last_runtime_save_load_event: Any = None,
    ) -> list[dict[str, str]]:
        return [
            summary.as_legacy_dict()
            for summary in build_replay_slot_summaries(
                ReplaySlotSelectionInput(
                    native_save_receipts=native_save_receipts,
                    runtime_current_save_slot=runtime_current_save_slot,
                    runtime_display_save_slot=runtime_display_save_slot,
                    last_runtime_save_load_event=last_runtime_save_load_event,
                )
            )
        ]

    describe_last_save_loaded = staticmethod(describe_last_save_loaded)
    describe_save_slot = staticmethod(describe_save_slot)
    slot_replay_index = staticmethod(slot_replay_index)
    describe_highest_ap_index = staticmethod(describe_highest_ap_index)
    display_pending_item_count = staticmethod(display_pending_item_count)
