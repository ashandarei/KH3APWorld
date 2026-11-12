from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from .ap_archive import (
        ArchiveValidationError,
        get_slot_game,
        get_slot_name,
        load_multidata,
        require_bridge_locations,
        resolve_kh3_slot,
    )
except ImportError:
    from ap_archive import (
        ArchiveValidationError,
        get_slot_game,
        get_slot_name,
        load_multidata,
        require_bridge_locations,
        resolve_kh3_slot,
    )

SCRIPT_PATH = Path(__file__).resolve()
KH3_WORLD_ROOT = SCRIPT_PATH.parent
ARCH_ROOT = SCRIPT_PATH.parents[2]
ARCHIPELAGO_RUNTIME_ROOT = ARCH_ROOT
EXTRACTED_DATA_PATH = KH3_WORLD_ROOT / "data" / "extracted_locations.json"

BRIDGE_ONLY_LOCATION_TYPES = frozenset({
    "space", "classic_kingdom", "olympus_coliseum", "ingredient", "adversary",
    "melody_of_memory", "kh02_chest", "kh02_boss", "kh02_objective", "music_chest_bonus", "vbonus_extra",
    "minigame_reward", "sequence_break_bonus", "character_unlock",
})

try:
    from .Data import (
        TRAP_RUNTIME_TOKEN_BY_ITEM_NAME,
        CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME,
        CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME,
        DEFAULT_BASE_ABILITY_CHRINIT_FIELDS,
        FILLER_ITEM_RUNTIME_TOKENS,
        FIXED_SORA_CHRINIT_ABILITY_FIELDS,
        GATE_OPENER_ITEM_NAME,
        GATE_OPENER_RUNTIME_TOKEN,
        KEYBLADE_STYLE_RUNTIME_TOKEN_BY_ITEM_NAME,
        LIVE_BONUS_STAT_RUNTIME_TOKENS,
        MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME,
        PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME,
        PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME,
        ULTIMA_WEAPON_SHARD_ITEM_NAME,
        ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN,
        local_seeded_reward_requires_bridge_delivery,
        resolve_runtime_token_display_name,
    )
except ImportError:
    if str(ARCH_ROOT) not in sys.path:
        sys.path.insert(0, str(ARCH_ROOT))
    from worlds.kh3.Data import (
        TRAP_RUNTIME_TOKEN_BY_ITEM_NAME,
        CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME,
        CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME,
        DEFAULT_BASE_ABILITY_CHRINIT_FIELDS,
        FILLER_ITEM_RUNTIME_TOKENS,
        FIXED_SORA_CHRINIT_ABILITY_FIELDS,
        GATE_OPENER_ITEM_NAME,
        GATE_OPENER_RUNTIME_TOKEN,
        KEYBLADE_STYLE_RUNTIME_TOKEN_BY_ITEM_NAME,
        LIVE_BONUS_STAT_RUNTIME_TOKENS,
        MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME,
        PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME,
        PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME,
        ULTIMA_WEAPON_SHARD_ITEM_NAME,
        ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN,
        local_seeded_reward_requires_bridge_delivery,
        resolve_runtime_token_display_name,
    )


def first_existing_path(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_KH3_PATH = first_existing_path(
    [
        KH3_WORLD_ROOT / "randomizer" / "wwwroot" / "DefaultKH3.json",
        Path.home()
        / "Documents"
        / "projects"
        / "Arch"
        / "KH3Rando"
        / "Source Files"
        / "KH3RandomizerClient"
        / "KH3Randomizer"
        / "wwwroot"
        / "DefaultKH3.json",
    ]
)
DEFAULT_KH3_OVERRIDE_PATH = DEFAULT_KH3_PATH.with_name("DefaultKH3.ArchipelagoOverrides.json")

INVALID_ITEM_NAMES = {
    "ETresAbilityKind::NONE",
    "ETresVictoryBonusKind::NONE",
}

RAW_ITEM_DISPLAY_NAMES = {
    "WEP_KEYBLADE_SO_00": "Kingdom Key",
    "WEP_KEYBLADE_SO_01": "Hero's Origin",
    "WEP_KEYBLADE_SO_02": "Shooting Star",
    "WEP_KEYBLADE_SO_03": "Favorite Deputy",
    "WEP_KEYBLADE_SO_04": "Ever After",
    "WEP_KEYBLADE_SO_05": "Happy Gear",
    "WEP_KEYBLADE_SO_06": "Crystal Snow",
    "WEP_KEYBLADE_SO_07": "Hunny Spout",
    "WEP_KEYBLADE_SO_08": "Nano Gear",
    "WEP_KEYBLADE_SO_09": "Wheel of Fate",
    "WEP_KEYBLADE_SO_011": "Grand Chef",
    "WEP_KEYBLADE_SO_012": "Classic Tone",
    "WEP_KEYBLADE_SO_013": "Oathkeeper",
    "WEP_KEYBLADE_SO_014": "Oblivion",
    "WEP_KEYBLADE_SO_015": "Ultima Weapon",
    "WEP_KEYBLADE_SO_018": "Starlight",
}

CONTROLLER_UNLOCK_ITEMS = {
    "Style Change: Spellweaver": "spellweaver",
    "Unlock: Wall Run": "wall_run",
    "Unlock: Style Change": "style_change",
    "Unlock: Magic": "magic",
    "Unlock: Links": "links",
    "Unlock: Attraction Flow": "attraction_flow",
    "Unlock: Airstep": "airstep",
}
CONTROLLER_UNLOCK_RUNTIME_TOKEN_PREFIX = "KH3AP_CONTROLLER_UNLOCK::"

FOREIGN_ITEM_PLACEHOLDER_NAME = "Archipelago Item"
FOREIGN_ITEM_PLACEHOLDER_TOKEN = "ARCHIPELAGO_ITEM"
FOREIGN_ITEM_PLACEHOLDER_TOKEN_PREFIX = "AP_ITEM_"
LUCKY_MARK_FOREIGN_ITEM_NATIVE_TOKEN_PREFIX = "ITEM_AP_"
LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_PREFIX = "I02"
LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_START = 200
LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_MIN = 6
LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_MAX = 255
FOREIGN_ITEM_PLACEHOLDER_RUNTIME_TOKEN = "AP_ITEM_000"
ALWAYS_ON_EVENT_IDS = (
    "EVENT_KEYBLADE_012",
    "EVENT_KEYBLADE_013",
    "EVENT_HEARTBINDER_001",
    "EVENT_KEYITEM_001",
    "EVENT_KEYITEM_003",
    "EVENT_KEYITEM_004",
)
SUPPRESSED_ALWAYS_ON_EVENT_TOKEN = "ETresVictoryBonusKind::NONE"
LEVEL_UP_NONE_TOKEN = "ETresAbilityKind::NONE"
# Lucky Emblem rows above the configured AP limit still grant their replacement
# natively. Munny Pouches require the bridge's composite item-plus-munny grant,
# so using one here both skips its munny and exposes an unregistered custom token
# to the data-table interpreter.
LUCKY_MARK_LIMIT_FILLER_TOKENS = tuple(
    token
    for token in FILLER_ITEM_RUNTIME_TOKENS
    if token not in MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME.values()
)
LUCKY_EMBLEM_TOTAL_COUNT = 90

PROGRESSION_KEYWORDS = (
    "Proof of ",
    "Secret Report ",
    "Heartbinder",
    "Magic: ",
    "Ability: Air Slide",
    "Ability: Dodge Roll",
    "Ability: Block",
    "Ability: Pole Spin",
    "Ability: Doubleflight",
    "Ability: Second Chance",
    "Ability: Withstand Combo",
    "Unlock: ",
    "Archipelago Item",
)


@dataclass(frozen=True)
class LocationKey:
    source_table: str
    source_id: str


@dataclass(frozen=True)
class ForeignItemPlacement:
    ap_location_id: int
    ap_location_name: str
    location_type: str
    source_table: str
    source_id: str
    placeholder_item_name: str
    placeholder_token: str
    placeholder_runtime_token: str
    item_id: int
    item_name: str
    item_display_name: str
    item_game: str
    recipient_slot: int
    recipient_name: str


@dataclass(frozen=True)
class ExportResult:
    randomized_options: dict[str, Any]
    foreign_item_manifest: dict[str, Any]
    placement_summary: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export an AP archive into exact KH3Rando randomized-options JSON."
    )
    parser.add_argument("ap_zip", type=Path, help="Path to AP_<seed>.zip")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write randomized-options JSON.",
    )
    parser.add_argument(
        "--foreign-item-manifest",
        type=Path,
        default=None,
        help="Optional path to write foreign-item placeholder metadata JSON.",
    )
    parser.add_argument(
        "--placement-summary",
        type=Path,
        default=None,
        help="Optional path to write KH3 placement summary JSON.",
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=None,
        help="Numeric AP slot id. If omitted, uses the only KH3 slot in the archive.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ARCHIPELAGO_RUNTIME_ROOT,
        help="Archipelago runtime root containing Utils.py and Options.py",
    )
    parser.add_argument(
        "--default-kh3",
        type=Path,
        default=DEFAULT_KH3_PATH,
        help="Path to KH3Rando DefaultKH3.json",
    )
    parser.add_argument(
        "--default-kh3-overrides",
        type=Path,
        default=DEFAULT_KH3_OVERRIDE_PATH,
        help="Optional path to KH3Rando local override JSON.",
    )
    parser.add_argument(
        "--extracted-data",
        type=Path,
        default=EXTRACTED_DATA_PATH,
        help="Path to Archipelago KH3 extracted_locations.json",
    )
    parser.add_argument(
        "--chrinit-sora-have-ability",
        action="append",
        default=[],
        help=(
            "Compatibility hook for Sora ChrInit HaveAbility. New seeds always use "
            "Zero EXP here; any other requested ability is rejected."
        ),
    )
    parser.add_argument(
        "--event-reward-override",
        action="append",
        default=[],
        help=(
            "Experimental exact-seed patch: override an Event reward row with "
            "EVENT_ID=Item Name or EVENT_ID:FieldName=Item Name. Defaults to "
            "RandomizedItem for normal events."
        ),
    )
    return parser.parse_args()


