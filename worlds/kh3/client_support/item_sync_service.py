"""Synchronize Archipelago received items into the KH3 bridge payload."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..Ingredients import ingredient_location_description

from .json_snapshot import chunked_json_array, freeze_json
from .seed_guard import SeedGuardService


@dataclass(frozen=True)
class ItemSyncInput:
    """Explicit AP and slot state needed to construct ``ap_items.json``."""

    received_items: Iterable[Any]
    game: str
    player_names: dict[int, str]
    location_id_to_bridge_key: dict[int, str]
    slot_data: dict[str, Any]
    bridge_save_dir: Path | None
    lookup_item_name: Callable[[int, str], str]
    lookup_location_name: Callable[[int, int], str]


class ItemSyncService:
    """Own received-item normalization, indexing, and AP-items representation."""

    def __init__(
        self,
        *,
        protocol_name: str,
        protocol_version: int,
        load_optional_json: Callable[[Path], dict[str, Any] | None],
        validate_protocol: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
        resolve_item_display_name: Callable[[str], str],
        log_warning: Callable[..., None],
    ) -> None:
        self.protocol_name = protocol_name
        self.protocol_version = protocol_version
        self.load_optional_json = load_optional_json
        self.validate_protocol = validate_protocol
        self.protocol_error = protocol_error
        self.resolve_item_display_name = resolve_item_display_name
        self.log_warning = log_warning
        self.invalidate_items()

    def invalidate_items(self) -> None:
        """Discard normalized receipts after a reset, resync, or new data package."""
        self._item_context: tuple[Any, ...] | None = None
        self._received_prefix: tuple[Any, ...] = ()
        self._item_chunks = ()
        self._seen_sources: set[tuple[int, int]] = set()
        self._slot_location_index: dict[str, dict[str, Any]] = {}
        self._cached_items_payload: dict[str, Any] | None = None
        self._item_context_owners: tuple[Any, ...] = ()

    def cached_items_payload(
        self,
        source: ItemSyncInput,
    ) -> dict[str, Any]:
        """Normalize only appended receipts; retain an immutable full snapshot."""
        received = tuple(source.received_items)
        context = (
            source.game, id(source.slot_data), id(source.location_id_to_bridge_key),
            tuple(sorted(source.player_names.items())),
        )
        if context != self._item_context or received[:len(self._received_prefix)] != self._received_prefix:
            self.invalidate_items()
            self._item_context = context
            # Retain the owners of identity-keyed indexes for the cache lifetime.
            self._item_context_owners = (
                source.slot_data, source.location_id_to_bridge_key,
            )
            self._slot_location_index = self.build_slot_location_index(source.slot_data)
        if received == self._received_prefix and self._cached_items_payload is not None:
            return self._cached_items_payload
        added = []
        seen_sources = set(self._seen_sources)
        for index in range(len(self._received_prefix), len(received)):
            item = received[index]
            if not self._is_duplicate_source(item, index, seen_sources):
                added.append(self._build_received_item(
                    index, item, source, self._slot_location_index,
                ))
        if added:
            chunk = freeze_json(added)
            self._item_chunks += (chunk,)
        self._seen_sources = seen_sources
        self._received_prefix = received
        self._cached_items_payload = freeze_json({
            **self.empty_payload(), **self.seed_scope(source.slot_data), "items": chunked_json_array(
                self._item_chunks,
            ),
        })
        return self._cached_items_payload

    def empty_payload(self) -> dict[str, Any]:
        return {
            "protocol_name": self.protocol_name,
            "protocol_version": self.protocol_version,
            "items": [],
        }

    @staticmethod
    def seed_scope(slot_data: dict[str, Any]) -> dict[str, Any]:
        seed = slot_data.get("kh3_seed", {})
        if not isinstance(seed, dict) or not seed.get("seed_name"):
            return {}
        return {"kh3_seed": {key: seed.get(key) for key in ("seed_name", "slot", "slot_name")}}

    @staticmethod
    def same_seed_slot(previous: dict[str, Any], current: dict[str, Any]) -> bool:
        def seed_name(value: Any) -> str:
            return SeedGuardService.extract_seed_id(value) or str(value or "")

        return bool(
            current.get("seed_name")
            and seed_name(previous.get("seed_name")) == seed_name(current.get("seed_name"))
            and type(current.get("slot")) is int
            and type(previous.get("slot")) is int
            and previous["slot"] == current["slot"]
        )

    def load_cached_payload(
        self, bridge_dir: Path | None, *, expected_seed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Load and normalize the existing bridge AP-items payload."""
        if bridge_dir is None:
            return self.empty_payload()
        items_path = bridge_dir / "ap_items.json"
        payload = self.load_optional_json(items_path)
        if not isinstance(payload, dict):
            return self.empty_payload()
        try:
            payload = self.validate_protocol(payload, str(items_path))
        except self.protocol_error:
            return self.empty_payload()
        if expected_seed is not None:
            owner = payload.get("kh3_seed")
            if not isinstance(owner, dict) or not self.same_seed_slot(owner, expected_seed):
                return self.empty_payload()
            payload = {**payload, "kh3_seed": dict(expected_seed)}
        if not isinstance(payload.get("items"), list):
            payload["items"] = []
        return payload

    def payload_for_refresh(
        self,
        built_payload: dict[str, Any],
        *,
        bridge_dir: Path | None,
        ap_connected: bool,
        received_items_snapshot_ready: bool = False,
    ) -> dict[str, Any]:
        """Preserve cached items until the server's initial item snapshot is authoritative."""
        if built_payload.get("items") or (ap_connected and received_items_snapshot_ready):
            return built_payload
        cached_payload = self.load_cached_payload(bridge_dir, expected_seed=built_payload.get("kh3_seed"))
        return cached_payload if cached_payload.get("items") else built_payload

    @staticmethod
    def handle_received_items(
        bridge_dir: Path | None,
        *,
        refresh_bridge_files: Callable[[], Any],
    ) -> Any:
        """Refresh bridge-facing state after the AP ReceivedItems callback."""
        if bridge_dir is not None:
            return refresh_bridge_files()

    def received_item_count(
        self,
        received_items: Iterable[Any],
        *,
        bridge_dir: Path | None,
        ap_connected: bool,
        received_items_snapshot_ready: bool = False,
        expected_seed: dict[str, Any] | None = None,
    ) -> int:
        count = len(received_items) if hasattr(received_items, "__len__") else sum(1 for _ in received_items)
        if count == 0 and (not ap_connected or not received_items_snapshot_ready):
            count = len(self.load_cached_payload(bridge_dir, expected_seed=expected_seed).get("items", []))
        return count

    def build_items_payload(
        self,
        source: ItemSyncInput,
    ) -> dict[str, Any]:
        """Build the ordered bridge representation of AP received items."""
        slot_location_index = self.build_slot_location_index(source.slot_data)
        items: list[dict[str, Any]] = []
        seen_ap_source_keys: set[tuple[int, int]] = set()
        for index, network_item in enumerate(source.received_items):
            if self._is_duplicate_source(network_item, index, seen_ap_source_keys):
                continue
            items.append(
                self._build_received_item(
                    index,
                    network_item,
                    source,
                    slot_location_index,
                )
            )

        payload = self.empty_payload()
        payload.update(self.seed_scope(source.slot_data))
        payload["items"] = items
        return payload


    def _is_duplicate_source(
        self,
        network_item: Any,
        index: int,
        seen_ap_source_keys: set[tuple[int, int]],
    ) -> bool:
        try:
            source_location_id = int(network_item.location)
            source_player_id = int(network_item.player)
        except (TypeError, ValueError):
            return False
        if source_location_id < 0:
            return False
        source_key = (source_player_id, source_location_id)
        if source_key not in seen_ap_source_keys:
            seen_ap_source_keys.add(source_key)
            return False
        self.log_warning(
            "Skipping duplicate AP received item source from player %s at location %s "
            "while building KH3 bridge item payload; receive index %s will not be queued.",
            source_player_id,
            source_location_id,
            index,
        )
        return True

    def _build_received_item(
        self,
        index: int,
        network_item: Any,
        source: ItemSyncInput,
        slot_location_index: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        raw_item_name = source.lookup_item_name(network_item.item, source.game)
        item_name = self.resolve_item_display_name(raw_item_name)
        item_payload = {
            "index": index,
            "item_id": network_item.item,
            "item_name": item_name,
            "location_id": network_item.location,
            "location_name": source.lookup_location_name(network_item.location, network_item.player),
            "from_player": network_item.player,
            "from_player_name": source.player_names.get(network_item.player, str(network_item.player)),
            "flags": network_item.flags,
        }
        if raw_item_name != item_name:
            item_payload["kh3_raw_item_name"] = str(raw_item_name)
        # Location IDs can repeat across slots. Seed-specific descriptions and
        # bridge keys belong only to receipts from this player's own world.
        local_slot = source.slot_data.get("kh3_seed", {}).get("slot")
        bridge_key = (source.location_id_to_bridge_key.get(network_item.location)
                      if network_item.player == local_slot else None)
        if bridge_key:
            item_payload["bridge_key"] = bridge_key
            slot_location = slot_location_index.get(bridge_key)
            if slot_location:
                display_location_name = self.display_location_name(slot_location)
                if display_location_name:
                    item_payload["location_name"] = display_location_name
        return item_payload

    @staticmethod
    def build_slot_location_index(slot_data: dict[str, Any] | Any) -> dict[str, dict[str, Any]]:
        bridge_data = slot_data.get("bridge", {}) if isinstance(slot_data, dict) else {}
        locations = bridge_data.get("locations", [])
        if not isinstance(locations, list):
            return {}
        index: dict[str, dict[str, Any]] = {}
        for location in locations:
            if not isinstance(location, dict):
                continue
            bridge_key = location.get("bridge_key")
            if isinstance(bridge_key, str) and bridge_key:
                index[bridge_key] = location
        return index

    @staticmethod
    def display_location_name(slot_location: dict[str, Any]) -> str | None:
        ingredient = ingredient_location_description(slot_location)
        if ingredient:
            return ingredient
        location_description = str(slot_location.get("location_description") or "").strip()
        return location_description or None
