"""PopTracker payloads and publication state for KH3 maps and Lucky Emblems.

The client supplies runtime snapshots and an AP sender. This module owns only
tracker translation and duplicate suppression, never client or bridge files.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..MinimapReference import WORLD_NAMES, minimap_display_name
from ..PackageResources import read_package_text
from .json_snapshot import JsonObject

logger = logging.getLogger("Client")

KH3_LUCKY_EMBLEM_SPOTS_RESOURCE = "data/lucky_emblem_spots.json"
KH3_LUCKY_EMBLEM_STATE_FILE = "runtime_lucky_emblem_state.json"
KH3_LUCKY_EMBLEM_STORAGE_KEY_PREFIX = "kh3_lucky_emblems"

SendMessages = Callable[[list[dict[str, Any]]], Awaitable[None]]


@dataclass(frozen=True)
class TrackerRoute:
    """AP destination and player identity used by tracker messages."""

    team: int
    slot: int
    slot_name: str | None
    seed_name: str | None


@dataclass(frozen=True)
class TrackerCallbacks:
    """Current connection, sidecar reader, route, and AP transport boundary."""

    ready: Callable[[], bool]
    payload: Callable[[str], dict[str, Any] | None]
    route: Callable[[], TrackerRoute]
    send: SendMessages


def build_context_payload(
    runtime_status: dict[str, Any],
    *,
    slot: int | None = None,
    slot_name: str | None = None,
    seed_name: str | None = None,
) -> dict[str, Any] | None:
    """Format the DLL's interpreted map and coherent minimap for PopTracker."""
    if not isinstance(runtime_status, dict) or runtime_status.get("schema") != "kh3ap.runtime_status.v1":
        return None
    primary = runtime_status.get("map")
    if not isinstance(primary, dict) or not primary.get("asset_path"):
        return None
    context = runtime_status.get("context", {})
    minimap = runtime_status.get("minimap", {})
    payload = {
        "kh3_tracker_event": "map_changed",
        **{f"kh3_{key}": str(primary.get(key) or "") for key in
           ("object_path", "object_name", "path_kind", "world_code", "level_id", "room_id")},
        "kh3_level_path": primary["asset_path"],
        "kh3_hook": str(context.get("hook") or ""),
        "kh3_opened_at": str(context.get("opened_at") or ""),
        "kh3_raw_world": str(context.get("world") or ""),
        "kh3_raw_outer": str(context.get("outer") or ""),
        "slot": slot,
        "slot_name": slot_name,
        "seed_name": seed_name,
    }
    payload["kh3_world_name"] = WORLD_NAMES.get(payload["kh3_world_code"], payload["kh3_world_code"])
    if minimap:
        payload.update({
            "kh3_minimap_id": str(minimap.get("minimap_id") or ""),
            "kh3_minimap_texture_path": str(minimap.get("texture_path") or ""),
            "kh3_minimap_source": "TresUIP_NaviMapGra.SelectMap",
            "kh3_minimap_texture_id": str(minimap.get("texture_id") or ""),
            "kh3_minimap_observed_at": str(minimap.get("observed_at_utc") or ""),
        })
        payload["kh3_minimap_name"] = minimap_display_name(payload["kh3_minimap_id"], payload["kh3_world_code"])
    return payload


