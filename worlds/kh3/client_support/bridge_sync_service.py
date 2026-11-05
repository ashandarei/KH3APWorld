"""Synchronize AP client state with the KH3 filesystem bridge."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..bridge_support.files import bridge_exists, file_signature, safe_unlink
from .json_snapshot import freeze_json


@dataclass(frozen=True)
class BridgeInitializationInput:
    """Explicit state required to initialize an online bridge directory."""

    slot_data: dict[str, Any]
    bridge_root: Path
    seed_name: str | None
    player_name: str
    previous_bridge_dir: Path | None
    checked_locations: set[int]
    locations_checked: set[int]
    finished_game: bool
    bridge_dir_name: str = ""


@dataclass(frozen=True)
class BridgeInitializationResult:
    """Bridge indexes and preserved observations produced by initialization."""

    bridge_dir: Path
    location_key_to_id: dict[str, int]
    location_id_to_bridge_key: dict[int, str]
    locations_checked: set[int]
    has_unreported_observations: bool
    reused_existing_state: bool = False


@dataclass(frozen=True)
class ObservationResetInput:
    """State used to replace bridge observation files safely."""

    bridge_dir: Path
    reason: str
    checked_locations: set[int]
    locations_checked: set[int]
    location_id_to_bridge_key: dict[int, str]
    goal_complete: bool = False
    mark_notifications: bool = True


@dataclass(frozen=True)
class GameStateSyncInput:
    """AP and bridge state needed for one game-state observation."""

    bridge_dir: Path
    location_key_to_id: dict[str, int]
    check_reporting_enabled: bool


@dataclass(frozen=True)
class GameStateSyncResult:
    """Observable result of one game-state synchronization attempt."""

    processed: bool = False
    location_ids: frozenset[int] = frozenset()
    goal_reported: bool = False


@dataclass
class BridgeObservationState:
    """Observation cursors shared by the AP facade and bridge service."""

    last_sequence: int = -1
    last_file_signature: tuple[int, int] | None = None
    goal_reported: bool = False


class BridgeSyncService:
    """Own bridge initialization, observation transport, and AP publication."""

    def __init__(
        self,
        *,
        protocol_name: str,
        protocol_version: int,
        load_optional_json: Callable[[Path], dict[str, Any] | None],
        validate_protocol: Callable[[dict[str, Any], str], dict[str, Any]],
        validate_slot_data: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
        atomic_write_json: Callable[[Path, dict[str, Any]], None],
        safe_name: Callable[[str], str],
        configure_profiler: Callable[[Path], None],
        mark_existing_notifications: Callable[[], None],
        installed_pak_compatible: Callable[[], bool],
        build_installed_pak_status: Callable[[], str],
        refresh_save_seed_guard: Callable[[], bool],
        build_save_seed_status: Callable[[], str],
        record_locations: Callable[[set[int]], None],
        send_msgs: Callable[[list[Any]], Awaitable[None]],
        publish_bridge_files: Callable[[], Any],
        profile_scope: Callable[[str], AbstractContextManager[Any]],
        log_info: Callable[..., None],
        log_warning: Callable[..., None],
        goal_status: int,
        initial_sequence: int = -1,
        initial_file_signature: tuple[int, int] | None = None,
        initial_goal_reported: bool = False,
        poll_json: Callable[[Path], dict[str, Any] | None] | None = None,
        state: BridgeObservationState | None = None,
    ) -> None:
        self.protocol_name = protocol_name
        self.protocol_version = protocol_version
        self.load_optional_json = load_optional_json
        self.poll_json = poll_json or load_optional_json
        self.validate_protocol = validate_protocol
        self.validate_slot_data = validate_slot_data
        self.protocol_error = protocol_error
        self.atomic_write_json = atomic_write_json
        self.safe_name = safe_name
        self.configure_profiler = configure_profiler
        self.mark_existing_notifications = mark_existing_notifications
        self.installed_pak_compatible = installed_pak_compatible
        self.build_installed_pak_status = build_installed_pak_status
        self.refresh_save_seed_guard = refresh_save_seed_guard
        self.build_save_seed_status = build_save_seed_status
        self.record_locations = record_locations
        self.send_msgs = send_msgs
        self.publish_bridge_files = publish_bridge_files
        self.profile_scope = profile_scope
        self.log_info = log_info
        self.log_warning = log_warning
        self.goal_status = goal_status
        self.state = (
            state
            if state is not None
            else BridgeObservationState(initial_sequence, initial_file_signature, initial_goal_reported)
        )
        self._publication_pending = False
        self._observation_revision = 0
        self._published_payloads: dict[Path, tuple[Any, dict[str, Any]]] = {}

    @property
    def last_sequence(self) -> int:
        return self.state.last_sequence

    @last_sequence.setter
    def last_sequence(self, value: int) -> None:
        self.state.last_sequence = value

    @property
    def last_file_signature(self) -> tuple[int, int] | None:
        return self.state.last_file_signature

    @last_file_signature.setter
    def last_file_signature(self, value: tuple[int, int] | None) -> None:
        self.state.last_file_signature = value

    @property
    def goal_reported(self) -> bool:
        return self.state.goal_reported

    @goal_reported.setter
    def goal_reported(self, value: bool) -> None:
        self.state.goal_reported = value

    def invalidate_pending_observation(self) -> None:
        """Prevent an awaited publication from finishing a replaced observation."""
        self._observation_revision += 1
        self._publication_pending = False

    def initialize(self, source: BridgeInitializationInput) -> BridgeInitializationResult:
        """Initialize an online bridge and preserve monotonic observations."""
        self.validate_slot_data(source.slot_data, "Connected KH3 slot_data")
        self.invalidate_pending_observation()
        bridge_data = source.slot_data.get("bridge", {})
        if not bridge_data:
            raise RuntimeError("Connected KH3 slot is missing bridge metadata.")

        requested_bridge_name = str(source.bridge_dir_name or "").strip()
        if requested_bridge_name and Path(requested_bridge_name).name != requested_bridge_name:
            raise RuntimeError("Managed KH3 bridge directory name is invalid.")
        slot_name = requested_bridge_name or self.safe_name(f"{source.seed_name}_{source.player_name}")
        bridge_dir = source.bridge_root / slot_name
        bridge_dir.mkdir(parents=True, exist_ok=True)
        self.configure_profiler(bridge_dir)
        key_to_id = {location["bridge_key"]: location["ap_location_id"] for location in bridge_data["locations"]}
        id_to_key = {location["ap_location_id"]: location["bridge_key"] for location in bridge_data["locations"]}
        preserved_keys, preserved_goal = self.load_observation_snapshot(bridge_dir, key_to_id)
        preserved_ids = {key_to_id[key] for key in preserved_keys}
        unreported_ids = preserved_ids - source.checked_locations
        locations_checked = source.locations_checked | preserved_ids
        reused_existing_state = self._can_reuse_existing_state(
            source,
            bridge_dir,
            preserved_keys=preserved_keys,
            location_id_to_bridge_key=id_to_key,
        )
        if reused_existing_state:
            self.last_sequence = -1
            self.last_file_signature = None
            self.log_info("Reusing compatible KH3 bridge observation and reconciliation state in %s.", bridge_dir)
        else:
            self.reset_observation_state(
                ObservationResetInput(
                    bridge_dir=bridge_dir,
                    reason="connected_bridge_initialized",
                    checked_locations=source.checked_locations,
                    locations_checked=locations_checked,
                    location_id_to_bridge_key=id_to_key,
                    goal_complete=preserved_goal,
                    mark_notifications=False,
                )
            )
        has_unreported = bool(unreported_ids or (preserved_goal and not source.finished_game))
        if has_unreported:
            self.last_sequence = -1
            self.last_file_signature = None
        return BridgeInitializationResult(
            bridge_dir=bridge_dir,
            location_key_to_id=key_to_id,
            location_id_to_bridge_key=id_to_key,
            locations_checked=locations_checked,
            has_unreported_observations=has_unreported,
            reused_existing_state=reused_existing_state,
        )

    def _can_reuse_existing_state(
        self,
        source: BridgeInitializationInput,
        bridge_dir: Path,
        *,
        preserved_keys: set[str],
        location_id_to_bridge_key: dict[int, str],
    ) -> bool:
        if source.previous_bridge_dir is None:
            return False
        try:
            if source.previous_bridge_dir.expanduser().resolve() != bridge_dir.expanduser().resolve():
                return False
        except OSError:
            if source.previous_bridge_dir != bridge_dir:
                return False

        existing_slot_data = self.load_optional_json(bridge_dir / "slot_data.json")
        if existing_slot_data != source.slot_data:
            return False

        game_state_path = bridge_dir / "game_state.json"
        try:
            game_state = self.load_optional_json(game_state_path)
            if game_state is None:
                return False
            self.validate_protocol(game_state, str(game_state_path))
        except (self.protocol_error, OSError, ValueError):
            return False

        locally_reported_keys = {
            bridge_key
            for location_id in source.locations_checked
            if (bridge_key := location_id_to_bridge_key.get(location_id))
        }
        return locally_reported_keys.issubset(preserved_keys)

    def publish(
        self,
        bridge_dir: Path,
        *,
        slot_data: dict[str, Any],
        status_payload: dict[str, Any],
        items_payload: dict[str, Any],
    ) -> None:
        """Publish the three AP-owned bridge files in established order."""
        self._publish_json_if_changed(bridge_dir / "slot_data.json", slot_data)
        self._publish_json_if_changed(bridge_dir / "ap_status.json", status_payload)
        self._publish_json_if_changed(bridge_dir / "ap_items.json", items_payload)

    def _publish_json_if_changed(self, path: Path, payload: dict[str, Any]) -> bool:
        signature = file_signature(path)
        cached = self._published_payloads.get(path)
        if (
            cached is not None
            and signature != (None, None)
            and cached[0] == signature
            and (cached[1] is payload or cached[1] == payload)
        ):
            return False
        # A current cached publication already proves the old contents; avoid
        # reparsing a multi-megabyte history just to compare it a second time.
        if (cached is None or cached[0] != signature) and self.load_optional_json(path) == payload:
            self._published_payloads[path] = (signature, freeze_json(payload))
            return False
        self.atomic_write_json(path, payload)
        self._published_payloads[path] = (file_signature(path), freeze_json(payload))
        return True

    def reset_observation_state(self, source: ObservationResetInput) -> None:
        """Reset observation state while preserving only locally reported checks."""
        self.invalidate_pending_observation()
        checked_keys = sorted(
            {
                bridge_key
                for location_id in source.locations_checked
                if (bridge_key := source.location_id_to_bridge_key.get(location_id))
            }
        )
        self.atomic_write_json(
            source.bridge_dir / "game_state.json",
            {
                "protocol_name": self.protocol_name,
                "protocol_version": self.protocol_version,
                "seq": 0,
                "checked_locations": checked_keys,
                "goal_complete": bool(source.goal_complete),
                "reset_reason": source.reason,
                "reset_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        self._delete_stale_observations(source.bridge_dir)
        self.last_sequence = -1 if checked_keys or source.goal_complete else 0
        self.last_file_signature = None
        if source.mark_notifications:
            self.mark_existing_notifications()

    def _delete_stale_observations(self, bridge_dir: Path) -> None:
        for file_name in ("runtime_loot_event.json",):
            try:
                safe_unlink(bridge_dir / file_name)
            except OSError as exc:
                self.log_info("Could not remove stale KH3 runtime event file %s: %s", file_name, exc)

    def load_observation_snapshot(
        self,
        bridge_dir: Path,
        location_key_to_id: dict[str, int],
    ) -> tuple[set[str], bool]:
        """Load valid preserved checks and goal state from a bridge."""
        game_state_path = bridge_dir / "game_state.json"
        try:
            payload = self.load_optional_json(game_state_path)
            if payload is None:
                return set(), False
            self.validate_protocol(payload, str(game_state_path))
        except (self.protocol_error, OSError, ValueError):
            return set(), False
        checked_keys = {
            key for key in payload.get("checked_locations", []) if isinstance(key, str) and key in location_key_to_id
        }
        return checked_keys, bool(payload.get("goal_complete", False))

    def ensure_observation_state(self, source: ObservationResetInput) -> None:
        """Retain a valid observation file or create a fresh reset payload."""
        game_state_path = source.bridge_dir / "game_state.json"
        if not bridge_exists(game_state_path):
            self.reset_observation_state(source)
            return
        try:
            payload = self.load_optional_json(game_state_path)
            if payload is not None:
                self.validate_protocol(payload, str(game_state_path))
                self.last_sequence = -1
                return
        except (self.protocol_error, ValueError):
            pass
        self.reset_observation_state(source)

    async def synchronize_game_state(self, source: GameStateSyncInput) -> GameStateSyncResult:
        """Transport a newer bridge observation to AP exactly once."""
        revision = self._observation_revision
        loaded = self._load_changed_game_state(source.bridge_dir)
        if loaded is None:
            return GameStateSyncResult()
        payload, sequence, signature = loaded
        if sequence <= self.last_sequence:
            if self._publication_pending:
                with self.profile_scope("client:game_state:retry_write_bridge_files"):
                    await self._publish_game_state_progress(sequence, signature)
                return GameStateSyncResult(processed=True)
            self.last_file_signature = signature
            return GameStateSyncResult()
        if not source.check_reporting_enabled:
            return GameStateSyncResult()
        if not await self._guards_allow_checks(sequence, signature):
            return GameStateSyncResult(processed=True)
        if revision != self._observation_revision:
            return GameStateSyncResult()

        location_ids = self._location_ids(
            payload,
            source.location_key_to_id,
        )
        if location_ids:
            self.record_locations(location_ids)
            with self.profile_scope("client:game_state:send_location_checks"):
                await self.send_msgs([{"cmd": "LocationChecks", "locations": sorted(location_ids)}])
            if revision != self._observation_revision:
                return GameStateSyncResult()
        goal_reported = False
        if payload.get("goal_complete") and not self.goal_reported:
            with self.profile_scope("client:game_state:send_goal"):
                await self.send_msgs([{"cmd": "StatusUpdate", "status": self.goal_status}])
            if revision != self._observation_revision:
                return GameStateSyncResult()
            self.goal_reported = True
            goal_reported = True
        with self.profile_scope("client:game_state:write_bridge_files"):
            await self._publish_game_state_progress(sequence, signature)
        return GameStateSyncResult(True, frozenset(location_ids), goal_reported)

    async def _publish_game_state_progress(self, sequence: int, signature: tuple[int, int]) -> None:
        revision = self._observation_revision
        self.last_sequence = max(self.last_sequence, sequence)
        self._publication_pending = True
        publication = self.publish_bridge_files()
        if inspect.isawaitable(publication):
            await asyncio.shield(publication)
        if revision != self._observation_revision:
            return
        self.last_file_signature = signature
        self._publication_pending = False

    def _load_changed_game_state(
        self,
        bridge_dir: Path,
    ) -> tuple[dict[str, Any], int, tuple[int, int]] | None:
        path = bridge_dir / "game_state.json"
        signature = file_signature(path)
        if signature == (None, None):
            return None
        if signature == self.last_file_signature:
            return None
        with self.profile_scope("client:game_state:load_validate"):
            try:
                payload = self.poll_json(path)
            except ValueError:
                self.log_warning("Ignoring invalid KH3 game_state.json in %s", bridge_dir)
                return None
            if payload is None:
                return None
            try:
                self.validate_protocol(payload, str(path))
            except self.protocol_error as exc:
                self.log_warning("Ignoring incompatible KH3 game_state.json in %s: %s", bridge_dir, exc)
                return None
        return payload, int(payload.get("seq", 0)), signature

    async def _guards_allow_checks(self, sequence: int, signature: tuple[int, int]) -> bool:
        with self.profile_scope("client:game_state:installed_pak_guard"):
            installed_pak_compatible = self.installed_pak_compatible()
        if not installed_pak_compatible:
            self.log_warning(
                "Suppressing KH3 location checks because the installed AP pak does not match this room: %s",
                self.build_installed_pak_status(),
            )
            await self._finish_suppressed(sequence, signature)
            return False
        with self.profile_scope("client:game_state:save_seed_guard"):
            save_seed_ready = self.refresh_save_seed_guard()
        if not save_seed_ready:
            self.log_warning(
                "Suppressing KH3 location checks because active KH3 saves do not match this room: %s",
                self.build_save_seed_status(),
            )
            await self._finish_suppressed(sequence, signature)
            return False
        return True

    async def _finish_suppressed(self, sequence: int, signature: tuple[int, int]) -> None:
        await self._publish_game_state_progress(sequence, signature)

    def _location_ids(
        self,
        payload: dict[str, Any],
        location_key_to_id: dict[str, int],
    ) -> set[int]:
        location_ids: set[int] = set()
        for bridge_key in payload.get("checked_locations", []):
            if bridge_key in location_key_to_id:
                location_ids.add(location_key_to_id[bridge_key])
            if bridge_key not in location_key_to_id:
                self.log_warning("Ignoring unknown KH3 bridge check key: %s", bridge_key)
        return location_ids
