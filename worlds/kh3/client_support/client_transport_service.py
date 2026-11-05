"""Coordinate KH3 client transport, diagnostics, and bridge payload reads."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..bridge_support.files import file_signature
from ..bridge_support.diagnostic_history import history_for
from .json_snapshot import copy_json, freeze_json

KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE = "location_check_transport.jsonl"
KH3_LOCATION_CHECK_TRANSPORT_STATE_FILE = "location_check_transport_state.json"
logger = logging.getLogger("Client")


@dataclass(frozen=True)
class ConnectionPreparationInput:
    """Current authentication state used before starting a new AP connection."""

    auth: str | None
    inferred_slot_name: str | None


@dataclass(frozen=True)
class ConnectionPreparationResult:
    """Explicit state replacement decisions for a fresh AP connection."""

    auth: str | None
    inferred_slot_name: str | None
    log_inferred_slot_name: bool
    seed_name: None = None
    check_reporting_enabled: bool = False
    clear_locations_checked: bool = True
    clear_locations_scouted: bool = True
    goal_status_sent: bool = False
    last_processed_game_state_seq: int = -1
    last_processed_game_state_file_signature: None = None
    save_seed_mismatch_active: bool = False
    last_save_seed_status_warning: str = ""


@dataclass(frozen=True)
class OutgoingMessageInput:
    """Messages and guard state observed at the CommonContext send boundary."""

    messages: tuple[Any, ...]
    check_reporting_enabled: bool
    goal_status: Any


@dataclass(frozen=True)
class OutgoingMessagePlan:
    """Filtered messages and ordered transport-observation decisions."""

    messages: tuple[Any, ...]
    send_messages: bool = False
    location_check_messages: tuple[Mapping[str, Any], ...] = ()
    suppressed_location_check_messages: tuple[Mapping[str, Any], ...] = ()
    suppressed_location_count: int = 0
    suppressed_goal: bool = False
    record_send_attempt: bool = False
    record_send_complete: bool = False
    record_send_suppressed: bool = False
    log_suppression_warning: bool = False


@dataclass(frozen=True)
class LocationTransportDiagnosticInput:
    """State used to build one location-check transport diagnostic record."""

    event: str
    recorded_at_utc: datetime
    location_id_to_bridge_key: Mapping[int, str]
    last_processed_game_state_seq: int
    check_reporting_enabled: bool
    connected: bool
    server_address: Any
    team: Any
    slot: Any
    location_check_messages: tuple[Mapping[str, Any], ...] | None = None
    locations: Any = None
    reason: str | None = None
    error: str | None = None
    source_cmd: str | None = None


@dataclass(frozen=True)
class LocationTransportEntry:
    """One normalized AP location and its optional KH3 bridge key."""

    location_id: int
    bridge_key: str | None

    def as_payload(self) -> dict[str, Any]:
        """Serialize with the established diagnostic field names."""
        return {
            "location_id": self.location_id,
            "bridge_key": self.bridge_key,
        }


@dataclass(frozen=True)
class LocationTransportDiagnosticResult:
    """Typed state for one location-check transport diagnostic record."""

    protocol_name: str
    protocol_version: int
    event: str
    recorded_at_utc: str
    locations: tuple[LocationTransportEntry, ...]
    last_processed_game_state_seq: int
    check_reporting_enabled: bool
    connected: bool
    server_address: Any
    team: Any
    slot: Any
    reason: str | None = None
    error: str | None = None
    source_cmd: str | None = None

    @property
    def payload(self) -> dict[str, Any]:
        """Serialize the compatibility payload consumed by the facade."""
        location_payloads = [entry.as_payload() for entry in self.locations]
        payload: dict[str, Any] = {
            "protocol_name": self.protocol_name,
            "protocol_version": self.protocol_version,
            "event": self.event,
            "recorded_at_utc": self.recorded_at_utc,
            "locations": location_payloads,
            "location_ids": [entry.location_id for entry in self.locations],
            "bridge_keys": [entry.bridge_key for entry in self.locations if entry.bridge_key],
            "last_processed_game_state_seq": self.last_processed_game_state_seq,
            "check_reporting_enabled": self.check_reporting_enabled,
            "connected": self.connected,
            "server_address": self.server_address,
            "team": self.team,
            "slot": self.slot,
        }
        for key, value in (
            ("reason", self.reason),
            ("error", self.error),
            ("source_cmd", self.source_cmd),
        ):
            if value:
                payload[key] = value
        return payload


class ClientTransportService:
    """Own AP transport ordering, diagnostic writes, and bridge payload parsing."""

    def __init__(
        self,
        *,
        protocol_name: str,
        protocol_version: int,
        payload_reader: Callable[[Path], Any],
        validate_protocol: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
    ) -> None:
        self.protocol_name = protocol_name
        self.protocol_version = protocol_version
        self.payload_reader = payload_reader
        self.validate_protocol = validate_protocol
        self.protocol_error = protocol_error
        self._payload_cache: dict[
            tuple[str, Path],
            tuple[tuple[int, int] | None, dict[str, Any] | None],
        ] = {}

    @staticmethod
    def _payload_signature(path: Path) -> tuple[int, int] | None:
        signature = file_signature(path)
        return None if signature == (None, None) else signature

    def _load_cached_payload(
        self,
        path: Path,
        *,
        kind: str,
        normalize: Callable[[Any], dict[str, Any] | None],
        read_only: bool = False,
    ) -> dict[str, Any] | None:
        signature = self._payload_signature(path)
        cache_key = (kind, path)
        cached = self._payload_cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1] if read_only else copy_json(cached[1])

        try:
            loaded = self.payload_reader(path)
        except ValueError:
            loaded = None
        payload = freeze_json(normalize(loaded))
        # A failed/partial read or a concurrent replacement must be retried,
        # rather than pinning that result to a revision we did not actually read.
        if signature == self._payload_signature(path) and (payload is not None or signature is None):
            self._payload_cache[cache_key] = (signature, payload)
        return payload if read_only else copy_json(payload)

    def clear_payload_cache(self) -> None:
        """Discard cached bridge reads after changing bridge ownership."""
        self._payload_cache.clear()

    async def send_messages(
        self,
        source: OutgoingMessageInput,
        *,
        sender: Callable[[list[Any]], Awaitable[None]],
        record_event: Callable[..., None],
    ) -> None:
        plan = self.plan_outgoing_messages(source)
        if plan.record_send_attempt:
            record_event("location_checks_send_attempt", location_check_msgs=list(plan.location_check_messages))
        if plan.record_send_suppressed:
            record_event(
                "location_checks_send_suppressed",
                location_check_msgs=list(plan.suppressed_location_check_messages),
                reason="check_reporting_not_enabled",
            )
        if plan.log_suppression_warning:
            logger.warning(
                "Suppressed KH3 check reporting before seed guard finished (locations=%s, goal=%s).",
                plan.suppressed_location_count,
                plan.suppressed_goal,
            )
        if not plan.send_messages:
            return
        try:
            await sender(list(plan.messages))
        except Exception as exc:
            if plan.record_send_attempt:
                record_event(
                    "location_checks_send_failed",
                    location_check_msgs=list(plan.location_check_messages),
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise
        if plan.record_send_complete:
            record_event("location_checks_send_complete", location_check_msgs=list(plan.location_check_messages))

    def write_location_transport_diagnostic(self, bridge_dir: Path, source: LocationTransportDiagnosticInput) -> None:
        result = self.build_location_transport_diagnostic(source)
        # The latest diagnostic is the tail of this bounded history. Actual
        # completed checks have their own durable checkpoint.
        history_for(bridge_dir).append(KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE, result.payload)

    @staticmethod
    def plan_connection_preparation(source: ConnectionPreparationInput) -> ConnectionPreparationResult:
        """Plan the exact pre-connect reset and installed-pak auth selection."""
        inferred_slot_name = source.inferred_slot_name
        auth = inferred_slot_name or source.auth
        return ConnectionPreparationResult(
            auth=auth,
            inferred_slot_name=inferred_slot_name,
            log_inferred_slot_name=bool(inferred_slot_name and source.auth != inferred_slot_name),
        )

    @staticmethod
    def plan_outgoing_messages(source: OutgoingMessageInput) -> OutgoingMessagePlan:
        """Observe and filter outgoing AP messages without performing network I/O."""
        location_messages = tuple(
            message
            for message in source.messages
            if isinstance(message, dict) and message.get("cmd") == "LocationChecks"
        )
        if source.check_reporting_enabled:
            return OutgoingMessagePlan(
                messages=source.messages,
                send_messages=True,
                location_check_messages=location_messages,
                record_send_attempt=bool(location_messages),
                record_send_complete=bool(location_messages),
            )

        filtered: list[Any] = []
        suppressed_messages: list[Mapping[str, Any]] = []
        suppressed_location_count = 0
        suppressed_goal = False
        for message in source.messages:
            if not isinstance(message, dict):
                filtered.append(message)
                continue
            command = message.get("cmd")
            if command == "LocationChecks":
                locations = message.get("locations", [])
                suppressed_location_count += len(locations) if isinstance(locations, list) else 0
                suppressed_messages.append(message)
                continue
            if command == "StatusUpdate" and message.get("status") == source.goal_status:
                suppressed_goal = True
                continue
            filtered.append(message)

        should_log = bool(suppressed_location_count or suppressed_goal)
        return OutgoingMessagePlan(
            messages=tuple(filtered),
            send_messages=bool(filtered),
            location_check_messages=location_messages,
            suppressed_location_check_messages=tuple(suppressed_messages),
            suppressed_location_count=suppressed_location_count,
            suppressed_goal=suppressed_goal,
            record_send_suppressed=bool(suppressed_messages and should_log),
            log_suppression_warning=should_log,
        )

    def build_location_transport_diagnostic(
        self,
        source: LocationTransportDiagnosticInput,
    ) -> LocationTransportDiagnosticResult:
        """Normalize one monotonic location-check transport observation."""
        location_entries: tuple[LocationTransportEntry, ...] = ()
        if source.location_check_messages is not None:
            entries: list[LocationTransportEntry] = []
            for message in source.location_check_messages:
                entries.extend(
                    self.parse_locations(
                        message.get("locations", []),
                        source.location_id_to_bridge_key,
                    )
                )
            location_entries = tuple(entries)
        else:
            location_entries = self.parse_locations(source.locations, source.location_id_to_bridge_key)
        return LocationTransportDiagnosticResult(
            protocol_name=self.protocol_name,
            protocol_version=self.protocol_version,
            event=source.event,
            recorded_at_utc=source.recorded_at_utc.isoformat(),
            locations=location_entries,
            last_processed_game_state_seq=source.last_processed_game_state_seq,
            check_reporting_enabled=source.check_reporting_enabled,
            connected=source.connected,
            server_address=source.server_address,
            team=source.team,
            slot=source.slot,
            reason=source.reason,
            error=source.error,
            source_cmd=source.source_cmd,
        )

    @staticmethod
    def parse_locations(
        raw_locations: Any,
        location_id_to_bridge_key: Mapping[int, str],
    ) -> tuple[LocationTransportEntry, ...]:
        """Parse location identifiers into immutable diagnostic entries."""
        if not isinstance(raw_locations, (list, tuple, set)):
            return ()
        entries: list[LocationTransportEntry] = []
        for raw_location in raw_locations:
            try:
                location_id = int(raw_location)
            except (TypeError, ValueError):
                continue
            entries.append(
                LocationTransportEntry(
                    location_id=location_id,
                    bridge_key=location_id_to_bridge_key.get(location_id),
                )
            )
        return tuple(entries)

    @staticmethod
    def normalize_locations(
        raw_locations: Any,
        location_id_to_bridge_key: Mapping[int, str],
    ) -> list[dict[str, Any]]:
        """Return the legacy list-of-dictionaries location representation."""
        return [
            entry.as_payload()
            for entry in ClientTransportService.parse_locations(
                raw_locations,
                location_id_to_bridge_key,
            )
        ]

    def load_protocol_payload(
        self,
        bridge_dir: Path | None,
        file_name: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any] | None:
        """Load and validate an optional protocol-bearing bridge payload."""
        if bridge_dir is None:
            return None
        path = bridge_dir / file_name

        def validate(payload: Any) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            try:
                return self.validate_protocol(payload, str(path))
            except self.protocol_error:
                return None

        return self._load_cached_payload(path, kind="protocol", normalize=validate, read_only=read_only)

    def load_sidecar_payload(
        self,
        bridge_dir: Path | None,
        file_name: str,
        *,
        read_only: bool = False,
    ) -> dict[str, Any] | None:
        """Load an optional unversioned sidecar with the established fallback."""
        if bridge_dir is None:
            return None
        return self._load_cached_payload(
            bridge_dir / file_name,
            kind="sidecar",
            normalize=lambda payload: payload if isinstance(payload, dict) else None,
            read_only=read_only,
        )

__all__: Sequence[str] = (
    "ClientTransportService",
    "ConnectionPreparationInput",
    "ConnectionPreparationResult",
    "KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE",
    "KH3_LOCATION_CHECK_TRANSPORT_STATE_FILE",
    "LocationTransportDiagnosticInput",
    "LocationTransportDiagnosticResult",
    "LocationTransportEntry",
    "OutgoingMessageInput",
    "OutgoingMessagePlan",
)
