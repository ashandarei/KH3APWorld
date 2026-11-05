"""Coordinate KH3 packet effects and completion of a connected session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..Bridge import BridgeProtocolError
from .olympus_coliseum_compat import OlympusColiseumCompatResult
from .received_items_trace import ReceivedItemsTrace


@dataclass(frozen=True)
class ClientPacketCallbacks:
    observe_room: Callable[[Any], None]
    reset_auth_recovery: Callable[[], None]
    apply_connected: Callable[[dict[str, Any]], None]
    disable_checks: Callable[[], None]
    warn: Callable[[str], None]
    disconnect: Callable[[], Awaitable[None]]
    schedule: Callable[[Awaitable[None], str], None]
    bootstrap_pending: Callable[[], bool]
    bootstrap: Callable[[dict[str, Any]], Awaitable[None]]
    companion: Callable[[], OlympusColiseumCompatResult | None]
    prepare_companion: Callable[[dict[str, Any]], Awaitable[None]]
    finish_connected: Callable[[dict[str, Any]], None]
    record_checks: Callable[[str, Any, str], None]
    bridge: Callable[[], Path | None]
    publish: Callable[[], asyncio.Future[None] | None]
    invalidate_items: Callable[[], None]
    mark_receipts_ready: Callable[[], None]
    receive_items: Callable[[Path | None], asyncio.Future[None] | None]


@dataclass(frozen=True)
class ConnectedSetupCallbacks:
    initialize_bridge: Callable[[], None]
    initialize_deathlink: Callable[[], None]
    record_checks: Callable[[Any], None]
    publish: Callable[[], object]
    configure_probe: Callable[[], None]
    warn_pak: Callable[[], None]
    compatible_pak: Callable[[], bool]
    pak_status: Callable[[], str]
    save_guard: Callable[[], bool]
    disable_checks: Callable[[], None]
    activation_pending: Callable[[], bool]
    game_running: Callable[[], bool]
    schedule_activation: Callable[[dict[str, Any]], None]
    save_status: Callable[[], str]
    disconnect_guard: Callable[[str, str], None]
    enroll: Callable[[], None]
    enable_checks: Callable[[], None]
    start_bridge: Callable[[], None]


class ClientPacketService:
    def __init__(self, callbacks: ClientPacketCallbacks) -> None:
        self._client = callbacks

    def handle(self, command: str, args: dict[str, Any]) -> None:
        if command == "RoomInfo":
            self._client.observe_room(args.get("seed_name"))
        elif command == "Connected":
            self._connected(args)
        elif command == "RoomUpdate":
            if "checked_locations" in args:
                self._client.record_checks(
                    "location_checks_server_confirmed", args.get("checked_locations", []), command
                )
            self._publish_if_selected()
        elif command == "DataPackage":
            self._client.invalidate_items()
            self._publish_if_selected()
        elif command == "ReceivedItems":
            self._received_items(args)

    def _connected(self, args: dict[str, Any]) -> None:
        self._client.reset_auth_recovery()
        try:
            self._client.apply_connected(args)
        except BridgeProtocolError as exc:
            self._client.disable_checks()
            self._client.warn(str(exc))
            self._client.schedule(self._client.disconnect(), "KH3IncompatibleSlotData")
            return
        if self._client.bootstrap_pending():
            self._client.schedule(self._client.bootstrap(dict(args)), "KH3ConnectedSeedPreparation")
            return
        companion = self._client.companion()
        if companion is not None and companion.status == "missing":
            self._client.schedule(self._client.prepare_companion(dict(args)), "KH3ConnectedOlympusColiseumPreparation")
            return
        self._client.finish_connected(args)

    def _publish_if_selected(self) -> None:
        if self._client.bridge() is not None:
            self._client.publish()

    def _received_items(self, args: dict[str, Any]) -> None:
        trace = ReceivedItemsTrace(self._client.bridge(), args)
        if args.get("index") == 0:
            # Only the index-zero snapshot makes this connection's receipts
            # authoritative. ItemSyncService owns prefix/metadata validation.
            self._client.mark_receipts_ready()
        bridge_dir = self._client.bridge()
        publication = self._client.receive_items(self._client.bridge())
        trace.follow(publication, bridge_dir=bridge_dir)


class ConnectedSetupService:
    def __init__(self, callbacks: ConnectedSetupCallbacks) -> None:
        self._client = callbacks

    def complete(self, args: dict[str, Any]) -> None:
        self._client.initialize_bridge()
        self._client.initialize_deathlink()
        self._client.record_checks(args.get("checked_locations", []))
        self._client.publish()
        self._client.configure_probe()
        self._client.warn_pak()
        if not self._client.compatible_pak():
            self._client.disconnect_guard("installed_pak_mismatch", self._client.pak_status())
            return
        if not self._client.save_guard():
            self._client.disable_checks()
            if not self._client.activation_pending() and not self._client.game_running():
                self._client.schedule_activation(dict(args))
                return
            self._client.disconnect_guard("save_seed_mismatch", self._client.save_status())
            return
        self._client.enroll()
        self._client.enable_checks()
        # Live context belongs to the DLL and may arrive after server connection.
        self._client.start_bridge()
