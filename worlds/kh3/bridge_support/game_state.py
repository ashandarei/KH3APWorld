"""Persistent game-state transitions for a KH3 bridge session."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import (
    PayloadValidationError,
    ReconciliationDecision,
    ReconciliationOutcome,
    parse_game_state_payload,
)
from .paths import GAME_STATE_FILE
from .store import BridgeStore
from .time import UtcClock, utc_now_text


class GameStateService:
    """Own ``game_state.json`` loading, persistence, and reconciliation."""

    def __init__(
        self,
        store: BridgeStore,
        *,
        clock: UtcClock,
        validate_protocol_payload: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
    ) -> None:
        self.store = store
        self.clock = clock
        self.validate_protocol_payload = validate_protocol_payload
        self.protocol_error = protocol_error

    def _default_payload(self) -> dict[str, Any]:
        return {
            **self.store.protocol_metadata(),
            "seq": 0,
            "checked_locations": [],
            "goal_complete": False,
            "last_newly_reported_keys": [],
            "last_newly_reported_at_utc": None,
        }

    def load(self) -> dict[str, Any]:
        path = self.store.path(GAME_STATE_FILE)
        payload = self.store.read_optional_json(GAME_STATE_FILE)
        if payload is None:
            return self._default_payload()
        try:
            payload = self.validate_protocol_payload(payload, str(path))
            payload = parse_game_state_payload(payload, str(path))
        except (self.protocol_error, PayloadValidationError):
            return self._default_payload()
        payload.setdefault("last_newly_reported_keys", [])
        payload.setdefault("last_newly_reported_at_utc", None)
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        payload.update(self.store.protocol_metadata())
        self.store.write_json(GAME_STATE_FILE, payload)

    def add_checks(self, tokens: list[str], resolve: Callable[[str], dict[str, Any]]) -> int:
        game_state = self.load()
        checked = set(game_state.get("checked_locations", []))
        newly_reported_keys: list[str] = []
        for token in tokens:
            bridge_key = resolve(token)["bridge_key"]
            if bridge_key not in checked:
                checked.add(bridge_key)
                newly_reported_keys.append(bridge_key)
        game_state["seq"] = int(game_state.get("seq", 0)) + 1
        game_state["checked_locations"] = sorted(checked)
        if newly_reported_keys:
            game_state["last_newly_reported_keys"] = sorted(newly_reported_keys)
            game_state["last_newly_reported_at_utc"] = utc_now_text(self.clock)
        self.save(game_state)
        return len(newly_reported_keys)

    def set_goal_complete(self, complete: bool) -> None:
        game_state = self.load()
        game_state["seq"] = int(game_state.get("seq", 0)) + 1
        game_state["goal_complete"] = complete
        self.save(game_state)

    def mark_goal_complete_once(
        self,
        *,
        reason: str,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        game_state = self.load()
        if bool(game_state.get("goal_complete", False)):
            return {"status": "already_complete", "seq": int(game_state.get("seq", 0)), "reason": reason}
        game_state["seq"] = int(game_state.get("seq", 0)) + 1
        game_state["goal_complete"] = True
        game_state["last_goal_complete_reason"] = reason
        game_state["last_goal_complete_at_utc"] = utc_now_text(self.clock)
        if isinstance(runtime_context, dict):
            game_state["last_goal_complete_runtime_context"] = runtime_context
        self.save(game_state)
        return {"status": "marked_complete", "seq": int(game_state.get("seq", 0)), "reason": reason}

    def sync_snapshot(
        self,
        tokens: list[str],
        goal_complete: bool,
        resolve: Callable[[str], dict[str, Any]],
    ) -> bool:
        resolved_keys = sorted({resolve(token)["bridge_key"] for token in tokens})
        game_state = self.load()
        current_keys = sorted(game_state.get("checked_locations", []))
        current_goal = bool(game_state.get("goal_complete", False))
        merged_goal = current_goal or goal_complete
        if current_keys == resolved_keys and current_goal == merged_goal:
            return False
        game_state["seq"] = int(game_state.get("seq", 0)) + 1
        game_state["checked_locations"] = resolved_keys
        game_state["goal_complete"] = merged_goal
        self.save(game_state)
        return True

    def reconcile_snapshot(
        self,
        tokens: list[str],
        goal_complete: bool,
        resolve: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        observed_keys = {resolve(token)["bridge_key"] for token in tokens}
        game_state = self.load()
        current_keys = set(game_state.get("checked_locations", []))
        current_goal = bool(game_state.get("goal_complete", False))
        merged_keys = current_keys | observed_keys
        merged_goal = current_goal or goal_complete
        newly_reported = sorted(observed_keys - current_keys)
        changed = merged_keys != current_keys or merged_goal != current_goal
        if changed:
            game_state["seq"] = int(game_state.get("seq", 0)) + 1
            game_state["checked_locations"] = sorted(merged_keys)
            game_state["goal_complete"] = merged_goal
            if newly_reported:
                game_state["last_newly_reported_keys"] = list(newly_reported)
                game_state["last_newly_reported_at_utc"] = utc_now_text(self.clock)
            self.save(game_state)
        return ReconciliationDecision(
            outcome=ReconciliationOutcome.CHANGED if changed else ReconciliationOutcome.UNCHANGED,
            newly_reported_keys=tuple(newly_reported),
            reported_check_count=len(merged_keys),
            observed_check_count=len(observed_keys),
            goal_complete=merged_goal,
        ).to_payload()

    def clear(self) -> None:
        self.save(
            {
                **self.store.protocol_metadata(),
                "seq": 0,
                "checked_locations": [],
                "goal_complete": False,
                "reset_at_utc": utc_now_text(self.clock),
            }
        )
