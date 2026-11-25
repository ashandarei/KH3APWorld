"""Current KH3 wire format and local reconstruction of runtime slot data.

Catalog references cross AP/container boundaries and are persisted in local
seed files. Client services and native consumers use cached expanded records.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from .Bridge import BridgeProtocolError, build_bridge_slot_data, build_conditional_checks, validate_slot_data_payload
from .JsonSnapshot import copy_json
from .Data import HEART_PIECE_GOAL_COUNT, load_extracted_data, normalize_locations

WIRE_VERSION = 1
WIRE_KEY = "location_catalog"
SERVER_SEED_FORMAT_VERSION = 3
SECRET_REPORT_NUMBERS = range(1, 14)
ASSIGNMENT_FIELDS = (
    "assigned_item_id", "assigned_item_name", "assigned_item_player",
    "assigned_item_player_name", "assigned_item_game", "assigned_item_classification",
    "is_foreign_item",
)


def server_slot_data(full: dict[str, Any]) -> dict[str, Any]:
    """Publish only the seed-specific data needed to reconstruct a KH3 slot."""
    bridge = {key: value for key, value in full["bridge"].items() if key != "conditional_checks"}
    result = {**full, "bridge": bridge, WIRE_KEY: _pack_server_catalog(full[WIRE_KEY])}
    seed = full["kh3_seed"]
    if "secret_reports" in seed:
        result["kh3_seed"] = {**seed, "secret_reports": _pack_secret_reports(seed["secret_reports"])}
    tracker = full["universal_tracker"]
    defaults, fingerprint = _tracker_defaults()
    options = tracker["options"]
    if options.keys() != defaults.keys():
        raise BridgeProtocolError("KH3 generation and tracker options have different schemas.")
    result["universal_tracker"] = {
        **tracker,
        "defaults_sha256": fingerprint,
        "randomizer_sha256": _fingerprint(result.pop("kh3_randomizer")),
        "options": {key: value for key, value in options.items() if value != defaults[key]},
    }
    return result


def _pack_server_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    _, indices = _catalog_order()
    # Preserve seed order for pak generation. Overrides address that ordered subset.
    active, changes_by_field, removals_by_field = [], {}, {}
    for offset, (location_id, _, changes, removed) in enumerate(catalog["entries"]):
        active.append(indices[location_id])
        if "mom_song_id" in changes:
            labels = _song_labels()[0].get(changes["mom_song_id"])
            if labels is None or any(changes.get(key) != label for key, label in labels.items()):
                raise BridgeProtocolError("KH3 music chest labels differ from the bundled song catalog.")
            changes = {key: value for key, value in changes.items() if key not in labels}
        for key, value in changes.items():
            groups = changes_by_field.setdefault(key, {})
            groups.setdefault(_canonical(value), (value, []))[1].append(offset)
        for key in removed:
            removals_by_field.setdefault(key, []).append(offset)
    packed = {
        "version": catalog["version"],
        "sha256": catalog["sha256"],
        "active": _index_runs(active),
        "changes": {
            key: [[value, _index_runs(offsets)] for value, offsets in groups.values()]
            for key, groups in changes_by_field.items()
        },
    }
    if removals_by_field:
        packed["removed"] = {key: _index_runs(offsets) for key, offsets in removals_by_field.items()}
    if "mom_song_id" in changes_by_field:
        packed["songs_sha256"] = _song_labels()[1]
    return packed


def _pack_secret_reports(plan: dict[str, Any]) -> list:
    # Each report keeps its rendered lines and AP hint targets in report order.
    return [
        [plan["text"][f"SecretReport{number + 1:02}"],
         [[target["player"], target["location"]] for target in plan["reports"][str(number)]]]
        for number in SECRET_REPORT_NUMBERS
    ]


def _unpack_secret_reports(plan: list) -> dict[str, Any]:
    if len(plan) != len(SECRET_REPORT_NUMBERS):
        raise BridgeProtocolError("Invalid KH3 Secret Report count.")
    text, reports = {}, {}
    for number, report in zip(SECRET_REPORT_NUMBERS, plan):
        if not isinstance(report, list) or len(report) != 2:
            raise BridgeProtocolError("Invalid KH3 Secret Report.")
        lines, targets = report
        if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
            raise BridgeProtocolError("Invalid KH3 Secret Report text.")
        if not isinstance(targets, list) or any(
            not isinstance(target, list) or len(target) != 2
            or type(target[0]) is not int or type(target[1]) is not int for target in targets
        ):
            raise BridgeProtocolError("Invalid KH3 Secret Report targets.")
        text[f"SecretReport{number + 1:02}"] = lines
        reports[str(number)] = [{"player": player, "location": location} for player, location in targets]
    return {"text": text, "reports": reports}


def _index_runs(indices: list[int]) -> list[int | list[int]]:
    runs = []
    for index in indices:
        last = runs[-1] if runs else None
        if isinstance(last, list) and index == last[0] + last[1]:
            last[1] += 1
        elif isinstance(last, int) and index == last + 1:
            runs[-1] = [last, 2]
        else:
            runs.append(index)
    return runs


def _expand_index_runs(runs: Any, limit: int) -> list[int]:
    if not isinstance(runs, (list, tuple)):
        raise BridgeProtocolError("Invalid KH3 catalog index runs.")
    result = []
    for run in runs:
        if type(run) is int:
            start, count = run, 1
        elif isinstance(run, (list, tuple)) and len(run) == 2:
            start, count = run
        else:
            raise BridgeProtocolError("Invalid KH3 catalog index run.")
        if (type(start) is not int or type(count) is not int or count < 1 or start < 0
                or start + count > limit or len(result) + count > limit):
            raise BridgeProtocolError("KH3 catalog index run is out of bounds.")
        result.extend(range(start, start + count))
    return result


def _expand_server_entries(metadata: dict[str, Any]) -> list:
    location_ids = _catalog_order()[0]
    active = _expand_index_runs(metadata["active"], len(location_ids))
    entries = [[location_ids[index], 0, {}, []] for index in active]
    changes, removed = metadata.get("changes"), metadata.get("removed", {})
    if not isinstance(changes, dict) or not isinstance(removed, dict):
        raise BridgeProtocolError("Invalid KH3 catalog overrides.")
    for key, groups in changes.items():
        if not isinstance(key, str) or not isinstance(groups, (list, tuple)) or len(groups) > len(active):
            raise BridgeProtocolError("Invalid KH3 catalog override groups.")
        for group in groups:
            if not isinstance(group, (list, tuple)) or len(group) != 2:
                raise BridgeProtocolError("Invalid KH3 catalog override group.")
            value, runs = group
            for offset in _expand_index_runs(runs, len(active)):
                fields = entries[offset][2]
                if key in fields:
                    raise BridgeProtocolError("Duplicate KH3 catalog override index for one field.")
                fields[key] = value
    for key, runs in removed.items():
        for offset in _expand_index_runs(runs, len(active)):
            entries[offset][3].append(key)
    if "mom_song_id" in changes:
        songs, fingerprint = _song_labels()
        if metadata.get("songs_sha256") != fingerprint:
            raise BridgeProtocolError("KH3 song catalog mismatch. Use the world version that generated this seed.")
        for _, _, fields, _ in entries:
            if "mom_song_id" in fields:
                song_id = fields["mom_song_id"]
                if type(song_id) is not int or song_id not in songs or fields.keys() & songs[song_id].keys():
                    raise BridgeProtocolError("Invalid KH3 music chest song reference.")
                fields.update(songs[song_id])
    return entries


@lru_cache(maxsize=1)
def _song_labels() -> tuple[dict[int, dict[str, str]], str]:
    from .MoMSongCatalog import MOM_SONG_BY_ID

    labels = {
        song_id: {"mom_song_name": song["name"], "mom_series": song["series"]}
        for song_id, song in MOM_SONG_BY_ID.items()
    }
    return labels, _fingerprint(labels)


@lru_cache(maxsize=1)
def _catalog_order() -> tuple[tuple[int, ...], dict[int, int]]:
    location_ids = tuple(sorted(_catalog()[0]))
    return location_ids, {location_id: index for index, location_id in enumerate(location_ids)}


@lru_cache(maxsize=1)
def _tracker_defaults() -> tuple[dict[str, Any], str]:
    from .Options import KH3Options
    from .universal_tracker import KH3_UNIVERSAL_TRACKER_OPTION_NAMES

    defaults = {}
    for name in KH3_UNIVERSAL_TRACKER_OPTION_NAMES:
        kind = KH3Options.type_hints[name]
        value = kind.from_any(kind.default).value
        defaults[name] = sorted(value) if isinstance(value, (set, frozenset)) else value
    return defaults, _fingerprint(defaults)


def _restore_tracker_config(result: dict[str, Any], tracker: dict[str, Any]) -> None:
    from .Options import build_randomizer_generation_config
    from .RaceMode import race_slot_data

    defaults, fingerprint = _tracker_defaults()
    if tracker.pop("defaults_sha256") != fingerprint:
        raise BridgeProtocolError("KH3 tracker option defaults differ. Use the world version that generated this seed.")
    options = tracker.get("options")
    if not isinstance(options, dict) or not options.keys() <= defaults.keys():
        raise BridgeProtocolError("Invalid compact KH3 tracker options.")
    options = tracker["options"] = {**copy_json(defaults), **options}
    try:
        config = build_randomizer_generation_config(SimpleNamespace(**{
            key: SimpleNamespace(value=value) for key, value in options.items()
        }), result.get("mixed_portal_unlocks"))
    except (TypeError, ValueError) as exc:
        raise BridgeProtocolError("Invalid KH3 randomizer options.") from exc
    if result.get("race_mode"):
        config = race_slot_data({"race_mode": True, "kh3_randomizer": config})["kh3_randomizer"]
    if "kh3_randomizer" in result or tracker.pop("randomizer_sha256", None) != _fingerprint(config):
        raise BridgeProtocolError("KH3 randomizer configuration mismatch. Use the world version that generated this seed.")
    result["kh3_randomizer"] = config


def seed_reward_assignment(item_id: int, player: int, flags: int, owner: int,
                           player_name: str, game: str, item_name: str) -> dict[str, Any]:
    return {
        "assigned_item_id": item_id,
        "assigned_item_name": item_name,
        "assigned_item_player": player,
        "assigned_item_player_name": player_name,
        "assigned_item_game": game,
        "assigned_item_classification": flags,
        "is_foreign_item": player != owner,
    }


def attach_seed_rewards(slot_data: dict[str, Any], rewards: dict[int, dict]) -> dict[str, Any]:
    bridge = slot_data["bridge"]
    locations = bridge["locations"]
    fields = set(ASSIGNMENT_FIELDS)
    if rewards.keys() != {row["ap_location_id"] for row in locations} or any(
        reward.keys() != fields for reward in rewards.values()
    ):
        raise BridgeProtocolError("KH3 rewards do not cover the complete seed location catalog.")
    rows = [{**row, **rewards[row["ap_location_id"]]} for row in locations]
    fingerprint = slot_data.get("kh3_seed", {}).get("reward_sha256")
    if fingerprint and seed_reward_fingerprint(rows) != fingerprint:
        raise BridgeProtocolError("The KH3 rewards differ from this room's placements. Use its matching seed pak.")
    return {**slot_data, "bridge": {**bridge, "locations": rows}}


def seed_reward_fingerprint(locations: list[dict]) -> str:
    placements = sorted(
        (row["ap_location_id"], row["assigned_item_id"], row["assigned_item_player"],
         row["assigned_item_classification"])
        for row in locations if "assigned_item_id" in row
    )
    return hashlib.sha256(_canonical(placements).encode("utf-8")).hexdigest()


def needs_seed_rewards(slot_data: dict[str, Any]) -> bool:
    locations = slot_data.get("bridge", {}).get("locations", [])
    return not slot_data.get("race_mode") and any("assigned_item_id" not in row for row in locations)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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
    if "active" in metadata:
        if assignments is not None or entries is not None:
            raise BridgeProtocolError("KH3 slot data contains multiple location catalogs.")
        assignments, entries = [{}], _expand_server_entries(metadata)
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
    result = {key: copy_json(value) for key, value in wire.items() if key != WIRE_KEY}
    tracker = result.get("universal_tracker")
    if isinstance(tracker, dict) and "defaults_sha256" in tracker:
        _restore_tracker_config(result, tracker)
    seed = result.get("kh3_seed")
    if isinstance(seed, dict) and isinstance(seed.get("secret_reports"), list):
        seed["secret_reports"] = _unpack_secret_reports(seed["secret_reports"])
    runtime_bridge = result["bridge"]
    runtime_bridge["locations"] = copy_json(rows)
    if "conditional_checks" not in runtime_bridge:
        if conditional_checks := build_conditional_checks(runtime_bridge["locations"]):
            runtime_bridge["conditional_checks"] = conditional_checks
    validate_slot_data_payload(result)
    return result
