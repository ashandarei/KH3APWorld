"""Typed contracts for KH3 bridge payloads and internal decisions.

Persisted payloads remain ordinary dictionaries so existing JSON readers and
writers keep their wire format.  Parsing functions validate known fields,
preserve unknown extension fields, and accept legacy payloads that omit fields
introduced after the original protocol.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, TypedDict, cast


class PayloadValidationError(ValueError):
    """Raised when a bridge file contains an invalid JSON payload shape."""






class SlotKind(str, Enum):
    """Kinds of KH3 save slots used by bridge decisions."""

    AUTO = "auto"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class GrantResult(str, Enum):
    """High-level outcomes of an internal runtime grant decision."""

    APPLIED = "applied"
    PENDING = "pending"
    SKIPPED = "skipped"
    FAILED = "failed"


class ReconciliationOutcome(str, Enum):
    """Whether reconciliation changed persisted bridge state."""

    CHANGED = "changed"
    UNCHANGED = "unchanged"


class GameStatePayload(TypedDict, total=False):
    """Persisted ``game_state.json`` shape, including legacy optional fields."""

    protocol_name: str
    protocol_version: int
    seq: int
    checked_locations: list[str]
    goal_complete: bool
    last_newly_reported_keys: list[str]
    last_newly_reported_at_utc: str | None
    reset_at_utc: str
    last_goal_complete_reason: str
    last_goal_complete_at_utc: str
    last_goal_complete_runtime_context: dict[str, Any]






class RuntimeCurrentSaveSlotPayload(TypedDict, total=False):
    """Persisted runtime current-save-slot observation shape."""

    protocol_name: str
    protocol_version: int
    status: str
    event: str
    save_slot: int | str
    slot_file: str
    observed_at_utc: str
    source: str


class RuntimeGrantResultPayload(TypedDict, total=False):
    """Persisted acknowledgement fields shared by runtime grant results."""

    protocol_name: str
    protocol_version: int
    request_id: str
    status: str
    processed_at: str
    applied_indices: list[int]
    failed_indices: list[int]


class ReconciliationPayload(TypedDict):
    """Public compatibility result returned by snapshot reconciliation."""

    changed: bool
    newly_reported_keys: list[str]
    reported_check_count: int
    observed_check_count: int
    goal_complete: bool






@dataclass(frozen=True)
class SlotDecision:
    """Internal save-slot classification decision."""

    kind: SlotKind
    slot_number: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class GrantDecision:
    """Internal runtime grant outcome."""

    result: GrantResult
    request_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReconciliationDecision:
    """Internal snapshot reconciliation decision with legacy serialization."""

    outcome: ReconciliationOutcome
    newly_reported_keys: tuple[str, ...]
    reported_check_count: int
    observed_check_count: int
    goal_complete: bool

    @property
    def changed(self) -> bool:
        return self.outcome is ReconciliationOutcome.CHANGED

    def to_payload(self) -> ReconciliationPayload:
        """Serialize without adding the internal outcome to the public shape."""
        return {
            "changed": self.changed,
            "newly_reported_keys": list(self.newly_reported_keys),
            "reported_check_count": self.reported_check_count,
            "observed_check_count": self.observed_check_count,
            "goal_complete": self.goal_complete,
        }


def _copy_json_object(payload: Any, source_description: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PayloadValidationError(f"{source_description} is not a JSON object.")
    return dict(payload)


def _validate_protocol_field_types(payload: dict[str, Any], source_description: str) -> None:
    if "protocol_name" in payload and not isinstance(payload["protocol_name"], str):
        raise PayloadValidationError(f"{source_description}.protocol_name must be a string.")
    version = payload.get("protocol_version")
    if "protocol_version" in payload and (not isinstance(version, int) or isinstance(version, bool)):
        raise PayloadValidationError(f"{source_description}.protocol_version must be an integer.")


def _validate_optional_integer(payload: dict[str, Any], field: str, source_description: str) -> None:
    value = payload.get(field)
    if field in payload and (not isinstance(value, int) or isinstance(value, bool)):
        raise PayloadValidationError(f"{source_description}.{field} must be an integer.")


def _validate_optional_boolean(payload: dict[str, Any], field: str, source_description: str) -> None:
    if field in payload and not isinstance(payload[field], bool):
        raise PayloadValidationError(f"{source_description}.{field} must be a boolean.")


def _validate_optional_string_list(payload: dict[str, Any], field: str, source_description: str) -> None:
    if field not in payload:
        return
    value = payload[field]
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise PayloadValidationError(f"{source_description}.{field} must be a list of strings.")


def parse_game_state_payload(payload: Any, source_description: str = "game_state.json") -> GameStatePayload:
    """Validate a legacy or current game-state payload without normalizing it."""
    parsed = _copy_json_object(payload, source_description)
    _validate_protocol_field_types(parsed, source_description)
    _validate_optional_integer(parsed, "seq", source_description)
    _validate_optional_string_list(parsed, "checked_locations", source_description)
    _validate_optional_boolean(parsed, "goal_complete", source_description)
    _validate_optional_string_list(parsed, "last_newly_reported_keys", source_description)
    reported_at = parsed.get("last_newly_reported_at_utc")
    if "last_newly_reported_at_utc" in parsed and reported_at is not None and not isinstance(reported_at, str):
        raise PayloadValidationError(f"{source_description}.last_newly_reported_at_utc must be a string or null.")
    return cast(GameStatePayload, parsed)


def serialize_game_state_payload(payload: GameStatePayload) -> dict[str, Any]:
    """Return an ordinary JSON-ready dictionary without changing its shape."""
    return dict(payload)
