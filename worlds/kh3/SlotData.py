"""Current KH3 wire format and local reconstruction of runtime slot data.

Catalog references cross AP/container boundaries and are persisted in local
seed files. Client services and native consumers use cached expanded records.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from .Bridge import BridgeProtocolError, build_bridge_slot_data, validate_slot_data_payload
from .JsonSnapshot import copy_json
from .Data import HEART_PIECE_GOAL_COUNT, load_extracted_data, normalize_locations

WIRE_VERSION = 1
WIRE_KEY = "location_catalog"
ASSIGNMENT_FIELDS = (
    "assigned_item_id", "assigned_item_name", "assigned_item_player",
    "assigned_item_player_name", "assigned_item_game", "assigned_item_classification",
    "is_foreign_item",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[int, dict], str]:
    from .ExtraVictoryBonuses import extra_vbonus_locations
    from .MelodyOfMemory import MOM_SONGS, MUSIC_CHEST_PLACEMENTS, music_chest_bonus_locations, placed_music_chest
    from .Space import space_locations
    from .kh02.locations import ALL_KH02_LOCATIONS

    locations = normalize_locations(load_extracted_data())
    from .CharacterUnlocks import ALL_CHARACTER_UNLOCK_LOCATIONS
    locations.extend(ALL_CHARACTER_UNLOCK_LOCATIONS)
    locations.extend(ALL_KH02_LOCATIONS)
    locations.extend(space_locations(True, include_grindy_space_missions=True, include_space_superbosses=True))
    locations.extend(extra_vbonus_locations(locations))
    locations.extend(placed_music_chest(p["placement_id"], MOM_SONGS[0]["song_id"], 0)
                     for p in MUSIC_CHEST_PLACEMENTS)
    # All possible physical chest parents have stable song-bonus identities.
    parents = [dict(row, music_chest_kind="vanilla") for row in locations
               if row["type"] == "treasure" or row.get("music_chest_placement_version")]
    locations.extend(music_chest_bonus_locations(parents))
    rows = build_bridge_slot_data(locations, HEART_PIECE_GOAL_COUNT)["locations"]
    from .Locations import ROCK_TITAN_BONUS_BRIDGE_LOCATION
    rows.append(dict(ROCK_TITAN_BONUS_BRIDGE_LOCATION))
    catalog = {}
    for row in rows:
        # Song selection, difficulty and shop prices/tokens belong to the seed.
        row = {key: value for key, value in row.items()
               if not key.startswith("mom_") and key not in {"shop_price", "shop_item_token", "music_chest_kind"}}
        catalog[row["ap_location_id"]] = row
    if len(catalog) != len(rows):
        raise BridgeProtocolError("Duplicate KH3 location IDs in the bundled catalog.")
    digest = hashlib.sha256(_canonical(catalog).encode("utf-8")).hexdigest()
    return catalog, digest


def encode_slot_data(runtime: dict[str, Any]) -> dict[str, Any]:
    """Replace full runtime rows with catalog references and exact seed deltas."""
    validate_slot_data_payload(runtime)
    if WIRE_KEY in runtime:
        raise BridgeProtocolError("KH3 slot data is already encoded.")
    catalog, digest = _catalog()
    result = copy_json(runtime)
    rows = result["bridge"].pop("locations")
    assignments: list[dict] = []
    assignment_indexes: dict[str, int] = {}
    entries = []
    seen = set()
    for row in rows:
        location_id = row.get("ap_location_id")
        if type(location_id) is not int or location_id not in catalog or location_id in seen:
            raise BridgeProtocolError(f"Invalid or duplicate KH3 catalog location ID: {location_id!r}.")
        seen.add(location_id)
        assignment = {key: row[key] for key in ASSIGNMENT_FIELDS if key in row}
        assignment_key = _canonical(assignment)
        if assignment_key not in assignment_indexes:
            assignment_indexes[assignment_key] = len(assignments)
            assignments.append(assignment)
        base = catalog[location_id]
        changes = {key: value for key, value in row.items()
                   if key not in ASSIGNMENT_FIELDS and (key not in base or value != base[key])}
        removed = sorted(key for key in base if key not in row)
        entries.append([location_id, assignment_indexes[assignment_key], changes, removed])
    result[WIRE_KEY] = {"version": WIRE_VERSION, "sha256": digest,
                        "assignments": assignments, "entries": entries}
    return result


def decode_slot_data(wire: dict[str, Any]) -> dict[str, Any]:
    """Strictly decode the current format; old expanded server payloads fail."""
    if not isinstance(wire, dict):
        raise BridgeProtocolError("KH3 server slot data must be an object.")
    metadata = wire.get(WIRE_KEY)
    if (not isinstance(metadata, dict) or type(metadata.get("version")) is not int
            or metadata["version"] != WIRE_VERSION):
        raise BridgeProtocolError("Unsupported KH3 slot-data format. Generate a new seed with the current KH3 world.")
    catalog, digest = _catalog()
    if metadata.get("sha256") != digest:
        raise BridgeProtocolError("KH3 location catalog mismatch. Install the same KH3 client/world version used to generate this seed.")
    bridge = wire.get("bridge")
    if not isinstance(bridge, dict) or "locations" in bridge:
        raise BridgeProtocolError("KH3 wire slot data must contain catalog references, not expanded locations.")
    assignments, entries = metadata.get("assignments"), metadata.get("entries")
    if not isinstance(assignments, (list, tuple)) or not isinstance(entries, (list, tuple)):
        raise BridgeProtocolError("KH3 slot data is missing catalog assignments or entries.")
    for assignment in assignments:
        if not isinstance(assignment, dict) or not set(assignment).issubset(ASSIGNMENT_FIELDS):
            raise BridgeProtocolError("Invalid KH3 catalog item assignment.")
    rows, seen = [], set()
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            raise BridgeProtocolError("Invalid KH3 catalog entry.")
        location_id, assignment_index, changes, removed = entry
        if type(location_id) is not int or location_id not in catalog or location_id in seen:
            raise BridgeProtocolError(f"Invalid or duplicate KH3 catalog location ID: {location_id!r}.")
        if type(assignment_index) is not int or not 0 <= assignment_index < len(assignments):
            raise BridgeProtocolError("Invalid KH3 catalog assignment index.")
        if (not isinstance(changes, dict) or not isinstance(removed, (list, tuple))
                or any(not isinstance(key, str) or key not in catalog[location_id] for key in removed)
                or "ap_location_id" in changes or "ap_location_id" in removed
                or set(changes).intersection(ASSIGNMENT_FIELDS)):
            raise BridgeProtocolError("Invalid KH3 catalog location overrides.")
        seen.add(location_id)
        row = {key: value for key, value in catalog[location_id].items() if key not in removed}
        row.update(changes)
        row.update(assignments[assignment_index])
        rows.append(row)
    result = copy_json(wire)
    del result[WIRE_KEY]
    result["bridge"]["locations"] = copy_json(rows)
    validate_slot_data_payload(result)
    return result
