"""Facade and ordered I/O boundary for a KH3 bridge session."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .Bridge import (
    BRIDGE_PROTOCOL_NAME,
    BRIDGE_PROTOCOL_VERSION,
    BridgeProtocolError,
    validate_protocol_payload,
    validate_slot_data_payload,
)
from .bridge_support import runtime_ack as _runtime_ack
from .bridge_support import runtime_notifications as runtime_notifications
from .bridge_support.core_session import CoreSession
from .bridge_support.deathlink import DeathLinkService
from .bridge_support.files import atomic_write_json, load_optional_json_file, safe_unlink
from .bridge_support.location_index import LocationIndex
from .bridge_support.observability import (
    RuntimeGrantProfileRecord,
    append_runtime_item_latency_trace_event,
)
from .bridge_support.store import BridgeStore
from .bridge_support.time import UtcClock, system_utc_now

RUNTIME_GRANT_PROFILE_EVENTS_ENABLED = os.environ.get("KH3_RUNTIME_GRANT_PROFILE_EVENTS_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


class KH3BridgeSession:
    """Coordinate bridge services with explicit startup and lazy seed metadata.

    Entry points call ``initialize()`` to validate the seed before doing work.
    Direct metadata access also initializes on demand; retrieving methods and
    calling pure helpers never loads the seed.
    """

    def __init__(
        self,
        bridge_dir: Path,
        *,
        defer_initialization: bool = True,
        clock: UtcClock = system_utc_now,
    ):
        self.bridge_dir = bridge_dir.expanduser().resolve()
        self.store = BridgeStore(
            self.bridge_dir,
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
        )
        self._clock = clock
        self._core = CoreSession(
            self.store,
            clock=clock,
            validate_protocol_payload=validate_protocol_payload,
            validate_slot_data_payload=validate_slot_data_payload,
            protocol_error=BridgeProtocolError,
        )
        self._deathlink = DeathLinkService(
            self.store,
            clock=clock,
        )
        self.defer_initialization = defer_initialization
        self._initialized = False
        self._initializing = False
        self._slot_data: dict[str, Any] | None = None
        self._location_index = None
        self._location_by_key: dict[str, dict[str, Any]] = {}
        self._location_by_name: dict[str, dict[str, Any]] = {}
        self._location_by_source: dict[tuple[str, str], dict[str, Any]] = {}
        self._runtime_grant_profile_signatures: set[str] = set()
        self._runtime_grant_profile_seq = 0
        if not defer_initialization:
            self.initialize()

    @property
    def slot_data(self) -> dict[str, Any]:
        """Return validated seed metadata, loading it once on first access."""
        self._ensure_initialized()
        assert self._slot_data is not None
        return self._slot_data

    @property
    def _locations(self) -> LocationIndex:
        self._ensure_initialized()
        assert self._location_index is not None
        return self._location_index

    def initialize(self) -> KH3BridgeSession:
        """Load immutable session metadata without publishing delivery state."""
        if self._initialized:
            return self
        if self._initializing:
            return self
        self._initializing = True
        try:
            self._core.initialize()
            self._slot_data = self._core.slot_data
            self._location_index = self._core.locations
            assert self._location_index is not None
            self._location_by_key = self._location_index.by_key
            self._location_by_name = self._location_index.by_name
            self._location_by_source = self._location_index.by_source
            self._initialized = True
        except Exception:
            self._initialized = False
            raise
        finally:
            self._initializing = False
        return self

    def _ensure_initialized(self) -> None:
        if not self._initialized and not self._initializing:
            self.initialize()

    def resolve_location(self, token: str) -> dict[str, Any]:
        return self._locations.resolve_location(token)

    def resolve_source_id(self, source_table: str, source_id: str) -> dict[str, Any]:
        return self._locations.resolve_source_id(source_table, source_id)

    def resolve_ap_location_id(self, ap_location_id: int) -> dict[str, Any]:
        return self._locations.resolve_ap_location_id(ap_location_id)

    def list_locations(self, world_name: str | None = None) -> list[dict[str, Any]]:
        return self._locations.list_locations(world_name)

    @staticmethod
    def display_location_name(location: dict[str, Any], fallback: str = "") -> str:
        return LocationIndex.display_location_name(location, fallback)

    def load_game_state(self) -> dict[str, Any]:
        return self._core.game_state.load()

    def add_checks(self, tokens: list[str]) -> int:
        return self._core.game_state.add_checks(tokens, self.resolve_location)

    def set_goal_complete(self, complete: bool) -> None:
        self._core.game_state.set_goal_complete(complete)

    def mark_goal_complete_once(
        self,
        *,
        reason: str,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._core.game_state.mark_goal_complete_once(
            reason=reason,
            runtime_context=runtime_context,
        )

    def sync_snapshot(self, tokens: list[str], goal_complete: bool) -> bool:
        return self._core.game_state.sync_snapshot(tokens, goal_complete, self.resolve_location)

    def reconcile_snapshot(self, tokens: list[str], goal_complete: bool) -> dict[str, Any]:
        return self._core.game_state.reconcile_snapshot(
            tokens,
            goal_complete,
            self.resolve_location,
        )

    def clear_game_state(self) -> None:
        self._core.game_state.clear()

    def build_status(self) -> dict[str, Any]:
        return self._core.status.build_status(self)

    def load_ap_items_payload(self) -> dict[str, Any]:
        return self._core.status.load_ap_items_payload()

    def _load_ap_items_view(self) -> dict[str, Any]:
        return self._core.status.load_ap_items_view()

    def load_ap_status_payload(self) -> dict[str, Any]:
        return self._core.status.load_ap_status_payload()


    def build_item_delivery_ledger_summary(self) -> dict[str, Any]:
        from .bridge_support.native_delivery import read
        return read(self, "item_delivery_ledger_summary.json") or {"owner": "unavailable"}


    def load_item_delivery_state(self) -> dict[str, Any]:
        from .bridge_support.native_delivery import read
        return read(self, "item_delivery_state.json") or {"owner": "unavailable"}


    def native_reconciliation_state(self):
        from .bridge_support.native_reconciliation import state
        return state(self)

    def sync_native_receipt_catalog(self):
        from .bridge_support.native_reconciliation import publish_catalog
        publish_catalog(self)

    def build_item_delivery_payload(self) -> dict[str, Any]:
        from .bridge_support.native_delivery import read
        payload = read(self, "item_delivery.json")
        if payload is not None:
            return payload
        return {"protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "total_received_item_count": None, "applied_item_indices": [], "last_applied_index": -1,
                "pending_items": [], "native_delivery_ready": False,
                "native_delivery_reason": "awaiting_native_delivery_snapshot"}

    def load_item_consumer_plan(self) -> dict[str, Any]:
        from .bridge_support.native_delivery import require
        return require(self, "item_consumer_plan.json")

    def append_runtime_grant_profile_event(self, event: str, **fields: Any) -> None:
        """Append one deduplicated structured runtime-grant profile event."""
        if not RUNTIME_GRANT_PROFILE_EVENTS_ENABLED:
            return
        record = RuntimeGrantProfileRecord(
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            event=event,
            bridge_dir=str(self.bridge_dir),
            fields=fields,
        )
        signature = record.signature()
        if signature in self._runtime_grant_profile_signatures:
            return
        self._runtime_grant_profile_signatures.add(signature)
        self._runtime_grant_profile_seq += 1
        payload = record.to_payload(
            profile_seq=self._runtime_grant_profile_seq,
            recorded_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        try:
            self.store.append_jsonl("runtime_grant_profile.jsonl", payload)
        except OSError:
            pass

    def append_runtime_item_latency_event(
        self,
        event: str,
        *,
        recorded_at_utc: str | None = None,
        **fields: Any,
    ) -> None:
        append_runtime_item_latency_trace_event(
            self.bridge_dir,
            event,
            recorded_at_utc=recorded_at_utc,
            **fields,
        )

    def queue_deathlink_game_over(self, data: dict[str, Any], *, manual: bool = False) -> dict[str, Any]:
        return self._deathlink.queue(data, manual=manual)

    def deathlink_game_over_transition_status(self) -> dict[str, Any]:
        """Readonly native request/recovery status; gameplay fencing lives in the DLL."""
        state = self._deathlink.native_state() or {}
        return {
            "active": state.get("active") is True,
            "awaiting_apply": state.get("awaiting_apply") is True,
            "recovery_pending": state.get("recovery_pending") is True,
            "reason": state.get("reason", "no_deathlink_game_over_transition"),
            "state": state or None,
        }

    def load_bridge_runtime_status(self) -> dict[str, Any] | None:
        payload = load_optional_json_file(self.bridge_dir / "bridge_runtime_status.json")
        return payload if isinstance(payload, dict) else None


    def runtime_item_apply_event_context_status(self, payload):
        runtime = self.load_bridge_runtime_status() or {}
        seed = self.slot_data.get("kh3_seed") or {}
        native = self.native_reconciliation_state() or {}
        bound = (payload.get("owner") == "dll" and type(payload.get("process_id")) is int
                 and payload["process_id"] > 0 and payload["process_id"] == runtime.get("kh3_process_pid")
                 and payload.get("seed_name") == seed.get("seed_name") and payload.get("slot") == seed.get("slot")
                 and type(payload.get("delivery_epoch")) is int
                 and 0 <= payload["delivery_epoch"] <= native.get("delivery_epoch", -1))
        return {"ready": bound, "reason": "bound_native_ack" if bound else "native_ack_identity_mismatch"}


    def get_next_pending_action(self) -> dict[str, Any] | None:
        from .bridge_support.native_delivery import read
        plan = read(self, "item_consumer_plan.json")
        if plan is None:
            return None
        pending_actions = list(plan.get("pending_actions", []))
        if not pending_actions:
            return None
        return pending_actions[0]

    def list_pending_items(self) -> list[dict[str, Any]]:
        return list(self.build_item_delivery_payload().get("pending_items", []))


    def load_treasure_candidates(self) -> dict[str, Any] | None:
        return load_optional_json_file(self.bridge_dir / "treasure_candidates.json")

    def load_treasure_review_state(self) -> dict[str, Any]:
        payload = load_optional_json_file(self.bridge_dir / "treasure_review_state.json")
        if payload is None:
            return {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "applied_confirmations": [],
            }
        try:
            payload = validate_protocol_payload(payload, str(self.bridge_dir / "treasure_review_state.json"))
        except BridgeProtocolError:
            return {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "applied_confirmations": [],
            }
        return payload

    def save_treasure_review_state(self, payload: dict[str, Any]) -> None:
        payload["protocol_name"] = BRIDGE_PROTOCOL_NAME
        payload["protocol_version"] = BRIDGE_PROTOCOL_VERSION
        atomic_write_json(self.bridge_dir / "treasure_review_state.json", payload)


    def load_bridge_notification_state(self) -> dict[str, Any]:
        return runtime_notifications.load_bridge_notification_state(self.bridge_dir)

    def save_bridge_notification_state(self, payload: dict[str, Any]) -> None:
        runtime_notifications.save_bridge_notification_state(self.bridge_dir, payload)

    def append_bridge_notification(
        self,
        *,
        event: str,
        message: str,
        bridge_key: str | None = None,
        location_name: str | None = None,
        item_name: str | None = None,
        status: str | None = None,
        native_request_id: str | None = None,
    ) -> dict[str, Any]:
        return runtime_notifications.append_bridge_notification(
            self.bridge_dir,
            event=event,
            message=message,
            bridge_key=bridge_key,
            location_name=location_name,
            item_name=item_name,
            status=status,
            native_request_id=native_request_id,
            clock=self._clock,
        )

    def consume_native_notification_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        native = self.native_reconciliation_state()
        if (native is None or payload.get("schema") != "kh3ap.notification.v1"
                or payload.get("owner") != "dll"
                or any(payload.get(key) != native.get(key) for key in ("process_id", "seed_name", "slot"))):
            return {"status": "ignored", "reason": "notification_identity_mismatch"}
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Native notification is missing request_id")
        state = self.load_bridge_notification_state()
        if any(row.get("native_request_id") == request_id for row in state.get("notifications", [])):
            return {"status": "duplicate_event"}
        notification = self.append_bridge_notification(
            event=str(payload.get("event") or "runtime_loot"),
            status=payload.get("status"), message=str(payload.get("message") or ""),
            bridge_key=payload.get("bridge_key"), location_name=payload.get("location_name"),
            item_name=payload.get("item_name"), native_request_id=request_id,
        )
        return {"status": "applied", "notification": notification}


    runtime_item_apply_event_key = staticmethod(_runtime_ack.event_key)

    runtime_item_apply_event_indices = staticmethod(_runtime_ack.event_indices)

    def retire_acknowledged_runtime_item_grant_requests(self, payload: dict[str, Any]) -> list[str]:
        """Retire a matching submitted request after its final native acknowledgement.

        Keep partial requests and unrelated requests pending. Native priority
        requests have a separate owner and are never retired through this path.
        """
        if str(payload.get("status") or "") not in {"applied", "partial"}:
            return []
        if payload.get("request_complete") is False:
            return []
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return []

        retired_files: list[str] = []
        for file_name in ("runtime_item_grant.json",):
            path = self.bridge_dir / file_name
            existing = load_optional_json_file(path)
            if not isinstance(existing, dict) or str(existing.get("request_id") or "") != request_id:
                continue
            try:
                safe_unlink(path)
            except FileNotFoundError:
                continue
            retired_files.append(file_name)

        if retired_files:
            self.append_runtime_grant_profile_event(
                "runtime_item_grant_request_retired_from_apply_event",
                request_id=request_id,
                action_kind=self.infer_runtime_item_apply_action_kind(payload),
                status=payload.get("status"),
                retired_files=retired_files,
            )
        return retired_files

    infer_runtime_item_apply_action_kind = staticmethod(_runtime_ack.action_kind)


    def _append_runtime_item_apply_latency_trace(
        self, payload: dict[str, Any], event_record: dict[str, Any], *, queued_at_utc: str | None
    ) -> None:
        fields = _runtime_ack.latency_record(
            payload,
            event_record,
            queued_at_utc=queued_at_utc,
            processed_at=datetime.now(timezone.utc),
        )
        self.append_runtime_item_latency_event("runtime_apply_ack_processed", **fields)


    def apply_runtime_item_apply_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("schema") != "kh3ap.receipt_ack.v1" or payload.get("receipt_bookkeeping_owner") != "dll":
            raise ValueError("KH3 acknowledgment has not completed native receipt bookkeeping")
        context_status = self.runtime_item_apply_event_context_status(payload)
        if not context_status["ready"]:
            return {"status": "unsafe_context_ignored", "reason": context_status["reason"],
                    "indices": [], "newly_applied_indices": [], "applied_actions": []}
        event_key = payload["event_key"]
        if getattr(self, "_last_receipt_ack_key", None) == event_key:
            return {"status": "duplicate_event", "event_key": event_key,
                    "indices": payload["indices"], "newly_applied_indices": [], "applied_actions": [],
                    "retired_runtime_grant_files": self.retire_acknowledged_runtime_item_grant_requests(payload)}
        result = dict(payload)
        result["retired_runtime_grant_files"] = self.retire_acknowledged_runtime_item_grant_requests(payload)
        self.append_runtime_grant_profile_event(
            "runtime_item_apply_event_processed", request_id=payload.get("request_id"),
            status=payload.get("status"), indices=payload["indices"],
            newly_applied_indices=payload["newly_applied_indices"], pending_item_count=payload["pending_item_count"])
        self._append_runtime_item_apply_latency_trace(payload, result, queued_at_utc=payload.get("queued_at_utc"))
        self._last_receipt_ack_key = event_key
        return result

    def list_treasure_candidate_analyses(self) -> list[dict[str, Any]]:
        payload = self.load_treasure_candidates()
        if payload is None:
            return []
        return list(payload.get("analyses", []))

    def get_treasure_candidate_analysis(self, analysis_key: str) -> dict[str, Any]:
        for analysis in self.list_treasure_candidate_analyses():
            if analysis.get("analysis_key") == analysis_key:
                return analysis
        raise KeyError(f"Unknown KH3 treasure candidate analysis key: {analysis_key}")

    def confirm_treasure_candidate(self, analysis_key: str, bridge_key: str | None = None) -> dict[str, Any]:
        analysis = self.get_treasure_candidate_analysis(analysis_key)
        review_state = self.load_treasure_review_state()
        applied = review_state.get("applied_confirmations", [])
        for entry in applied:
            if entry.get("analysis_key") == analysis_key:
                return {
                    "status": "already_applied",
                    "analysis_key": analysis_key,
                    "bridge_key": entry.get("bridge_key"),
                    "target_file": entry.get("target_file"),
                }

        recommendation = analysis.get("recommendation", {})
        resolved_bridge_key = bridge_key or recommendation.get("top_bridge_key")
        if not resolved_bridge_key:
            raise RuntimeError(f"Analysis {analysis_key} has no candidate bridge key to confirm.")

        self.add_checks([resolved_bridge_key])
        confirmation = {
            "analysis_key": analysis_key,
            "bridge_key": resolved_bridge_key,
            "target_file": analysis.get("target_file"),
            "top_score": recommendation.get("top_score"),
            "score_margin": recommendation.get("score_margin"),
            "auto_confirm_safe": recommendation.get("auto_confirm_safe"),
        }
        applied.append(confirmation)
        review_state["applied_confirmations"] = applied
        self.save_treasure_review_state(review_state)
        return {
            "status": "applied",
            **confirmation,
        }
