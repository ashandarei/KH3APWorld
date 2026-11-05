"""Build the AP status payload and retain same-seed identity during reconnect."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION, KH3_WORLD_VERSION


@dataclass(frozen=True)
class BridgeStatusInput:
    reported_checks: tuple[int, ...]
    total_locations: int
    received_item_count: int
    seed_name: str | None
    slot: int | None
    team: int | None
    player_name: str | None
    previous_status: Mapping[str, Any]
    connected: bool
    server_address: str | None
    goal_complete: bool
    last_processed_game_state_seq: int
    installed_pak: str
    save_seed: str
    check_reporting_blocked: bool


def build_bridge_status_payload(source: BridgeStatusInput) -> dict[str, Any]:
    slot, team, player_name = source.slot, source.team, source.player_name
    if slot is None and source.seed_name and source.previous_status.get("seed_name") == source.seed_name:
        slot, team = source.previous_status.get("slot"), source.previous_status.get("team")
        player_name = player_name or source.previous_status.get("player_name")
    checks = list(source.reported_checks)
    payload = {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "world_version": KH3_WORLD_VERSION,
        "connected": source.connected,
        "server_address": source.server_address,
        "slot": slot,
        "team": team,
        "player_name": player_name,
        "seed_name": source.seed_name,
        "checked_locations": checks,
        "checked_location_count": len(checks),
        "total_location_count": source.total_locations,
        "received_item_count": source.received_item_count,
        "goal_complete": source.goal_complete,
        "last_processed_game_state_seq": source.last_processed_game_state_seq,
        "installed_pak": source.installed_pak,
        "save_seed": source.save_seed,
        "check_reporting_blocked": source.check_reporting_blocked,
    }
    return payload