def is_invalid_item_name(item_name: str) -> bool:
    return not item_name or item_name in INVALID_ITEM_NAMES or item_name.endswith("::NONE")


def is_progression_name(item_name: str) -> bool:
    return item_name.startswith(PROGRESSION_KEYWORDS) or item_name.startswith("Proof of ")


def build_archipelago_item_display_name(item_name: str, recipient_name: str, is_foreign_item: bool) -> str:
    item_name = str(item_name or "").strip()
    recipient_name = str(recipient_name or "").strip()
    if is_foreign_item and item_name and recipient_name:
        return f"{item_name} for {recipient_name}"
    return item_name


def add_null_terminator(value: str) -> str:
    return value if value.endswith("\u0000") else f"{value}\u0000"


def build_foreign_item_placeholder_token(ap_location_id: int) -> str:
    return f"{FOREIGN_ITEM_PLACEHOLDER_TOKEN_PREFIX}{ap_location_id}"


def build_lucky_mark_foreign_item_native_token(
    ap_location_id: int,
    *,
    native_item_row_name: str | None = None,
) -> str:
    if native_item_row_name:
        return f"{LUCKY_MARK_FOREIGN_ITEM_NATIVE_TOKEN_PREFIX}{native_item_row_name}_{ap_location_id}"
    return f"{LUCKY_MARK_FOREIGN_ITEM_NATIVE_TOKEN_PREFIX}{ap_location_id}"


