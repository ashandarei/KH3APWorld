"""Select seed-owned AP snapshots before submitting an ordered bridge write."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bridge_publisher import OrderedBridgePublisher
from .item_sync_service import ItemSyncService


@dataclass(frozen=True)
class BridgeRefreshInput:
    bridge_dir: Path | None
    slot_data: dict[str, Any]
    owner_seed: Mapping[str, Any] | None
    room_seed: str | None


@dataclass(frozen=True)
class BridgeRefreshCallbacks:
    build_items: Callable[[], dict[str, Any]]
    select_items: Callable[[dict[str, Any], Path], dict[str, Any]]
    build_status: Callable[[dict[str, Any]], dict[str, Any]]
    success_callback: Callable[[], Callable[[], None] | None]
    publisher: Callable[[], OrderedBridgePublisher]
    profile_scope: Callable[[str], AbstractContextManager[Any]]


class BridgePublicationService:
    def __init__(self, callbacks: BridgeRefreshCallbacks) -> None:
        self._client = callbacks

    def refresh(self, source: BridgeRefreshInput) -> asyncio.Future[None] | None:
        if source.bridge_dir is None or not self._owns_bridge(source):
            return None
        with self._client.profile_scope("client:write_bridge_files"):
            items = self._client.select_items(self._client.build_items(), source.bridge_dir)
            on_success = self._client.success_callback()
            return self._client.publisher().submit(
                source.bridge_dir,
                slot_data=source.slot_data,
                status_payload=self._client.build_status(items),
                items_payload=items,
                on_success=on_success,
            )

    @staticmethod
    def _owns_bridge(source: BridgeRefreshInput) -> bool:
        current = ItemSyncService.seed_scope(source.slot_data).get("kh3_seed")
        if source.owner_seed and current and not ItemSyncService.same_seed_slot(source.owner_seed, current):
            # A new room's asynchronous preparation has not selected its bridge.
            return False
        return not (
            current
            and source.room_seed
            and not ItemSyncService.same_seed_slot(current, {**current, "seed_name": source.room_seed})
        )
