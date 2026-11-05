"""Protocol payload and aggregate status ownership for KH3 bridge sessions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..client_support.json_snapshot import freeze_json
from .paths import AP_ITEMS_FILE, AP_STATUS_FILE
from .store import BridgeStore


class StatusSource(Protocol):
    """Facade operations required to assemble a bridge status snapshot."""

    bridge_dir: Any
    slot_data: dict[str, Any]

    def load_game_state(self) -> dict[str, Any]: ...
    def load_treasure_review_state(self) -> dict[str, Any]: ...
    def load_item_delivery_state(self) -> dict[str, Any]: ...
    def build_item_delivery_payload(self) -> dict[str, Any]: ...
    def build_item_delivery_ledger_summary(self) -> dict[str, Any]: ...
    def get_next_pending_action(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BridgeStatusSnapshot:
    """Typed internal status boundary with compatibility payload serialization."""

    bridge_dir: str
    protocol_name: str
    protocol_version: int
    tracked_checks: int
    reported_checks: int
    received_item_count: int | None
    pending_ap_item_count: int | None
    applied_ap_item_count: int | None
    last_applied_item_index: int | None
    last_applied_actions: tuple[Any, ...]
    item_delivery_ledger: dict[str, Any]
    next_pending_action: dict[str, Any]
    last_confirmed_treasure: Any
    reviewed_treasure_confirmations: int
    ap_status: dict[str, Any]
    game_state: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Serialize the snapshot to the established public status shape."""
        return {
            "bridge_dir": self.bridge_dir,
            "protocol_name": self.protocol_name,
            "protocol_version": self.protocol_version,
            "tracked_checks": self.tracked_checks,
            "reported_checks": self.reported_checks,
            "received_item_count": self.received_item_count,
            "pending_ap_item_count": self.pending_ap_item_count,
            "applied_ap_item_count": self.applied_ap_item_count,
            "last_applied_item_index": self.last_applied_item_index,
            "last_applied_actions": list(self.last_applied_actions),
            "item_delivery_ledger": self.item_delivery_ledger,
            "next_pending_action": self.next_pending_action,
            "last_confirmed_treasure": self.last_confirmed_treasure,
            "reviewed_treasure_confirmations": self.reviewed_treasure_confirmations,
            "ap_status": self.ap_status,
            "game_state": self.game_state,
        }


class BridgeStatusService:
    """Read AP-owned protocol sidecars and assemble the public status shape."""

    def __init__(
        self,
        store: BridgeStore,
        *,
        validate_protocol_payload: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
    ) -> None:
        self.store = store
        self.validate_protocol_payload = validate_protocol_payload
        self.protocol_error = protocol_error
        self._items_view_signature = None
        self._items_view = None

    def load_ap_items_view(self) -> dict[str, Any]:
        """Share an immutable receipt snapshot until its source revision changes."""
        signature = self.store.signature(AP_ITEMS_FILE)
        if self._items_view is not None and signature == self._items_view_signature:
            return self._items_view
        payload = self.store.read_optional_json_view(AP_ITEMS_FILE)
        if payload is None:
            return freeze_json({"items": []})
        payload = self.validate_protocol_payload(payload, str(self.store.path(AP_ITEMS_FILE)))
        view = freeze_json(payload)
        # A concurrent AP publication must not label old data with a new revision.
        if self.store.signature(AP_ITEMS_FILE) == signature:
            self._items_view_signature, self._items_view = signature, view
        return view

    def load_ap_items_payload(self) -> dict[str, Any]:
        """Load and validate AP items, accepting the established missing-file default."""
        path = self.store.path(AP_ITEMS_FILE)
        payload = self.store.read_optional_json(AP_ITEMS_FILE)
        if payload is None:
            return {"items": []}
        return self.validate_protocol_payload(payload, str(path))

    def load_ap_status_payload(self) -> dict[str, Any]:
        """Load AP status while preserving the compatibility fallback for invalid data."""
        path = self.store.path(AP_STATUS_FILE)
        payload = self.store.read_optional_json(AP_STATUS_FILE)
        if payload is None:
            return {}
        try:
            return self.validate_protocol_payload(payload, str(path))
        except self.protocol_error:
            return {}

    def _load_ap_status_for_build(self) -> dict[str, Any]:
        path = self.store.path(AP_STATUS_FILE)
        payload = self.store.read_optional_json(AP_STATUS_FILE)
        if payload is None:
            return {}
        return self.validate_protocol_payload(payload, str(path))

    def build_snapshot(self, source: StatusSource) -> BridgeStatusSnapshot:
        """Collect facade-owned state into an immutable status boundary."""
        game_state = source.load_game_state()
        status_payload = self._load_ap_status_for_build()
        review_state = source.load_treasure_review_state()
        item_delivery_state = source.load_item_delivery_state()
        item_delivery_payload = source.build_item_delivery_payload()
        native_summary = item_delivery_payload.get("native_delivery_summary", {})
        ledger_summary = source.build_item_delivery_ledger_summary()
        applied_confirmations = list(review_state.get("applied_confirmations", []))
        last_confirmed_treasure = applied_confirmations[-1] if applied_confirmations else None
        last_applied_actions = list(item_delivery_state.get("last_applied_actions", []))
        return BridgeStatusSnapshot(
            bridge_dir=str(source.bridge_dir),
            protocol_name=source.slot_data["bridge"]["protocol_name"],
            protocol_version=source.slot_data["bridge"]["protocol_version"],
            tracked_checks=len(source.slot_data["bridge"]["locations"]),
            reported_checks=len(game_state.get("checked_locations", [])),
            received_item_count=native_summary.get("received_count"),
            pending_ap_item_count=native_summary.get("pending_count"),
            applied_ap_item_count=native_summary.get("applied_count"),
            last_applied_item_index=native_summary.get("last_applied_index"),
            last_applied_actions=tuple(last_applied_actions),
            item_delivery_ledger=ledger_summary,
            next_pending_action=source.get_next_pending_action(),
            last_confirmed_treasure=last_confirmed_treasure,
            reviewed_treasure_confirmations=len(applied_confirmations),
            ap_status=status_payload,
            game_state=game_state,
        )

    def build_status(self, source: StatusSource) -> dict[str, Any]:
        """Return the exact public status dictionary retained by the facade."""
        return self.build_snapshot(source).to_payload()
