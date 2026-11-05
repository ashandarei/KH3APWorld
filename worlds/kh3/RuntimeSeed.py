"""Small, bounded runtime projections of generation metadata.

The complete generation input belongs in generated-metadata.json. Runtime
documents retain exact gameplay values but not spoiler/hint-generation inputs.
"""
from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

_limits_resource = Path(__file__).with_name("data") / "runtime_json_limits.json"
if not _limits_resource.is_file():
    _limits_resource = importlib.resources.files(__package__).joinpath("data/runtime_json_limits.json")
LIMITS = json.loads(_limits_resource.read_text(encoding="utf-8"))
SETTINGS_FILE = "runtime_seed_settings.json"
HINTS_FILE = "runtime_seed_hints.json"
SEED_FILES = frozenset({"slot_data.json", "kh3_ap_seed_metadata.json", SETTINGS_FILE, HINTS_FILE})


def slot_payload(payload: dict) -> dict:
    result = dict(payload)
    identity = result.get("kh3_seed")
    if isinstance(identity, dict):
        result["kh3_seed"] = {key: value for key, value in identity.items()
                              if key not in {"remote_hint_placements", "all_players"}}
    return result


def hint_reports(metadata: dict) -> dict:
    hints = metadata.get("secret_report_hints", {})
    if not isinstance(hints, dict):
        raise ValueError("Invalid KH3 Secret Report targets")
    reports = hints.get("reports", {})
    if not isinstance(reports, dict):
        raise ValueError("Invalid KH3 Secret Report targets")
    result = {}
    for report, rows in reports.items():
        if report not in {str(n) for n in range(1, 14)} or not isinstance(rows, list):
            raise ValueError("Invalid KH3 Secret Report targets")
        targets = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid KH3 Secret Report target identity")
            player, location = row.get("player"), row.get("location")
            if type(player) is not int or type(location) is not int or player <= 0 or location <= 0:
                raise ValueError("Invalid KH3 Secret Report target identity")
            targets.add((player, location))
        result[report] = [dict(player=player, location=location) for player, location in sorted(targets)]
    return result


def metadata_payload(payload: dict) -> dict:
    result = dict(payload)
    result.pop("placement_summary", None)
    if isinstance(result.get("slot_data"), dict):
        result["slot_data"] = slot_payload(result["slot_data"])
    if isinstance(result.get("foreign_item_manifest"), dict):
        result["foreign_item_manifest"] = {key: value for key, value in result["foreign_item_manifest"].items()
                                           if key != "foreign_items"}
    if "secret_report_hints" in result:
        result["secret_report_hints"] = dict(format_version=1, reports=hint_reports(result))
    return result


def storage_payload(filename: str, payload: dict) -> dict:
    """Persist catalog references; application services use expanded records."""
    if filename not in {"slot_data.json", "kh3_ap_seed_metadata.json"}:
        return payload
    def compact(slot):
        # Settings projections are not AP slot documents.
        if slot.get("protocol_name") != "kh3_ap_file_bridge" or "location_catalog" in slot:
            return slot
        from .SlotData import encode_slot_data
        return encode_slot_data(slot)

    if filename == "slot_data.json":
        return compact(payload)
    if filename == "kh3_ap_seed_metadata.json" and isinstance(payload.get("slot_data"), dict):
        return {**payload, "slot_data": compact(payload["slot_data"])}
    return payload


def expand_document(filename: str, payload):
    """Decode stored seed catalogs once at the read boundary, never on grants."""
    if filename not in {"slot_data.json", "kh3_ap_seed_metadata.json"} or not isinstance(payload, dict):
        return payload
    if filename == "slot_data.json" and "location_catalog" in payload:
        from .SlotData import decode_slot_data
        return decode_slot_data(payload)
    if filename == "kh3_ap_seed_metadata.json":
        slot = payload.get("slot_data")
        if isinstance(slot, dict) and "location_catalog" in slot:
            from .SlotData import decode_slot_data
            return {**payload, "slot_data": decode_slot_data(slot)}
    return payload


def encode_document(filename: str, payload: dict) -> bytes:
    """Use the same UTF-8 byte and nesting bounds as the native readers."""
    payload = storage_payload(filename, payload)
    def depth(value, level=0):
        if isinstance(value, (dict, list, tuple)):
            if level >= LIMITS["document_depth"]:
                raise ValueError(f"KH3 runtime JSON nesting limit: {filename}")
            for child in value.values() if isinstance(value, dict) else value:
                depth(child, level + 1)
    depth(payload)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    limit = LIMITS["hints_bytes"] if filename == HINTS_FILE else LIMITS["seed_bytes"]
    if len(encoded) > limit:
        raise ValueError(f"KH3 runtime JSON size limit: {filename} ({len(encoded)} bytes; maximum {limit})")
    return encoded


def runtime_documents(metadata: dict) -> dict[str, dict]:
    slot_data = slot_payload(metadata.get("slot_data", {}))
    identity = slot_data.get("kh3_seed", {})
    settings = {key: value for key, value in metadata.items() if key in {
        "seed", "slot", "slot_name", "native_randomization",
        "enemy_randomization_initially_enabled", "boss_randomization_initially_enabled",
        "party_randomization_initially_enabled", "party_randomize_on_room_change"}}
    # Settings consumers need options, not the location catalog.
    settings["slot_data"] = {key: value for key, value in slot_data.items()
                             if key not in {"bridge", "universal_tracker", "runtime_reward_bundles"}}
    settings.update(schema="kh3ap.seed_settings.v1", seed_name=identity.get("seed_name", ""))
    hints = dict(schema="kh3ap.seed_hints.v1", seed_name=identity.get("seed_name", ""),
                 slot=metadata.get("slot", identity.get("slot")), reports=hint_reports(metadata))
    documents = {"slot_data.json": slot_data, SETTINGS_FILE: settings, HINTS_FILE: hints}
    for filename, payload in documents.items():
        encode_document(filename, payload)
    return documents
