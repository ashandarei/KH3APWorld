"""Client notification history; in-game presentation is owned by the DLL."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION, BridgeProtocolError, validate_protocol_payload
from .files import atomic_write_json, load_optional_json_file
from .paths import BRIDGE_NOTIFICATIONS_FILE, bridge_sidecar_path
from .time import UtcClock, system_utc_now, utc_now_text


def load_bridge_notification_state(bridge_dir: Path) -> dict[str, Any]:
    path = bridge_sidecar_path(bridge_dir, BRIDGE_NOTIFICATIONS_FILE)
    payload = load_optional_json_file(path)
    if payload is None:
        return {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "seq": 0,
            "notifications": [],
        }
    try:
        payload = validate_protocol_payload(payload, str(path))
    except BridgeProtocolError:
        return {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "seq": 0,
            "notifications": [],
        }
    payload.setdefault("seq", 0)
    payload.setdefault("notifications", [])
    return payload


def save_bridge_notification_state(bridge_dir: Path, payload: dict[str, Any]) -> None:
    payload["protocol_name"] = BRIDGE_PROTOCOL_NAME
    payload["protocol_version"] = BRIDGE_PROTOCOL_VERSION
    atomic_write_json(bridge_sidecar_path(bridge_dir, BRIDGE_NOTIFICATIONS_FILE), payload)


def append_bridge_notification(
    bridge_dir: Path,
    *,
    event: str,
    message: str,
    bridge_key: str | None = None,
    location_name: str | None = None,
    item_name: str | None = None,
    status: str | None = None,
    native_request_id: str | None = None,
    clock: UtcClock = system_utc_now,
) -> dict[str, Any]:
    payload = load_bridge_notification_state(bridge_dir)
    seq = int(payload.get("seq", 0)) + 1
    notifications = list(payload.get("notifications", []))
    notifications.append({
        "seq": seq,
        "native_request_id": native_request_id,
        "event": event,
        "status": status,
        "message": message,
        "bridge_key": bridge_key,
        "location_name": location_name,
        "item_name": item_name,
        "created_at_utc": utc_now_text(clock),
    })
    payload["seq"] = seq
    payload["notifications"] = notifications[-100:]
    save_bridge_notification_state(bridge_dir, payload)
    return notifications[-1]