class LuckyMarkNativeItemRowAllocator:
    def __init__(self) -> None:
        first_span = range(
            LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_START,
            LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_MAX + 1,
        )
        second_span = range(
            LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_MIN,
            LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_START,
        )
        self._available_indexes = list(first_span) + list(second_span)
        self._row_by_ap_location_id: dict[int, str] = {}

    def row_name_for(self, ap_location_id: int) -> str:
        existing = self._row_by_ap_location_id.get(ap_location_id)
        if existing is not None:
            return existing
        if not self._available_indexes:
            raise RuntimeError("Too many Lucky Emblem AP milestone items for native item rows.")

        row_name = f"{LUCKY_MARK_FOREIGN_ITEM_NATIVE_ROW_PREFIX}{self._available_indexes.pop(0):03d}"
        self._row_by_ap_location_id[ap_location_id] = row_name
        return row_name


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def deep_merge_dict(target: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge_dict(target[key], value)
        else:
            target[key] = value
    return target


def load_default_kh3(default_path: Path, override_path: Path | None) -> dict[str, Any]:
    defaults = load_json(default_path)
    if override_path and override_path.exists():
        deep_merge_dict(defaults, load_json(override_path))
    return defaults


def build_item_name_to_token(extracted_data: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in extracted_data["items"]:
        item_name = item["name"]
        if is_invalid_item_name(item_name):
            continue
        token = str(item["id"])
        if token.rstrip("\0").strip().isdecimal():
            continue
        mapping[item_name] = token
        display_name = RAW_ITEM_DISPLAY_NAMES.get(token)
        if display_name and display_name not in mapping:
            mapping[display_name] = token

    mapping.setdefault(FOREIGN_ITEM_PLACEHOLDER_NAME, FOREIGN_ITEM_PLACEHOLDER_RUNTIME_TOKEN)
    # Gate Openers are seed-authored inventory items, absent from vanilla data.
    mapping[GATE_OPENER_ITEM_NAME] = GATE_OPENER_RUNTIME_TOKEN
    for item_name, runtime_token in MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME.items():
        mapping.setdefault(item_name, runtime_token)
    mapping.update(CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME)
    try:
        from .Minigames import MINIGAME_APPENDED_ITEMS
    except ImportError:
        from worlds.kh3.Minigames import MINIGAME_APPENDED_ITEMS
    mapping.update(MINIGAME_APPENDED_ITEMS)
    # Standalone stat bonuses can enter the AP pool without appearing as an
    # individual item in the extracted vanilla reward tables.
    for runtime_token in LIVE_BONUS_STAT_RUNTIME_TOKENS:
        item_name = resolve_runtime_token_display_name(runtime_token)
        if item_name:
            mapping.setdefault(item_name, runtime_token)
    return mapping


def controller_unlock_runtime_token(item_name: str) -> str | None:
    unlock_key = CONTROLLER_UNLOCK_ITEMS.get(item_name)
    if unlock_key is None:
        return None
    return f"{CONTROLLER_UNLOCK_RUNTIME_TOKEN_PREFIX}{unlock_key}"


def party_member_unlock_runtime_token(item_name: str) -> str | None:
    return PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME.get(item_name)


def bridge_only_item_runtime_token(item_name: str) -> str | None:
    character_token = CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME.get(item_name)
    if character_token is not None:
        return character_token
    try:
        from .Space import SPACE_TOKENS
    except ImportError:
        from worlds.kh3.Space import SPACE_TOKENS
    if item_name in SPACE_TOKENS:
        return SPACE_TOKENS[item_name]
    if item_name == ULTIMA_WEAPON_SHARD_ITEM_NAME:
        return ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN
    munny_pouch_runtime_token = MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME.get(item_name)
    if munny_pouch_runtime_token is not None:
        return munny_pouch_runtime_token
    progressive_runtime_token = PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME.get(item_name)
    if progressive_runtime_token is not None:
        return progressive_runtime_token
    trap_runtime_token = TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.get(item_name)
    if trap_runtime_token is not None:
        return trap_runtime_token
    keyblade_style_runtime_token = KEYBLADE_STYLE_RUNTIME_TOKEN_BY_ITEM_NAME.get(item_name)
    if keyblade_style_runtime_token is not None:
        return keyblade_style_runtime_token
    controller_token = controller_unlock_runtime_token(item_name)
    if controller_token is not None:
        return controller_token
    return party_member_unlock_runtime_token(item_name)


def bridge_delivered_local_reward_token(location_type: str, ap_location_id: int) -> str:
    if location_type in {"event", "vbonus", "vbonus_extra"}:
        return SUPPRESSED_ALWAYS_ON_EVENT_TOKEN
    return build_foreign_item_placeholder_token(ap_location_id)


def build_extracted_location_map(extracted_data: dict[str, Any]) -> dict[LocationKey, dict[str, Any]]:
    try:
        from .Minigames import append_minigame_locations
    except ImportError:
        from worlds.kh3.Minigames import append_minigame_locations
    append_minigame_locations(extracted_data)
    return {
        LocationKey(location["source_table"], location["source_id"]): location
        for location in extracted_data["locations"]
    }


def build_game_item_id_to_name(multidata: dict[str, Any]) -> dict[str, dict[int, str]]:
    mappings: dict[str, dict[int, str]] = {}
    for game_name, package in multidata.get("datapackage", {}).items():
        item_name_to_id = package.get("item_name_to_id", {})
        mappings[game_name] = {item_id: item_name for item_name, item_id in item_name_to_id.items()}
    return mappings


def build_game_location_id_to_name(multidata: dict[str, Any]) -> dict[str, dict[int, str]]:
    mappings: dict[str, dict[int, str]] = {}
    for game_name, package in multidata.get("datapackage", {}).items():
        location_name_to_id = package.get("location_name_to_id", {})
        mappings[game_name] = {
            location_id: location_name
            for location_name, location_id in location_name_to_id.items()
            if location_id is not None
        }
    return mappings


def build_remote_hint_placements_from_multidata(
    multidata: dict[str, Any],
    kh3_slot: int,
    kh3_item_id_to_name: dict[int, str],
) -> list[dict[str, Any]]:
    """Describe KH3 items placed in another player's world for Secret Reports."""
    game_location_id_to_name = build_game_location_id_to_name(multidata)
    remote_placements: list[dict[str, Any]] = []

    for raw_location_slot, raw_assignments in multidata.get("locations", {}).items():
        location_slot = int(raw_location_slot)
        if location_slot == kh3_slot:
            continue

        location_game = get_slot_game(multidata, location_slot) or "Unknown Game"
        location_player_name = get_slot_name(multidata, location_slot)
        location_id_to_name = game_location_id_to_name.get(location_game, {})
        assignments = (
            dict(raw_assignments.items())
            if hasattr(raw_assignments, "items")
            else dict(raw_assignments)
        )

        for raw_location_id, assignment in assignments.items():
            item_id, recipient_slot, _flags = assignment
            if int(recipient_slot) != kh3_slot:
                continue

            if item_id is None:
                continue
            item_id = int(item_id)
            item_name = kh3_item_id_to_name.get(item_id)
            if not item_name:
                continue

            location_id = int(raw_location_id)
            location_name = location_id_to_name.get(location_id, f"Location {location_id}")
            remote_placements.append(
                {
                    "ap_location_id": location_id,
                    "ap_location_name": location_name,
                    "location_type": "archipelago",
                    "source_table": "Archipelago",
                    "source_id": str(location_id),
                    "world": location_game,
                    "location_description": location_name,
                    "location_game": location_game,
                    "location_player_slot": location_slot,
                    "location_player_name": location_player_name,
                    "is_remote_location": True,
                    "item_id": item_id,
                    "item_name": item_name,
                    "item_display_name": item_name,
                    "item_game": "Kingdom Hearts III",
                    "recipient_slot": kh3_slot,
                    "recipient_name": get_slot_name(multidata, kh3_slot),
                    "is_foreign_item": False,
                }
            )

    return sorted(
        remote_placements,
        key=lambda placement: (
            int(placement["location_player_slot"]),
            int(placement["ap_location_id"]),
        ),
    )


def build_hint_location_entries(
    placement_entries: list[dict[str, Any]],
    kh3_slot: int,
    remote_hint_placements: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    hint_locations = [
        dict(entry)
        for entry in placement_entries
        if int(entry.get("recipient_slot", kh3_slot)) == kh3_slot
    ]
    seen = {
        (
            int(entry.get("location_player_slot", kh3_slot)),
            int(entry["ap_location_id"]),
        )
        for entry in hint_locations
    }

    for remote in remote_hint_placements or []:
        entry = dict(remote)
        recipient_slot = int(entry.get("recipient_slot", kh3_slot))
        if recipient_slot != kh3_slot:
            continue
        entry["recipient_slot"] = recipient_slot
        entry.setdefault("source_table", "Archipelago")
        entry.setdefault("source_id", str(entry.get("ap_location_id", "")))
        entry.setdefault("location_description", entry.get("ap_location_name"))
        entry.setdefault("world", entry.get("location_game"))
        entry.setdefault("is_remote_location", True)
        key = (
            int(entry.get("location_player_slot", kh3_slot)),
            int(entry["ap_location_id"]),
        )
        if key in seen:
            continue
        seen.add(key)
        hint_locations.append(entry)

    return hint_locations


def choose_vbonus_primary_field(location: dict[str, Any]) -> str:
    vanilla_item_names: dict[str, str] = location["vanilla_item_names"]
    all_fields = list(vanilla_item_names.keys())
    ordered_items = [
        (field_name, item_name)
        for field_name, item_name in vanilla_item_names.items()
        if not is_invalid_item_name(item_name)
    ]
    if not ordered_items:
        if not all_fields:
            raise ValueError(f"VBonus location {location['source_id']} has no reward fields.")
        return all_fields[0]

    for field_name, item_name in ordered_items:
        if is_progression_name(item_name):
            return field_name

    for field_name, item_name in ordered_items:
        if item_name.startswith("Bonus: "):
            return field_name

    return ordered_items[0][0]


def apply_treasure_override(default_options: dict[str, Any], bridge_location: dict[str, Any], token: str) -> None:
    if bridge_location["source_table"] not in default_options:
        raise ArchiveValidationError(
            f"DefaultKH3 is missing treasure table '{bridge_location['source_table']}' for {bridge_location['bridge_key']}."
        )
    if bridge_location["source_id"] not in default_options[bridge_location["source_table"]]:
        raise ArchiveValidationError(
            f"DefaultKH3 is missing treasure source '{bridge_location['source_id']}' for {bridge_location['bridge_key']}."
        )
    default_options[bridge_location["source_table"]][bridge_location["source_id"]]["Treasure"] = add_null_terminator(token)


def apply_event_override(default_options: dict[str, Any], bridge_location: dict[str, Any], token: str) -> None:
    # Battlegate 0 is a flag-backed AP-only reward; no native Event item row
    # exists or should be manufactured in the randomizer's tables.
    if bridge_location["source_id"] == "EVENT_REPORT_000":
        return
    if "Event" not in default_options or bridge_location["source_id"] not in default_options["Event"]:
        raise ArchiveValidationError(
            f"DefaultKH3 is missing event source '{bridge_location['source_id']}' for {bridge_location['bridge_key']}."
        )
    field_name = bridge_location.get("subtype") or (
        "Reward" if bridge_location["source_id"] == "TresUIMobilePortalDataAsset" else "RandomizedItem"
    )
    default_options["Event"][bridge_location["source_id"]][field_name] = add_null_terminator(token)


def native_reward_token_for_foreign_location(location_type: str, export_token: str) -> str:
    if location_type in {"event", "vbonus", "vbonus_extra"}:
        return SUPPRESSED_ALWAYS_ON_EVENT_TOKEN
    return export_token


def suppress_always_on_event_rewards(default_options: dict[str, Any]) -> list[dict[str, Any]]:
    event_table = default_options.get("Event")
    if not isinstance(event_table, dict):
        raise ArchiveValidationError("DefaultKH3 is missing Event table for Always On suppression.")

    suppressed: list[dict[str, Any]] = []
    for event_id in ALWAYS_ON_EVENT_IDS:
        event_row = event_table.get(event_id)
        if not isinstance(event_row, dict):
            raise ArchiveValidationError(f"DefaultKH3 is missing Always On Event row '{event_id}'.")
        previous_token = event_row.get("RandomizedItem")
        event_row["RandomizedItem"] = add_null_terminator(SUPPRESSED_ALWAYS_ON_EVENT_TOKEN)
        suppressed.append({
            "event_id": event_id,
            "field_name": "RandomizedItem",
            "previous_token": previous_token,
            "runtime_token": SUPPRESSED_ALWAYS_ON_EVENT_TOKEN,
        })
    return suppressed


def read_default_base_abilities_from_randomizer_config(randomizer_config: Any) -> bool | None:
    if not isinstance(randomizer_config, dict):
        return None
    exceptions = randomizer_config.get("exceptions")
    if not isinstance(exceptions, dict) or "Default Base Abilities" not in exceptions:
        return None

    raw_value = exceptions["Default Base Abilities"]
    if isinstance(raw_value, str):
        return raw_value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(raw_value)


def suppress_default_base_chrinit_abilities(
    default_options: dict[str, Any],
    randomizer_config: Any,
) -> list[dict[str, Any]]:
    default_base_abilities = read_default_base_abilities_from_randomizer_config(randomizer_config)
    if default_base_abilities is not False:
        return []

    try:
        player_sora = default_options["ChrInit"]["m_PlayerSora"]
    except KeyError as exc:
        raise ArchiveValidationError("DefaultKH3 is missing ChrInit.m_PlayerSora for default ability suppression.") from exc
    if not isinstance(player_sora, dict):
        raise ArchiveValidationError("DefaultKH3 ChrInit.m_PlayerSora is not a valid field map.")

    suppressed: list[dict[str, Any]] = []
    for field_name, runtime_token, item_name in DEFAULT_BASE_ABILITY_CHRINIT_FIELDS:
        previous_token = player_sora.get(field_name)
        player_sora[field_name] = add_null_terminator(LEVEL_UP_NONE_TOKEN)
        suppressed.append({
            "target": f"ChrInit.m_PlayerSora.{field_name}",
            "field_name": field_name,
            "item_name": item_name,
            "previous_token": previous_token,
            "previous_runtime_token": runtime_token,
            "runtime_token": LEVEL_UP_NONE_TOKEN,
        })
    return suppressed


def enforce_fixed_sora_chrinit_abilities(default_options: dict[str, Any]) -> list[dict[str, Any]]:
    chrinit = default_options.setdefault("ChrInit", {})
    if not isinstance(chrinit, dict):
        raise ArchiveValidationError("DefaultKH3 ChrInit is not a valid row map.")
    player_sora = chrinit.setdefault("m_PlayerSora", {})
    if not isinstance(player_sora, dict):
        raise ArchiveValidationError("DefaultKH3 ChrInit.m_PlayerSora is not a valid field map.")

    enforced: list[dict[str, Any]] = []
    for field_name, runtime_token, item_name in FIXED_SORA_CHRINIT_ABILITY_FIELDS:
        previous_token = player_sora.get(field_name)
        player_sora[field_name] = add_null_terminator(runtime_token)
        enforced.append({
            "target": f"ChrInit.m_PlayerSora.{field_name}",
            "field_name": field_name,
            "item_name": item_name,
            "previous_token": previous_token,
            "runtime_token": runtime_token,
        })
    return enforced


def apply_lucky_mark_override(default_options: dict[str, Any], bridge_location: dict[str, Any], token: str) -> None:
    lucky_mark_table = default_options.get("LuckyMark")
    if not isinstance(lucky_mark_table, dict):
        raise ArchiveValidationError("DefaultKH3 is missing the LuckyMark table.")

    source_id = str(bridge_location["source_id"])
    try:
        milestone = int(source_id)
    except ValueError:
        milestone = 0
    if source_id not in lucky_mark_table and not (1 <= milestone <= LUCKY_EMBLEM_TOTAL_COUNT):
        raise ArchiveValidationError(
            f"DefaultKH3 is missing lucky mark source '{bridge_location['source_id']}' for {bridge_location['bridge_key']}."
        )
    lucky_mark_table.setdefault(source_id, {"Reward": add_null_terminator("ITEM_POTION")})
    lucky_mark_table[source_id]["Reward"] = add_null_terminator(token)


def suppress_level_up_path_rewards(default_options: dict[str, Any]) -> list[dict[str, Any]]:
    """Retire every native growth-path reward; level checks are AP-owned."""
    level_up_table = default_options.get("LevelUp")
    if level_up_table is None:
        return []
    if not isinstance(level_up_table, dict):
        raise ArchiveValidationError("DefaultKH3 is missing the LevelUp table.")

    suppressed: list[dict[str, Any]] = []
    for source_id, fields in level_up_table.items():
        if not isinstance(fields, dict):
            raise ArchiveValidationError(
                f"DefaultKH3 LevelUp row '{source_id}' is not a valid field map."
            )
        previous_fields = dict(fields)
        for field_name in tuple(fields):
            fields[field_name] = add_null_terminator(LEVEL_UP_NONE_TOKEN)
        suppressed.append({
            "source_id": str(source_id),
            "previous_fields": previous_fields,
        })
    return suppressed


def read_level_up_limit_from_randomizer_config(randomizer_config: Any) -> int | None:
    if not isinstance(randomizer_config, dict):
        return None
    limiters = randomizer_config.get("limiters")
    if not isinstance(limiters, dict):
        return None
    raw_limit = limiters.get("Level Up Limit")
    try:
        return int(raw_limit)
    except (TypeError, ValueError):
        return None


def read_lucky_emblem_limit_from_randomizer_config(randomizer_config: Any) -> int | None:
    if not isinstance(randomizer_config, dict):
        return None
    limiters = randomizer_config.get("limiters")
    if not isinstance(limiters, dict):
        return None
    raw_limit = limiters.get("Lucky Emblem Limit")
    try:
        return int(raw_limit)
    except (TypeError, ValueError):
        return None


def read_lucky_emblems_per_check_from_randomizer_config(randomizer_config: Any) -> int:
    if not isinstance(randomizer_config, dict):
        return 0
    raw_batch_size = randomizer_config.get("lucky_emblems_per_check", 0)
    try:
        batch_size = int(raw_batch_size)
    except (TypeError, ValueError):
        return 0
    return batch_size if 1 <= batch_size <= LUCKY_EMBLEM_TOTAL_COUNT else 0


def configure_lucky_mark_batch_rows(
    default_options: dict[str, Any],
    randomizer_config: Any,
    bridge_locations: list[dict[str, Any]],
    *,
    seed_material: str = "",
) -> dict[str, Any]:
    batch_size = read_lucky_emblems_per_check_from_randomizer_config(randomizer_config)
    if batch_size <= 0:
        return {
            "batch_size": 0,
            "active_milestones": [],
            "added_milestones": [],
            "completion_row_added": False,
        }

    active_milestones = sorted({
        int(str(location.get("source_id")))
        for location in bridge_locations
        if location.get("source_table") == "LuckyMark"
        and str(location.get("source_id", "")).isdigit()
        and 1 <= int(str(location.get("source_id"))) <= LUCKY_EMBLEM_TOTAL_COUNT
    })

    # Older seed containers used a separate include toggle. Honor it only when
    # replaying those containers; current seeds use the pool mode exclusively.
    raw_legacy_include_lucky_emblems = (
        randomizer_config.get("include_lucky_emblems")
        if isinstance(randomizer_config, dict)
        else None
    )
    if isinstance(raw_legacy_include_lucky_emblems, str):
        legacy_include_lucky_emblems = raw_legacy_include_lucky_emblems.strip().casefold() in {
            "1", "true", "yes", "on",
        }
    elif raw_legacy_include_lucky_emblems is None:
        legacy_include_lucky_emblems = None
    else:
        legacy_include_lucky_emblems = bool(raw_legacy_include_lucky_emblems)

    pools = randomizer_config.get("pools") if isinstance(randomizer_config, dict) else None
    lucky_emblems_pool_is_configured = isinstance(pools, dict) and any(
        str(pool_name).casefold() == "lucky emblems"
        for pool_name in pools
    )

    batching_is_active = (
        legacy_include_lucky_emblems is not False
        and (
            bool(active_milestones)
            or legacy_include_lucky_emblems is True
            or (legacy_include_lucky_emblems is None and lucky_emblems_pool_is_configured)
        )
    )
    if not batching_is_active:
        return {
            "batch_size": batch_size,
            "active_milestones": active_milestones,
            "added_milestones": [],
            "completion_row_added": False,
        }

    lucky_mark_table = default_options.get("LuckyMark")
    if not isinstance(lucky_mark_table, dict):
        raise ArchiveValidationError("DefaultKH3 is missing LuckyMark table for configurable batching.")

    configured_rows: dict[str, dict[str, Any]] = {}
    added_milestones: list[int] = []
    for milestone in active_milestones:
        source_id = str(milestone)
        existing_row = lucky_mark_table.get(source_id)
        if isinstance(existing_row, dict):
            configured_rows[source_id] = dict(existing_row)
        else:
            added_milestones.append(milestone)
            configured_rows[source_id] = {
                "Reward": add_null_terminator(
                    select_lucky_mark_limit_filler_token(source_id, seed_material)
                ),
            }

    completion_row_added = LUCKY_EMBLEM_TOTAL_COUNT not in active_milestones
    if completion_row_added:
        completion_source_id = str(LUCKY_EMBLEM_TOTAL_COUNT)
        configured_rows[completion_source_id] = {
            "Reward": add_null_terminator(
                select_lucky_mark_limit_filler_token(completion_source_id, seed_material)
            ),
        }

    default_options["LuckyMark"] = configured_rows
    return {
        "batch_size": batch_size,
        "active_milestones": active_milestones,
        "added_milestones": added_milestones,
        "completion_row_added": completion_row_added,
    }


def read_level_up_limit_for_export(randomizer_config: Any) -> int | None:
    return read_level_up_limit_from_randomizer_config(randomizer_config)


def read_lucky_emblem_limit_for_export(
    randomizer_config: Any,
    bridge_locations: list[dict[str, Any]],
) -> int | None:
    if not any(location.get("source_table") == "LuckyMark" for location in bridge_locations):
        return None
    return read_lucky_emblem_limit_from_randomizer_config(randomizer_config)


def select_lucky_mark_limit_filler_token(source_id: str, seed_material: str = "") -> str:
    selection_key = f"kh3-ap-lucky-mark-junk\0{seed_material}\0{source_id}".encode("utf-8")
    digest = hashlib.sha256(selection_key).digest()
    index = int.from_bytes(digest[:8], "big") % len(LUCKY_MARK_LIMIT_FILLER_TOKENS)
    return LUCKY_MARK_LIMIT_FILLER_TOKENS[index]


def suppress_lucky_mark_rewards_above_limit(
    default_options: dict[str, Any],
    lucky_emblem_limit: int | None,
    protected_source_ids: set[str] | frozenset[str] = frozenset(),
    *,
    seed_material: str = "",
) -> list[dict[str, Any]]:
    if lucky_emblem_limit is None or lucky_emblem_limit >= 90:
        return []
    lucky_mark_table = default_options.get("LuckyMark")
    if not isinstance(lucky_mark_table, dict):
        raise ArchiveValidationError("DefaultKH3 is missing LuckyMark table for Lucky Emblem Limit enforcement.")

    suppressed: list[dict[str, Any]] = []
    for source_id, fields in lucky_mark_table.items():
        source_id_text = str(source_id)
        try:
            milestone = int(source_id_text)
        except ValueError:
            continue
        if (
            milestone <= lucky_emblem_limit
            or source_id_text in protected_source_ids
            or not isinstance(fields, dict)
        ):
            continue

        previous_token = fields.get("Reward")
        filler_token = select_lucky_mark_limit_filler_token(source_id_text, seed_material)
        fields["Reward"] = add_null_terminator(filler_token)
        suppressed.append({
            "source_id": source_id_text,
            "milestone": milestone,
            "previous_token": previous_token,
            "runtime_token": filler_token,
        })
    return suppressed


def apply_vbonus_override(
    default_options: dict[str, Any],
    bridge_location: dict[str, Any],
    extracted_location: dict[str, Any],
    token: str,
    *,
    suppress_reward: bool = False,
) -> None:
    vbonus_table = default_options.setdefault("VBonus", {})
    target_fields = vbonus_table.setdefault(
        bridge_location["source_id"],
        {
            field_name: add_null_terminator(vanilla_token)
            for field_name, vanilla_token in extracted_location["vanilla_items"].items()
        },
    )
    target_field = None if suppress_reward else choose_vbonus_primary_field(extracted_location)
    for field_name, vanilla_token in extracted_location["vanilla_items"].items():
        if field_name == target_field:
            target_fields[field_name] = add_null_terminator(token)
        else:
            target_fields[field_name] = add_null_terminator(vbonus_none_token_for_field(field_name, vanilla_token))


def vbonus_none_token_for_field(field_name: str, vanilla_token: str) -> str:
    if "Ability" in field_name or str(vanilla_token).startswith("ETresAbilityKind::"):
        return "ETresAbilityKind::NONE"
    return "ETresVictoryBonusKind::NONE"


def normalize_ability_injection_token(value: str, item_name_to_token: dict[str, str]) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("ChrInit ability injection cannot be empty.")
    if normalized.startswith("ETresAbilityKind::"):
        return normalized
    if not normalized.startswith("Ability: "):
        normalized = f"Ability: {normalized}"
    token = item_name_to_token.get(normalized)
    if token is None or not token.startswith("ETresAbilityKind::"):
        raise ValueError(f"ChrInit ability injection is not a known KH3 ability: {value}")
    return token


def normalize_reward_override_token(value: str, item_name_to_token: dict[str, str]) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Event reward override item cannot be empty.")
    if normalized in item_name_to_token:
        return item_name_to_token[normalized]
    if normalized.startswith(("ETresAbilityKind::", "ETresVictoryBonusKind::")):
        return normalized
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized):
        return normalized
    raise ValueError(f"Event reward override is not a known KH3 item/token: {value}")


def split_chrinit_ability_values(values: list[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]

    split_values: list[str] = []
    for value in values:
        for part in str(value).split(","):
            stripped = part.strip()
            if stripped:
                split_values.append(stripped)
    return split_values


def apply_chrinit_sora_have_ability_injection(
    default_options: dict[str, Any],
    ability_value: list[str] | str | None,
    item_name_to_token: dict[str, str],
) -> dict[str, Any] | None:
    requested_values = split_chrinit_ability_values(ability_value)
    if not requested_values:
        return None
    if len(requested_values) != 1:
        raise ValueError(
            "ChrInit.m_PlayerSora.HaveAbility is a single enum field; "
            "only one startup ability can be injected through this hook."
        )

    token = normalize_ability_injection_token(requested_values[0], item_name_to_token)
    fixed_token = FIXED_SORA_CHRINIT_ABILITY_FIELDS[0][1]
    if token != fixed_token:
        raise ValueError(
            "ChrInit.m_PlayerSora.HaveAbility is fixed to Ability: Zero EXP for every seed "
            "and cannot be overridden."
        )
    try:
        player_sora = default_options["ChrInit"]["m_PlayerSora"]
    except KeyError as exc:
        raise ArchiveValidationError("DefaultKH3 is missing ChrInit.m_PlayerSora for ability injection.") from exc

    previous_token = player_sora.get("HaveAbility")
    player_sora["HaveAbility"] = add_null_terminator(token)
    return {
        "target": "ChrInit.m_PlayerSora.HaveAbility",
        "requested": requested_values[0],
        "runtime_token": token,
        "previous_token": previous_token,
    }


def apply_event_reward_overrides(
    default_options: dict[str, Any],
    override_values: list[str] | str | None,
    item_name_to_token: dict[str, str],
) -> list[dict[str, Any]]:
    if override_values is None:
        return []
    if isinstance(override_values, str):
        override_values = [override_values]

    applied: list[dict[str, Any]] = []
    for override_value in override_values:
        raw_override = str(override_value).strip()
        if not raw_override:
            continue
        if "=" not in raw_override:
            raise ValueError(
                "Event reward override must use EVENT_ID=Item Name or EVENT_ID:FieldName=Item Name."
            )
        target, requested_item = raw_override.split("=", 1)
        target = target.strip()
        requested_item = requested_item.strip()
        if not target or not requested_item:
            raise ValueError(
                "Event reward override must include both an event target and an item/token."
            )

        if ":" in target:
            event_id, field_name = [part.strip() for part in target.split(":", 1)]
        else:
            event_id = target
            field_name = "Reward" if event_id == "TresUIMobilePortalDataAsset" else "RandomizedItem"
        if not event_id or not field_name:
            raise ValueError(f"Invalid Event reward override target: {target}")

        try:
            event_row = default_options["Event"][event_id]
        except KeyError as exc:
            raise ArchiveValidationError(f"DefaultKH3 is missing Event row '{event_id}'.") from exc
        if field_name not in event_row:
            raise ArchiveValidationError(f"DefaultKH3 Event row '{event_id}' is missing field '{field_name}'.")

        token = normalize_reward_override_token(requested_item, item_name_to_token)
        previous_token = event_row.get(field_name)
        event_row[field_name] = add_null_terminator(token)
        applied.append(
            {
                "target": f"Event.{event_id}.{field_name}",
                "event_id": event_id,
                "field_name": field_name,
                "requested": requested_item,
                "runtime_token": token,
                "previous_token": previous_token,
            }
        )

    return applied


def export_randomized_options_from_bridge_data(
    *,
    default_kh3: Path,
    default_kh3_overrides: Path | None,
    extracted_data_path: Path,
    bridge_locations: list[dict[str, Any]],
    slot: int,
    slot_name: str,
    archive_name: str,
    randomizer_config: dict[str, Any] | None = None,
    chrinit_sora_have_ability: list[str] | str | None = None,
    event_reward_override: list[str] | str | None = None,
    remote_hint_placements: list[dict[str, Any]] | None = None,
    race_mode: bool = False,
) -> ExportResult:
    if race_mode:
        from worlds.kh3.RaceMode import race_export_location
        bridge_locations = [race_export_location(row) for row in bridge_locations]
        remote_hint_placements = []
    default_options = load_default_kh3(default_kh3, default_kh3_overrides)
    suppressed_level_up_path_rewards = suppress_level_up_path_rewards(default_options)
    suppressed_always_on_events = suppress_always_on_event_rewards(default_options)
    suppressed_default_base_chrinit_abilities = suppress_default_base_chrinit_abilities(
        default_options,
        randomizer_config,
    )
    extracted_data = load_json(extracted_data_path)
    item_name_to_token = build_item_name_to_token(extracted_data)
    extracted_location_map = build_extracted_location_map(extracted_data)
    foreign_item_placeholder_runtime_token = item_name_to_token.get(
        FOREIGN_ITEM_PLACEHOLDER_NAME,
        FOREIGN_ITEM_PLACEHOLDER_RUNTIME_TOKEN,
    )
    if foreign_item_placeholder_runtime_token is None:
        raise RuntimeError(
            f"Required runtime placeholder item '{FOREIGN_ITEM_PLACEHOLDER_NAME}' is missing from "
            "extracted KH3 item data."
        )

    unsupported_items: dict[str, set[str]] = {}
    unsupported_assignments: list[str] = []
    foreign_item_placements: list[ForeignItemPlacement] = []
    placement_entries: list[dict[str, Any]] = []
    lucky_mark_native_item_rows = LuckyMarkNativeItemRowAllocator()
    chrinit_injection = apply_chrinit_sora_have_ability_injection(
        default_options,
        chrinit_sora_have_ability,
        item_name_to_token,
    )
    fixed_sora_chrinit_abilities = enforce_fixed_sora_chrinit_abilities(default_options)
    lucky_mark_batch_rows = configure_lucky_mark_batch_rows(
        default_options,
        randomizer_config,
        bridge_locations,
        seed_material=archive_name,
    )

    for bridge_location in bridge_locations:
        ap_location_id = bridge_location["ap_location_id"]
        recipient_slot = int(bridge_location.get("assigned_item_player", slot))
        recipient_name = "" if race_mode else str(bridge_location.get("assigned_item_player_name") or slot_name)
        assigned_item_name = str(bridge_location.get("assigned_item_name") or "")
        assigned_item_game = str(bridge_location.get("assigned_item_game") or "")
        if not assigned_item_name or not assigned_item_game:
            unsupported_assignments.append(
                f"{bridge_location['ap_location_name']} has no assigned item metadata in the KH3 container."
            )
            continue

        raw_item_id = bridge_location.get("assigned_item_id")
        item_id = int(raw_item_id) if raw_item_id is not None else 0
        item_classification = int(bridge_location.get("assigned_item_classification") or 0)
        is_foreign_item = recipient_slot != slot
        bridge_delivery_required = bool(bridge_location.get("bridge_delivery_required"))
        if is_foreign_item:
            item_display_name = build_archipelago_item_display_name(
                assigned_item_name,
                recipient_name,
                is_foreign_item,
            )
            token = build_foreign_item_placeholder_token(ap_location_id)
            runtime_token = foreign_item_placeholder_runtime_token
            foreign_item_placements.append(
                ForeignItemPlacement(
                    ap_location_id=ap_location_id,
                    ap_location_name=bridge_location["ap_location_name"],
                    location_type=bridge_location["type"],
                    source_table=bridge_location["source_table"],
                    source_id=bridge_location["source_id"],
                    placeholder_item_name=FOREIGN_ITEM_PLACEHOLDER_NAME,
                    placeholder_token=token,
                    placeholder_runtime_token=foreign_item_placeholder_runtime_token,
                    item_id=item_id,
                    item_name=assigned_item_name,
                    item_display_name=item_display_name,
                    item_game=assigned_item_game,
                    recipient_slot=recipient_slot,
                    recipient_name=recipient_name,
                )
            )
        else:
            bridge_runtime_token = bridge_only_item_runtime_token(assigned_item_name)
            if bridge_runtime_token is not None:
                token = bridge_delivered_local_reward_token(bridge_location["type"], ap_location_id)
                runtime_token = bridge_runtime_token
                bridge_delivery_required = True
            else:
                token = item_name_to_token.get(assigned_item_name)
                if token is None:
                    unsupported_items.setdefault(assigned_item_name, set()).add(
                        bridge_location["ap_location_name"]
                    )
                    continue
                runtime_token = token
                if local_seeded_reward_requires_bridge_delivery(
                    bridge_location.get("bridge_key"),
                    bridge_location["type"],
                    runtime_token,
                    persistent_chests=bridge_location.get("chest_persistence_version") == 1,
                ):
                    token = bridge_delivered_local_reward_token(bridge_location["type"], ap_location_id)
                    bridge_delivery_required = True

        item_display_name = build_archipelago_item_display_name(
            assigned_item_name,
            recipient_name,
            is_foreign_item,
        )
        location_type = bridge_location["type"]
        export_token = token
        if location_type == "synthesis":
            # Completion flags supply the one-time AP check; crafting remains native.
            bridge_delivery_required = True
        if location_type == "shop":
            export_token = str(
                bridge_location.get("shop_item_token")
                or build_foreign_item_placeholder_token(ap_location_id)
            )
            bridge_delivery_required = True
        if location_type == "lucky_emblem" and (is_foreign_item or bridge_delivery_required):
            export_token = build_lucky_mark_foreign_item_native_token(
                ap_location_id,
                native_item_row_name=lucky_mark_native_item_rows.row_name_for(ap_location_id),
            )
        native_reward_token = (
            native_reward_token_for_foreign_location(location_type, export_token)
            if is_foreign_item
            else export_token
        )
        suppress_vbonus_reward = (
            location_type == "vbonus"
            and (is_foreign_item or bridge_delivery_required)
        )

        if location_type == "treasure":
            if bridge_location.get("chest_persistence_version") == 1:
                native_reward_token = SUPPRESSED_ALWAYS_ON_EVENT_TOKEN
            apply_treasure_override(default_options, bridge_location, native_reward_token)
        elif location_type == "event":
            apply_event_override(default_options, bridge_location, native_reward_token)
        elif location_type == "lucky_emblem":
            apply_lucky_mark_override(default_options, bridge_location, native_reward_token)
        elif location_type == "synthesis":
            pass
        elif location_type == "keyblade_upgrade":
            default_options["WeaponEnhance"][bridge_location["source_id"]]["Ability"] = "ETresAbilityKind::NONE\0"
        elif location_type == "photo_mission":
            pass
        elif location_type == "level_up":
            # Level checks report through AP; their placed reward is delivered
            # by the bridge and is not constrained by the native LevelUp DT.
            pass
        elif location_type == "vbonus":
            extracted_location = extracted_location_map[
                LocationKey(bridge_location["source_table"], bridge_location["source_id"])
            ]
            apply_vbonus_override(
                default_options,
                bridge_location,
                extracted_location,
                native_reward_token,
                suppress_reward=suppress_vbonus_reward,
            )
        elif location_type == "shop":
            # The seed-owned TresItemData row is the native Buy-menu reward.
            # Its one-copy inventory token is observed by the runtime bridge,
            # which reports the location and lets AP deliver the assigned item.
            pass
        elif location_type in BRIDGE_ONLY_LOCATION_TYPES:
            # Completion is reported by the runtime or client bridge. Synthetic
            # checks have no seed-pak row to edit; AP delivers local items.
            pass
        else:
            raise ValueError(f"Unsupported bridge location type: {location_type}")

        if bridge_delivery_required:
            bridge_location["bridge_delivery_required"] = True

        placement_entries.append(
            {
                "ap_location_id": ap_location_id,
                "ap_location_name": bridge_location["ap_location_name"],
                "bridge_key": bridge_location["bridge_key"],
                "location_type": location_type,
                "source_table": bridge_location["source_table"],
                "source_id": bridge_location["source_id"],
                "world": bridge_location.get("world"),
                "location_description": bridge_location.get("location_description"),
                "location_game": "Kingdom Hearts III",
                "location_player_slot": slot,
                "location_player_name": slot_name,
                "is_remote_location": False,
                "item_id": item_id,
                "item_classification": item_classification,
                "item_name": assigned_item_name,
                "item_display_name": item_display_name,
                "item_game": assigned_item_game,
                "recipient_slot": recipient_slot,
                "recipient_name": recipient_name,
                "is_foreign_item": is_foreign_item,
                "export_token": export_token,
                "native_reward_token": native_reward_token,
                "runtime_token": runtime_token,
                "bridge_delivery_required": bridge_delivery_required,
                **({
                    "shop_item_token": export_token,
                    "shop_price": int(bridge_location.get("shop_price") or 0),
                } if location_type == "shop" else {}),
            }
        )

    if unsupported_assignments:
        message = "\n".join(unsupported_assignments[:20])
        raise RuntimeError(
            "KH3 container contains assignments that could not be resolved into KH3 local items "
            "or foreign-item placeholders.\n"
            f"{message}"
        )

    if unsupported_items:
        lines = []
        for item_name, locations in sorted(unsupported_items.items()):
            sample_locations = ", ".join(sorted(locations)[:3])
            lines.append(f"{item_name}: {sample_locations}")
        raise RuntimeError(
            "KH3 container contains item names that cannot be mapped into KH3 reward tokens.\n"
            + "\n".join(lines)
        )

    event_reward_overrides = apply_event_reward_overrides(
        default_options,
        event_reward_override,
        item_name_to_token,
    )
    level_up_limit = read_level_up_limit_for_export(randomizer_config)
    lucky_emblem_limit = read_lucky_emblem_limit_for_export(randomizer_config, bridge_locations)
    lucky_mark_bridge_source_ids = {
        str(location.get("source_id"))
        for location in bridge_locations
        if location.get("source_table") == "LuckyMark"
    }
    suppressed_lucky_emblem_limit_rewards = suppress_lucky_mark_rewards_above_limit(
        default_options,
        lucky_emblem_limit,
        lucky_mark_bridge_source_ids,
        seed_material=archive_name,
    )
    hint_locations = [] if race_mode else build_hint_location_entries(
        placement_entries,
        slot,
        remote_hint_placements,
    )

    foreign_item_manifest = {
        "archive_name": archive_name,
        "kh3_slot": slot,
        "kh3_player_name": slot_name,
        "placeholder_item_name": FOREIGN_ITEM_PLACEHOLDER_NAME,
        "placeholder_token": FOREIGN_ITEM_PLACEHOLDER_TOKEN,
        "legacy_placeholder_token": FOREIGN_ITEM_PLACEHOLDER_TOKEN,
        "placeholder_token_prefix": FOREIGN_ITEM_PLACEHOLDER_TOKEN_PREFIX,
        "placeholder_runtime_token": foreign_item_placeholder_runtime_token,
        "foreign_item_count": len(foreign_item_placements),
        "foreign_items": [asdict(placement) for placement in foreign_item_placements],
    }
    placement_summary = {
        "archive_name": archive_name,
        "kh3_slot": slot,
        "kh3_player_name": slot_name,
        "location_count": len(placement_entries),
        "local_item_count": sum(1 for entry in placement_entries if not entry["is_foreign_item"]),
        "foreign_item_count": sum(1 for entry in placement_entries if entry["is_foreign_item"]),
        "chrinit_injection": chrinit_injection,
        "fixed_sora_chrinit_abilities": fixed_sora_chrinit_abilities,
        "suppressed_always_on_events": suppressed_always_on_events,
        "suppressed_default_base_chrinit_abilities": suppressed_default_base_chrinit_abilities,
        "level_up_limit": level_up_limit,
        "suppressed_level_up_path_rewards": suppressed_level_up_path_rewards,
        "lucky_emblem_limit": lucky_emblem_limit,
        "lucky_emblems_per_check": lucky_mark_batch_rows["batch_size"],
        "lucky_mark_batch_rows": lucky_mark_batch_rows,
        "suppressed_lucky_emblem_limit_rewards": suppressed_lucky_emblem_limit_rewards,
        "event_reward_overrides": event_reward_overrides,
        "locations": placement_entries,
        "hint_location_count": len(hint_locations),
        "hint_locations": hint_locations,
    }

    return ExportResult(
        randomized_options=default_options,
        foreign_item_manifest=foreign_item_manifest,
        placement_summary=placement_summary,
    )


def export_randomized_options(args: argparse.Namespace) -> ExportResult:
    default_options = load_default_kh3(args.default_kh3, args.default_kh3_overrides)
    suppressed_level_up_path_rewards = suppress_level_up_path_rewards(default_options)
    suppressed_always_on_events = suppress_always_on_event_rewards(default_options)
    extracted_data = load_json(args.extracted_data)
    multidata = load_multidata(args.ap_zip, args.runtime_root)
    slot = resolve_kh3_slot(multidata, args.slot)

    bridge_locations = require_bridge_locations(multidata, slot)
    slot_data_entry = multidata["slot_data"].get(slot)
    slot_randomizer_config = (
        slot_data_entry.get("kh3_randomizer")
        if isinstance(slot_data_entry, dict)
        else None
    )
    if multidata.get("race_mode") or (isinstance(slot_data_entry, dict) and slot_data_entry.get("race_mode")):
        return export_randomized_options_from_bridge_data(
            default_kh3=args.default_kh3,
            default_kh3_overrides=args.default_kh3_overrides,
            extracted_data_path=args.extracted_data,
            bridge_locations=bridge_locations,
            slot=slot,
            slot_name=get_slot_name(multidata, slot),
            archive_name=args.ap_zip.name,
            randomizer_config=slot_randomizer_config,
            chrinit_sora_have_ability=args.chrinit_sora_have_ability,
            event_reward_override=args.event_reward_override,
            race_mode=True,
        )
    suppressed_default_base_chrinit_abilities = suppress_default_base_chrinit_abilities(
        default_options,
        slot_randomizer_config,
    )
    location_assignments = multidata["locations"].get(slot)
    if location_assignments is None:
        raise ArchiveValidationError(f"Archive locations mapping is missing KH3 slot {slot}.")
    location_id_to_assignment = dict(location_assignments.items()) if hasattr(location_assignments, "items") else dict(location_assignments)
    game_item_id_to_name = build_game_item_id_to_name(multidata)
    kh3_item_id_to_name = game_item_id_to_name.get("Kingdom Hearts III")
    if kh3_item_id_to_name is None:
        raise ArchiveValidationError("Archive datapackage is missing Kingdom Hearts III item metadata.")
    item_name_to_token = build_item_name_to_token(extracted_data)
    extracted_location_map = build_extracted_location_map(extracted_data)
    foreign_item_placeholder_runtime_token = item_name_to_token.get(
        FOREIGN_ITEM_PLACEHOLDER_NAME,
        FOREIGN_ITEM_PLACEHOLDER_RUNTIME_TOKEN,
    )
    if foreign_item_placeholder_runtime_token is None:
        raise RuntimeError(
            f"Required runtime placeholder item '{FOREIGN_ITEM_PLACEHOLDER_NAME}' is missing from extracted KH3 item data."
        )

    unsupported_items: dict[str, set[str]] = {}
    unsupported_assignments: list[str] = []
    foreign_item_placements: list[ForeignItemPlacement] = []
    placement_entries: list[dict[str, Any]] = []
    kh3_player_name = get_slot_name(multidata, slot)
    lucky_mark_native_item_rows = LuckyMarkNativeItemRowAllocator()
    chrinit_injection = apply_chrinit_sora_have_ability_injection(
        default_options,
        args.chrinit_sora_have_ability,
        item_name_to_token,
    )
    fixed_sora_chrinit_abilities = enforce_fixed_sora_chrinit_abilities(default_options)
    lucky_mark_batch_rows = configure_lucky_mark_batch_rows(
        default_options,
        slot_randomizer_config,
        bridge_locations,
        seed_material=args.ap_zip.name,
    )

    for bridge_location in bridge_locations:
        ap_location_id = bridge_location["ap_location_id"]
        if ap_location_id not in location_id_to_assignment:
            raise KeyError(f"AP location id {ap_location_id} missing from archive assignments.")

        item_id, player, item_classification = location_id_to_assignment[ap_location_id]
        recipient_name = get_slot_name(multidata, player)
        assigned_item_name: str
        assigned_item_game: str
        runtime_token: str
        is_foreign_item = player != slot
        bridge_delivery_required = bool(bridge_location.get("bridge_delivery_required"))
        if player != slot:
            target_game = get_slot_game(multidata, player)
            foreign_item_name = None
            if target_game is not None:
                foreign_item_name = game_item_id_to_name.get(target_game, {}).get(item_id)
            if foreign_item_name is None:
                unsupported_assignments.append(
                    f"{bridge_location['ap_location_name']} assigned item id {item_id} for slot {player} ({target_game or 'unknown game'}); could not resolve item name."
                )
                continue

            token = build_foreign_item_placeholder_token(ap_location_id)
            runtime_token = foreign_item_placeholder_runtime_token
            assigned_item_name = foreign_item_name
            assigned_item_game = target_game or "Unknown"
            item_display_name = build_archipelago_item_display_name(
                assigned_item_name,
                recipient_name,
                is_foreign_item,
            )
            foreign_item_placements.append(
                ForeignItemPlacement(
                    ap_location_id=ap_location_id,
                    ap_location_name=bridge_location["ap_location_name"],
                    location_type=bridge_location["type"],
                    source_table=bridge_location["source_table"],
                    source_id=bridge_location["source_id"],
                    placeholder_item_name=FOREIGN_ITEM_PLACEHOLDER_NAME,
                    placeholder_token=token,
                    placeholder_runtime_token=foreign_item_placeholder_runtime_token,
                    item_id=item_id,
                    item_name=foreign_item_name,
                    item_display_name=item_display_name,
                    item_game=target_game or "Unknown",
                    recipient_slot=player,
                    recipient_name=recipient_name,
                )
            )
        else:
            item_name = kh3_item_id_to_name.get(item_id)
            if item_name is None:
                unsupported_assignments.append(
                    f"{bridge_location['ap_location_name']} uses item id {item_id} not found in KH3 datapackage."
                )
                continue

            bridge_runtime_token = bridge_only_item_runtime_token(item_name)
            if bridge_runtime_token is not None:
                token = bridge_delivered_local_reward_token(bridge_location["type"], ap_location_id)
                runtime_token = bridge_runtime_token
                bridge_delivery_required = True
            else:
                token = item_name_to_token.get(item_name)
                if token is None:
                    unsupported_items.setdefault(item_name, set()).add(bridge_location["ap_location_name"])
                    continue
                runtime_token = token
                if local_seeded_reward_requires_bridge_delivery(
                    bridge_location.get("bridge_key"),
                    bridge_location["type"],
                    runtime_token,
                    persistent_chests=bridge_location.get("chest_persistence_version") == 1,
                ):
                    token = bridge_delivered_local_reward_token(bridge_location["type"], ap_location_id)
                    bridge_delivery_required = True
            assigned_item_name = item_name
            assigned_item_game = "Kingdom Hearts III"

        item_display_name = build_archipelago_item_display_name(
            assigned_item_name,
            recipient_name,
            is_foreign_item,
        )
        location_type = bridge_location["type"]
        export_token = token
        if location_type == "synthesis":
            # Completion flags supply the one-time AP check; crafting remains native.
            bridge_delivery_required = True
        if location_type == "shop":
            export_token = str(
                bridge_location.get("shop_item_token")
                or build_foreign_item_placeholder_token(ap_location_id)
            )
            bridge_delivery_required = True
        if location_type == "lucky_emblem" and (is_foreign_item or bridge_delivery_required):
            export_token = build_lucky_mark_foreign_item_native_token(
                ap_location_id,
                native_item_row_name=lucky_mark_native_item_rows.row_name_for(ap_location_id),
            )
        native_reward_token = (
            native_reward_token_for_foreign_location(location_type, export_token)
            if is_foreign_item
            else export_token
        )
        suppress_vbonus_reward = (
            location_type == "vbonus"
            and (is_foreign_item or bridge_delivery_required)
        )

        if location_type == "treasure":
            if bridge_location.get("chest_persistence_version") == 1:
                native_reward_token = SUPPRESSED_ALWAYS_ON_EVENT_TOKEN
            apply_treasure_override(default_options, bridge_location, native_reward_token)
        elif location_type == "event":
            apply_event_override(default_options, bridge_location, native_reward_token)
        elif location_type == "lucky_emblem":
            apply_lucky_mark_override(default_options, bridge_location, native_reward_token)
        elif location_type == "synthesis":
            pass
        elif location_type == "keyblade_upgrade":
            default_options["WeaponEnhance"][bridge_location["source_id"]]["Ability"] = "ETresAbilityKind::NONE\0"
        elif location_type == "photo_mission":
            pass
        elif location_type == "level_up":
            # Level checks report through AP; their placed reward is delivered
            # by the bridge and is not constrained by the native LevelUp DT.
            pass
        elif location_type == "vbonus":
            extracted_location = extracted_location_map[LocationKey(bridge_location["source_table"], bridge_location["source_id"])]
            apply_vbonus_override(
                default_options,
                bridge_location,
                extracted_location,
                native_reward_token,
                suppress_reward=suppress_vbonus_reward,
            )
        elif location_type == "shop":
            # Shop purchases are represented by their seed-unique item row;
            # no existing vanilla reward table needs an override here.
            pass
        elif location_type in BRIDGE_ONLY_LOCATION_TYPES:
            # Completion is reported by the runtime or client bridge. Synthetic
            # checks have no seed-pak row to edit; AP delivers local items.
            pass
        else:
            raise ValueError(f"Unsupported bridge location type: {location_type}")

        if bridge_delivery_required:
            bridge_location["bridge_delivery_required"] = True

        placement_entries.append(
            {
                "ap_location_id": ap_location_id,
                "ap_location_name": bridge_location["ap_location_name"],
                "bridge_key": bridge_location["bridge_key"],
                "location_type": location_type,
                "source_table": bridge_location["source_table"],
                "source_id": bridge_location["source_id"],
                "world": bridge_location.get("world"),
                "location_description": bridge_location.get("location_description"),
                "location_game": "Kingdom Hearts III",
                "location_player_slot": slot,
                "location_player_name": kh3_player_name,
                "is_remote_location": False,
                "item_id": item_id,
                "item_classification": int(item_classification),
                "item_name": assigned_item_name,
                "item_display_name": item_display_name,
                "item_game": assigned_item_game,
                "recipient_slot": player,
                "recipient_name": recipient_name,
                "is_foreign_item": is_foreign_item,
                "export_token": export_token,
                "native_reward_token": native_reward_token,
                "runtime_token": runtime_token,
                "bridge_delivery_required": bridge_delivery_required,
                **({
                    "shop_item_token": export_token,
                    "shop_price": int(bridge_location.get("shop_price") or 0),
                } if location_type == "shop" else {}),
            }
        )

    if unsupported_assignments:
        message = "\n".join(unsupported_assignments[:20])
        raise RuntimeError(
            "Archive contains assignments that could not be resolved into KH3 local items or foreign-item placeholders.\n"
            f"{message}"
        )

    if unsupported_items:
        lines = []
        for item_name, locations in sorted(unsupported_items.items()):
            sample_locations = ", ".join(sorted(locations)[:3])
            lines.append(f"{item_name}: {sample_locations}")
        raise RuntimeError(
            "Archive contains item names that cannot be mapped into KH3 reward tokens.\n"
            + "\n".join(lines)
        )

    event_reward_overrides = apply_event_reward_overrides(
        default_options,
        args.event_reward_override,
        item_name_to_token,
    )
    level_up_limit = read_level_up_limit_for_export(slot_randomizer_config)
    lucky_emblem_limit = read_lucky_emblem_limit_for_export(slot_randomizer_config, bridge_locations)
    lucky_mark_bridge_source_ids = {
        str(location.get("source_id"))
        for location in bridge_locations
        if location.get("source_table") == "LuckyMark"
    }
    suppressed_lucky_emblem_limit_rewards = suppress_lucky_mark_rewards_above_limit(
        default_options,
        lucky_emblem_limit,
        lucky_mark_bridge_source_ids,
        seed_material=args.ap_zip.name,
    )
    remote_hint_placements = build_remote_hint_placements_from_multidata(
        multidata,
        slot,
        kh3_item_id_to_name,
    )
    hint_locations = build_hint_location_entries(
        placement_entries,
        slot,
        remote_hint_placements,
    )

    foreign_item_manifest = {
        "archive_name": args.ap_zip.name,
        "kh3_slot": slot,
        "kh3_player_name": kh3_player_name,
        "placeholder_item_name": FOREIGN_ITEM_PLACEHOLDER_NAME,
        "placeholder_token": FOREIGN_ITEM_PLACEHOLDER_TOKEN,
        "legacy_placeholder_token": FOREIGN_ITEM_PLACEHOLDER_TOKEN,
        "placeholder_token_prefix": FOREIGN_ITEM_PLACEHOLDER_TOKEN_PREFIX,
        "placeholder_runtime_token": foreign_item_placeholder_runtime_token,
        "foreign_item_count": len(foreign_item_placements),
        "foreign_items": [asdict(placement) for placement in foreign_item_placements],
    }
    placement_summary = {
        "archive_name": args.ap_zip.name,
        "kh3_slot": slot,
        "kh3_player_name": kh3_player_name,
        "location_count": len(placement_entries),
        "local_item_count": sum(1 for entry in placement_entries if not entry["is_foreign_item"]),
        "foreign_item_count": sum(1 for entry in placement_entries if entry["is_foreign_item"]),
        "chrinit_injection": chrinit_injection,
        "fixed_sora_chrinit_abilities": fixed_sora_chrinit_abilities,
        "suppressed_always_on_events": suppressed_always_on_events,
        "suppressed_default_base_chrinit_abilities": suppressed_default_base_chrinit_abilities,
        "level_up_limit": level_up_limit,
        "suppressed_level_up_path_rewards": suppressed_level_up_path_rewards,
        "lucky_emblem_limit": lucky_emblem_limit,
        "lucky_emblems_per_check": lucky_mark_batch_rows["batch_size"],
        "lucky_mark_batch_rows": lucky_mark_batch_rows,
        "suppressed_lucky_emblem_limit_rewards": suppressed_lucky_emblem_limit_rewards,
        "event_reward_overrides": event_reward_overrides,
        "locations": placement_entries,
        "hint_location_count": len(hint_locations),
        "hint_locations": hint_locations,
    }

    return ExportResult(
        randomized_options=default_options,
        foreign_item_manifest=foreign_item_manifest,
        placement_summary=placement_summary,
    )


def main() -> int:
    args = parse_args()
    result = export_randomized_options(args)
    foreign_item_manifest_path = args.foreign_item_manifest
    if foreign_item_manifest_path is None:
        foreign_item_manifest_path = args.output.with_name(f"{args.output.stem}.foreign-items.json")
    placement_summary_path = args.placement_summary
    if placement_summary_path is None:
        placement_summary_path = args.output.with_name(f"{args.output.stem}.placement-summary.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result.randomized_options, indent=2), encoding="utf-8")
    foreign_item_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    foreign_item_manifest_path.write_text(json.dumps(result.foreign_item_manifest, indent=2), encoding="utf-8")
    placement_summary_path.parent.mkdir(parents=True, exist_ok=True)
    placement_summary_path.write_text(json.dumps(result.placement_summary, indent=2), encoding="utf-8")
    print(f"Wrote randomized options to {args.output}")
    print(f"Wrote foreign-item manifest to {foreign_item_manifest_path}")
    print(f"Wrote placement summary to {placement_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