@lru_cache(maxsize=1)
def _load_emblem_catalog() -> dict[int, dict[str, Any]]:
    catalog: dict[int, dict[str, Any]] = {}
    try:
        payload = json.loads(
            read_package_text(
                KH3_LUCKY_EMBLEM_SPOTS_RESOURCE,
                module_file=__file__,
                encoding="utf-8-sig",
            )
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return catalog

    spots = payload.get("spots") if isinstance(payload, dict) else None
    if isinstance(spots, list):
        for spot in spots:
            if not isinstance(spot, dict):
                continue
            try:
                global_id = int(spot.get("global_id"))
            except (TypeError, ValueError):
                continue
            if 1 <= global_id <= 90:
                catalog[global_id] = dict(spot)

    return catalog


def _normalize_emblem_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        global_id = value
    elif isinstance(value, str) and value.strip().isdecimal():
        global_id = int(value.strip())
    else:
        return None
    return global_id if 1 <= global_id <= 90 else None


def emblem_storage_key(team: int, slot: int) -> str:
    """Return the room-local AP DataStorage key shared with PopTracker."""
    return f"{KH3_LUCKY_EMBLEM_STORAGE_KEY_PREFIX}_{int(team)}_{int(slot)}"


def build_emblem_payload(
    runtime_state: dict[str, Any],
    *,
    slot: int,
    slot_name: str | None = None,
    seed_name: str | None = None,
    catalog: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Translate the native exact-ID sidecar into an additive tracker snapshot."""
    if not isinstance(runtime_state, dict):
        return None

    captured_values = runtime_state.get("captured_global_ids")
    captured_ids = (
        {global_id for value in captured_values if (global_id := _normalize_emblem_id(value)) is not None}
        if isinstance(captured_values, list)
        else set()
    )
    latest_capture_id = _normalize_emblem_id(runtime_state.get("latest_capture_global_id"))
    if latest_capture_id is not None:
        captured_ids.add(latest_capture_id)
    if not captured_ids:
        return None

    spot_catalog = catalog if catalog is not None else _load_emblem_catalog()
    captured_spots = []
    for global_id in sorted(captured_ids):
        spot = spot_catalog.get(global_id, {})
        captured_spots.append(
            {
                "global_id": global_id,
                "world": str(spot.get("world") or ""),
                "world_index": spot.get("world_index"),
                "friendly_name": str(spot.get("friendly_name") or f"Lucky Emblem {global_id}"),
            }
        )

    try:
        sequence = max(0, int(runtime_state.get("sequence") or 0))
    except (TypeError, ValueError):
        sequence = 0

    return {
        "kh3_tracker_event": "lucky_emblem_state",
        "kh3_lucky_emblem_sequence": sequence,
        "kh3_lucky_emblem_global_ids": sorted(captured_ids),
        "kh3_lucky_emblem_latest_global_id": latest_capture_id,
        "kh3_lucky_emblem_spots": captured_spots,
        "kh3_runtime_context": str(runtime_state.get("runtime_context") or ""),
        "kh3_observed_at": str(runtime_state.get("observed_at_utc") or ""),
        "slot": slot,
        "slot_name": slot_name,
        "seed_name": seed_name,
    }


def build_emblem_sync_messages(
    payload: dict[str, Any],
    *,
    team: int,
    slot: int,
) -> list[dict[str, Any]]:
    """Build persistent and live AP messages for one additive emblem snapshot."""
    captured_ids = payload.get("kh3_lucky_emblem_global_ids")
    normalized_ids = (
        sorted({global_id for value in captured_ids if (global_id := _normalize_emblem_id(value)) is not None})
        if isinstance(captured_ids, list)
        else []
    )
    if not normalized_ids:
        return []

    storage_value = {str(global_id): True for global_id in normalized_ids}
    return [
        {
            "cmd": "Set",
            "key": emblem_storage_key(team, slot),
            "default": {},
            "want_reply": False,
            "operations": [{"operation": "update", "value": storage_value}],
        },
        {
            "cmd": "Bounce",
            "slots": [slot],
            "data": payload,
        },
    ]


class PopTrackerSyncService:
    """Publish changed snapshots and remember only successful publications."""

    def __init__(self) -> None:
        self._map_signature: tuple[str, str, str] | None = None
        self._emblem_signature: str | None = None
        self._emblem_snapshot: tuple[dict[str, Any], TrackerRoute] | None = None

    def reset(self) -> None:
        """Republish both channels after bridge initialization or replay reset."""
        self._map_signature = None
        self.reset_emblems()

    def reset_emblems(self) -> None:
        """Republish captured emblems after the AP connection state is reset."""
        self._emblem_signature = None
        self._emblem_snapshot = None

    async def publish_map(self, callbacks: TrackerCallbacks) -> None:
        if not callbacks.ready():
            return
        runtime_status = callbacks.payload("runtime_status.json")
        if not isinstance(runtime_status, dict):
            return
        await self.sync_map(
            runtime_status,
            route=callbacks.route(),
            send_messages=callbacks.send,
        )

    async def publish_emblems(self, callbacks: TrackerCallbacks) -> None:
        if not callbacks.ready():
            return
        await self.sync_emblems(
            callbacks.payload(KH3_LUCKY_EMBLEM_STATE_FILE),
            route=callbacks.route(),
            send_messages=callbacks.send,
        )

    async def sync_map(
        self,
        runtime_status: dict[str, Any],
        *,
        route: TrackerRoute,
        send_messages: SendMessages,
    ) -> None:
        payload = build_context_payload(
            runtime_status,
            slot=route.slot,
            slot_name=route.slot_name,
            seed_name=route.seed_name,
        )
        if payload is None:
            return
        signature = (
            payload["kh3_level_path"],
            payload.get("kh3_minimap_texture_path", ""),
            payload.get("kh3_minimap_id", ""),
        )
        if signature == self._map_signature:
            return
        # A temporarily unavailable HUD is not another zone change. Maps without
        # minimap support still send level-only messages when the level changes.
        if not signature[1] and self._map_signature and self._map_signature[0] == signature[0]:
            return
        await send_messages([{"cmd": "Bounce", "slots": [route.slot], "data": payload}])
        self._map_signature = signature

    async def sync_emblems(
        self,
        runtime_state: dict[str, Any] | None,
        *,
        route: TrackerRoute,
        send_messages: SendMessages,
    ) -> None:
        previous = self._emblem_snapshot
        if (
            isinstance(runtime_state, JsonObject)
            and previous is not None
            and runtime_state is previous[0]
            and route == previous[1]
        ):
            return
        payload = (
            build_emblem_payload(
                runtime_state,
                slot=route.slot,
                slot_name=route.slot_name,
                seed_name=route.seed_name,
            )
            if isinstance(runtime_state, dict)
            else None
        )
        if payload is None:
            return
        signature = json.dumps(
            {
                "key": emblem_storage_key(route.team, route.slot),
                "sequence": payload.get("kh3_lucky_emblem_sequence"),
                "ids": payload.get("kh3_lucky_emblem_global_ids"),
                "latest": payload.get("kh3_lucky_emblem_latest_global_id"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if signature == self._emblem_signature:
            self._emblem_snapshot = (runtime_state, route)
            return
        messages = build_emblem_sync_messages(payload, team=route.team, slot=route.slot)
        if not messages:
            return
        await send_messages(messages)
        self._emblem_signature = signature
        self._emblem_snapshot = (runtime_state, route)
        self._log_emblem_publication(payload)

    @staticmethod
    def _log_emblem_publication(payload: dict[str, Any]) -> None:
        latest_id = payload.get("kh3_lucky_emblem_latest_global_id")
        if latest_id is not None:
            latest_spot = next(
                (spot for spot in payload.get("kh3_lucky_emblem_spots", []) if spot.get("global_id") == latest_id),
                {},
            )
            logger.debug(
                "Sent photographed Lucky Emblem %s to PopTracker (%s).",
                latest_id,
                latest_spot.get("friendly_name") or f"Lucky Emblem {latest_id}",
            )
        else:
            logger.debug(
                "Sent %s photographed Lucky Emblem state(s) to PopTracker.",
                len(payload.get("kh3_lucky_emblem_global_ids", [])),
            )
