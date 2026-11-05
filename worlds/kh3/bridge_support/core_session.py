"""Composition root for the focused core of ``KH3BridgeSession``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .game_state import GameStateService
from .location_index import LocationIndex
from .paths import SLOT_DATA_FILE
from .status import BridgeStatusService
from .store import BridgeStore
from .time import UtcClock


class CoreSession:
    """Load immutable session metadata and compose core bridge services."""

    def __init__(
        self,
        store: BridgeStore,
        *,
        clock: UtcClock,
        validate_protocol_payload: Callable[[dict[str, Any], str], dict[str, Any]],
        validate_slot_data_payload: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
    ) -> None:
        self.store = store
        self.validate_slot_data_payload = validate_slot_data_payload
        self.game_state = GameStateService(
            store,
            clock=clock,
            validate_protocol_payload=validate_protocol_payload,
            protocol_error=protocol_error,
        )
        self.status = BridgeStatusService(
            store,
            validate_protocol_payload=validate_protocol_payload,
            protocol_error=protocol_error,
        )
        self.slot_data: dict[str, Any] | None = None
        self.locations: LocationIndex | None = None

    def initialize(self) -> None:
        """Load validated slot metadata and construct its immutable location index."""
        slot_data_path = self.store.path(SLOT_DATA_FILE)
        if not slot_data_path.exists():
            raise FileNotFoundError(f"Missing slot_data.json in bridge directory: {self.store.root}")
        slot_data = self.store.read_json(SLOT_DATA_FILE)
        self.validate_slot_data_payload(slot_data, str(slot_data_path))
        self.slot_data = slot_data
        self.locations = LocationIndex(slot_data)
