"""Player-facing race data contains checks and settings, never placements."""

from __future__ import annotations

from pathlib import Path

from .JsonSnapshot import copy_json

RACE_ITEM_NAME = "Archipelago Item"


def race_slot_data(slot_data: dict) -> dict:
    """Keep local pak generation and tracking independent of hidden rewards."""
    if not slot_data.get("race_mode"):
        return slot_data
    from .SlotData import ASSIGNMENT_FIELDS

    result = copy_json(slot_data)
    for location in result.get("bridge", {}).get("locations", []):
        for field in ASSIGNMENT_FIELDS:
            location.pop(field, None)
        location["bridge_delivery_required"] = True
    result.get("kh3_seed", {})["remote_hint_placements"] = []
    if "kh3_randomizer" in result:
        result["kh3_randomizer"]["hint"] = {"type": "None", "checks": []}
    options = result.get("universal_tracker", {}).get("options", {})
    if options:
        options["hint_selection_type"] = 0
        options["important_hint_checks"] = []
    return result


def race_export_location(location: dict) -> dict:
    """Reuse native AP placeholders without knowing the real item or recipient."""
    from .SlotData import ASSIGNMENT_FIELDS

    return {
        **{key: value for key, value in location.items() if key not in ASSIGNMENT_FIELDS},
        "assigned_item_id": 0,
        "assigned_item_name": RACE_ITEM_NAME,
        "assigned_item_game": "Archipelago",
        "assigned_item_player": 0,
        "assigned_item_player_name": "",
        "assigned_item_classification": 0,
        "bridge_delivery_required": True,
    }


def validate_race_export(metadata: dict) -> None:
    """Reject an ordinary reward export passed to the race pak generator."""
    summary = metadata.get("placement_summary", {})
    rows = list(summary.get("locations", []))
    rows.extend(metadata.get("foreign_item_manifest", {}).get("foreign_items", []))
    if (summary.get("hint_locations") or any(
        row.get("item_name") != RACE_ITEM_NAME
        or row.get("item_game") != "Archipelago"
        or row.get("recipient_slot") != 0
        or row.get("item_id") != 0
        or row.get("item_display_name") != RACE_ITEM_NAME
        or row.get("recipient_name")
        or row.get("item_classification", 0) != 0
        for row in rows
    )):
        raise ValueError("KH3 race generation requires generic AP rewards without hidden placements.")


def remove_race_spoilers(directory: Path, seed: str) -> None:
    """Do not retain ordinary generated spoilers when reusing an output folder."""
    directory = directory.resolve()
    for name in (f"SpoilerLog-{seed}.json", f"SpoilerLog-{seed}.txt", f"{seed}.generated-metadata.json"):
        path = directory / name
        if path.resolve().parent != directory:
            raise ValueError("KH3 race output name escapes its generation directory.")
        path.unlink(missing_ok=True)
