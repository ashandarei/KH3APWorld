"""Formatting, deduplication, and delivery of KH3 client notifications."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..Ingredients import ingredient_location_description


class NotificationSource(Protocol):
    """Context data and file access required by client notifications."""

    bridge_dir: Path | None
    location_id_to_display_name: dict[int, str]
    location_name_to_display_name: dict[str, str]
    slot: int | None
    slot_data: dict[str, Any]

    def slot_concerns_self(self, slot: int) -> bool: ...

    def _load_bridge_payload(self, file_name: str) -> dict[str, Any] | None: ...


@dataclass
class NotificationState:
    """Delivery cursors shared with the public client notification properties."""

    last_bridge_notification_seq: int = 0


class ClientNotificationService:
    """Own client/AP notification formatting, cursors, logging, and delivery."""

    def __init__(
        self,
        source: NotificationSource,
        *,
        present_print_json: Callable[[dict[str, Any]], None],
        load_optional_json: Callable[[Path], dict[str, Any] | None],
        validate_protocol: Callable[[dict[str, Any], str], dict[str, Any]],
        protocol_error: type[Exception],
        log_info: Callable[..., None],
        log_warning: Callable[..., None],
        last_bridge_notification_seq: int = 0,
        poll_json: Callable[[Path], dict[str, Any] | None] | None = None,
        state: NotificationState | None = None,
    ) -> None:
        self.source = source
        self.present_print_json = present_print_json
        self.load_optional_json = load_optional_json
        self.poll_json = poll_json or load_optional_json
        self.validate_protocol = validate_protocol
        self.protocol_error = protocol_error
        self.log_info = log_info
        self.log_warning = log_warning
        self.state = (
            state
            if state is not None
            else NotificationState(last_bridge_notification_seq)
        )

    @property
    def last_bridge_notification_seq(self) -> int:
        return self.state.last_bridge_notification_seq

    @last_bridge_notification_seq.setter
    def last_bridge_notification_seq(self, value: int) -> None:
        self.state.last_bridge_notification_seq = value


    def rebuild_location_display_indexes(self) -> None:
        bridge_data = self.source.slot_data.get("bridge", {}) if isinstance(self.source.slot_data, dict) else {}
        locations = bridge_data.get("locations", [])
        self.source.location_id_to_display_name = {}
        self.source.location_name_to_display_name = {}
        if not isinstance(locations, list):
            return

        for location in locations:
            if not isinstance(location, dict):
                continue
            display_name = self._display_slot_location(location)
            if not display_name:
                continue
            try:
                location_id = int(location.get("ap_location_id"))
            except (TypeError, ValueError):
                location_id = None
            if location_id is not None:
                self.source.location_id_to_display_name[location_id] = display_name
            ap_location_name = str(location.get("ap_location_name") or "").strip()
            if ap_location_name:
                self.source.location_name_to_display_name[ap_location_name.casefold()] = display_name

    @staticmethod
    def _display_slot_location(location: dict[str, Any]) -> str | None:
        ingredient = ingredient_location_description(location)
        if ingredient:
            return ingredient
        description = str(location.get("location_description") or "").strip()
        return description or None

    def display_location_name_from_json_node(self, node: dict[str, Any]) -> str | None:
        node_type = str(node.get("type") or "")
        try:
            player = int(node.get("player", self.source.slot if self.source.slot is not None else 0))
        except (TypeError, ValueError):
            player = self.source.slot
        if player and self.source.slot is not None and not self.source.slot_concerns_self(player):
            return None

        if node_type == "location_id":
            try:
                location_id = int(node.get("text"))
            except (TypeError, ValueError):
                return None
            return self.source.location_id_to_display_name.get(location_id)

        if node_type == "location_name":
            location_name = str(node.get("text") or "").strip()
            if location_name:
                return self.source.location_name_to_display_name.get(location_name.casefold())
        return None

    def translate_location_json_nodes(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.translate_location_json_nodes(entry) for entry in value]
        if not isinstance(value, dict):
            return value

        translated = {key: self.translate_location_json_nodes(child) for key, child in value.items()}
        display_name = self.display_location_name_from_json_node(translated)
        if display_name:
            translated["type"] = "location_name"
            translated["text"] = display_name
        return translated

    def deliver_print_json(self, args: dict[str, Any]) -> None:
        translated_args = copy.deepcopy(args)
        translated_args["data"] = self.translate_location_json_nodes(translated_args.get("data", []))
        self.present_print_json(translated_args)

    def load_bridge_notification_seq(self) -> int:
        bridge_dir = self.source.bridge_dir
        if bridge_dir is None:
            return 0
        notifications_path = bridge_dir / "bridge_notifications.json"
        try:
            payload = self.load_optional_json(notifications_path)
            if payload is None:
                return 0
            self.validate_protocol(payload, str(notifications_path))
        except (self.protocol_error, OSError, ValueError):
            return 0

        highest_seq = self._notification_sequence(payload.get("seq", 0)) or 0
        notifications = payload.get("notifications", [])
        if isinstance(notifications, list):
            for entry in notifications:
                if not isinstance(entry, dict):
                    continue
                sequence = self._notification_sequence(entry.get("seq", 0))
                if sequence is not None:
                    highest_seq = max(highest_seq, sequence)
        return highest_seq

    def mark_existing_bridge_notifications_processed(self) -> None:
        self.last_bridge_notification_seq = max(
            int(self.last_bridge_notification_seq or 0),
            self.load_bridge_notification_seq(),
        )


    def handle_bridge_notifications_file(self) -> None:
        bridge_dir = self.source.bridge_dir
        if bridge_dir is None:
            return
        notifications_path = bridge_dir / "bridge_notifications.json"
        try:
            payload = self.poll_json(notifications_path)
        except ValueError:
            self.log_warning("Ignoring invalid KH3 bridge_notifications.json in %s", bridge_dir)
            return
        if payload is None:
            return
        try:
            self.validate_protocol(payload, str(notifications_path))
        except self.protocol_error as exc:
            self.log_warning("Ignoring incompatible KH3 bridge_notifications.json in %s: %s", bridge_dir, exc)
            return

        notifications = payload.get("notifications", [])
        if not isinstance(notifications, list):
            return
        new_notifications = self._new_bridge_notifications(notifications)
        for _, entry in new_notifications:
            message = str(entry.get("message") or "").strip()
            if message:
                self.log_info(message)
        if new_notifications:
            self.last_bridge_notification_seq = max(
                self.last_bridge_notification_seq,
                max(sequence for sequence, _ in new_notifications),
            )

    def _new_bridge_notifications(self, notifications: list[Any]) -> list[tuple[int, dict[str, Any]]]:
        new_notifications: list[tuple[int, dict[str, Any]]] = []
        for entry in notifications:
            if not isinstance(entry, dict):
                continue
            sequence = self._notification_sequence(entry.get("seq", 0))
            if sequence is None or sequence <= self.last_bridge_notification_seq:
                continue
            new_notifications.append((sequence, entry))
        return sorted(new_notifications, key=lambda item: item[0])

    @staticmethod
    def _notification_sequence(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
