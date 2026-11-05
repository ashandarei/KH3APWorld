"""Seed-local DeathLink preferences shared with the native AP Options menu."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..bridge_support.files import atomic_write_json, load_optional_json_file_once

SETTINGS_FILE = "deathlink_settings.json"
AMNESTY_MAX = 20


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = load_optional_json_file_once(path)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def amnesty_value(value: Any) -> int:
    return value if type(value) is int and 0 <= value <= AMNESTY_MAX else 0


def read_settings(bridge_dir: Path, slot_data: Mapping[str, Any]) -> tuple[bool, int]:
    defaults = (bool(slot_data.get("death_link", False)),
                amnesty_value(slot_data.get("death_link_amnesty", 0)))
    payload = read_json(bridge_dir / SETTINGS_FILE)
    if payload.get("schema_version") != 1:
        return defaults
    enabled = payload.get("enabled")
    amnesty = payload.get("amnesty")
    return (
        bool(enabled) if type(enabled) is int and enabled in (0, 1) else defaults[0],
        amnesty if type(amnesty) is int and 0 <= amnesty <= AMNESTY_MAX else defaults[1],
    )


def write_settings(bridge_dir: Path, enabled: bool, amnesty: int) -> None:
    atomic_write_json(bridge_dir / SETTINGS_FILE, {
        "schema_version": 1, "enabled": int(enabled), "amnesty": amnesty_value(amnesty),
    })


def initialize_settings(bridge_dir: Path, slot_data: Mapping[str, Any]) -> tuple[bool, int]:
    settings = read_settings(bridge_dir, slot_data)
    if not (bridge_dir / SETTINGS_FILE).exists():
        write_settings(bridge_dir, *settings)
    return settings
