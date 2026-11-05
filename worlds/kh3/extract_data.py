from __future__ import annotations

import json
from pathlib import Path

WORLD_ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = WORLD_ROOT / "data" / "extracted_locations.json"
DESCRIPTIONS_PATH = WORLD_ROOT / "data" / "source_descriptions.json"
SOURCE_ROOT = WORLD_ROOT / "randomizer"
DEFAULT_KH3_PATH = SOURCE_ROOT / "wwwroot" / "DefaultKH3.json"
DEFAULT_KH3_OVERRIDE_PATH = SOURCE_ROOT / "wwwroot" / "DefaultKH3.ArchipelagoOverrides.json"


WORLD_TREASURE_TABLES = {
    "Olympus": "TreasureHE",
    "Twilight Town": "TreasureTT",
    "Toy Box": "TreasureTS",
    "Kingdom of Corona": "TreasureRA",
    "Monstropolis": "TreasureMI",
    "Arendelle": "TreasureFZ",
    "The Caribbean": "TreasureCA",
    "San Fransokyo": "TreasureBX",
    "Keyblade Graveyard": "TreasureKG",
    "The Final World": "TreasureEW",
    "Re Mind": "TreasureBT",
}

WORLD_EVENT_IDS = {
    "Olympus": ["EVENT_001", "EVENT_002", "EVENT_003", "EVENT_004", "EVENT_005", "EVENT_006", "EVENT_007", "EVENT_KEYBLADE_001"],
    "Twilight Town": ["EVENT_KEYBLADE_002", "EVENT_CKGAME_001"],
    "Toy Box": ["EVENT_KEYBLADE_003", "EVENT_HEARTBINDER_002"],
    "Kingdom of Corona": ["EVENT_KEYBLADE_004"],
    "Monstropolis": ["EVENT_008", "EVENT_KEYBLADE_005", "EVENT_HEARTBINDER_003"],
    "100 Acre Wood": ["EVENT_KEYBLADE_006"],
    "Arendelle": ["EVENT_KEYBLADE_007"],
    "San Fransokyo": ["EVENT_009", "EVENT_KEYBLADE_009", "EVENT_HEARTBINDER_004", "EVENT_KEYITEM_002"],
    "The Caribbean": ["EVENT_KEYBLADE_008"],
    "Keyblade Graveyard": ["EVENT_KEYBLADE_011"],
    "Unreality": ["EVENT_KEYITEM_005", "EVENT_YOZORA_001"],
}

FRIENDLY_EVENT_LOCATION_IDS = frozenset({
    "EVENT_001",
    "EVENT_002",
    "EVENT_003",
    "EVENT_004",
    "EVENT_005",
    "EVENT_006",
    "EVENT_007",
})

WORLD_VBONUS_IDS = {
    "Olympus": ["Vbonus_001", "Vbonus_002", "Vbonus_004", "Vbonus_005", "Vbonus_006", "Vbonus_007", "Vbonus_008", "Vbonus_009", "Vbonus_010", "Vbonus_011", "Vbonus_013", "Vbonus_082"],
    "Twilight Town": ["Vbonus_014", "Vbonus_015", "Vbonus_016"],
    "Toy Box": ["Vbonus_017", "Vbonus_018", "Vbonus_019", "Vbonus_020", "Vbonus_021", "Vbonus_022", "Vbonus_023"],
    "Kingdom of Corona": ["Vbonus_024", "Vbonus_025", "Vbonus_026", "Vbonus_027", "Vbonus_028", "Vbonus_029", "Vbonus_030"],
    "Monstropolis": ["Vbonus_032", "Vbonus_033", "Vbonus_034", "Vbonus_035", "Vbonus_036", "Vbonus_037", "Vbonus_038", "Vbonus_039", "Vbonus_040"],
    "Arendelle": ["Vbonus_041", "Vbonus_042", "Vbonus_043", "Vbonus_044", "Vbonus_045", "Vbonus_047", "Vbonus_048", "Vbonus_049", "Vbonus_050"],
    "San Fransokyo": ["Vbonus_051", "Vbonus_052", "Vbonus_053", "Vbonus_054", "Vbonus_055", "Vbonus_056", "Vbonus_057"],
    "The Caribbean": ["Vbonus_058", "Vbonus_059", "Vbonus_060", "Vbonus_061", "Vbonus_062", "Vbonus_063", "Vbonus_064", "Vbonus_065", "Vbonus_066"],
    "Keyblade Graveyard": ["Vbonus_068", "Vbonus_069", "Vbonus_070", "Vbonus_071", "Vbonus_072", "Vbonus_073", "Vbonus_074", "Vbonus_075", "Vbonus_076", "Vbonus_083", "Vbonus_084"],
    "Re Mind": ["VBonus_DLC_001", "VBonus_DLC_002", "VBonus_DLC_003", "VBonus_DLC_004", "VBonus_DLC_005", "VBonus_DLC_006", "VBonus_DLC_007", "VBonus_DLC_008", "VBonus_DLC_009", "VBonus_DLC_010", "VBonus_DLC_011", "VBonus_DLC_012", "VBonus_DLC_013", "VBonus_DLC_014", "VBonus_DLC_015"],
    "Dark World": ["Vbonus_067"],
}

# Keep these rows in the extracted location catalog to reserve their stable AP
# IDs, while excluding them from generated worlds via the ap_enabled marker.
DISABLED_AP_VBONUS_IDS = frozenset({"Vbonus_004"})

DATA_BATTLE_EVENT_IDS = [
    "EVENT_DATAB_001", "EVENT_DATAB_002", "EVENT_DATAB_003", "EVENT_DATAB_004", "EVENT_DATAB_005", "EVENT_DATAB_006", "EVENT_DATAB_007",
    "EVENT_DATAB_008", "EVENT_DATAB_009", "EVENT_DATAB_010", "EVENT_DATAB_011", "EVENT_DATAB_012", "EVENT_DATAB_013",
]

BATTLEGATE_REPORT_IDS = [
    "EVENT_REPORT_001a", "EVENT_REPORT_002a", "EVENT_REPORT_003a", "EVENT_REPORT_004a", "EVENT_REPORT_005a", "EVENT_REPORT_006a", "EVENT_REPORT_007a",
    "EVENT_REPORT_008a", "EVENT_REPORT_009a", "EVENT_REPORT_010a", "EVENT_REPORT_011a", "EVENT_REPORT_012a", "EVENT_REPORT_013a",
]

BATTLEGATE_REWARD_IDS = [
    "EVENT_REPORT_001b", "EVENT_REPORT_002b", "EVENT_REPORT_003b", "EVENT_REPORT_004b", "EVENT_REPORT_005b", "EVENT_REPORT_006b", "EVENT_REPORT_007b",
    "EVENT_REPORT_008b", "EVENT_REPORT_009b", "EVENT_REPORT_010b", "EVENT_REPORT_011b", "EVENT_REPORT_012b", "EVENT_REPORT_013b", "EVENT_REPORT_014",
]

ALWAYS_ON_EVENT_IDS = [
    "EVENT_KEYBLADE_012", "EVENT_KEYBLADE_013", "EVENT_HEARTBINDER_001", "EVENT_KEYITEM_001", "EVENT_KEYITEM_003", "EVENT_KEYITEM_004",
]

IMPORTANT_ITEM_IDS = {
    "WEP_KEYBLADE_SO_01",
    "WEP_KEYBLADE_SO_02",
    "WEP_KEYBLADE_SO_03",
    "WEP_KEYBLADE_SO_04",
    "WEP_KEYBLADE_SO_05",
    "WEP_KEYBLADE_SO_06",
    "WEP_KEYBLADE_SO_08",
    "WEP_KEYBLADE_SO_09",
    "WEP_KEYBLADE_SO_013",
    "WEP_KEYBLADE_SO_014",
    "WEP_KEYBLADE_SO_015",
    "WEP_KEYBLADE_SO_018",
    "KEY_ITEM14",
    "KEY_ITEM15",
    "KEY_ITEM16",
    "KEY_ITEM06",
    "KEY_ITEM07",
    "KEY_ITEM08",
    "KEY_ITEM09",
    "KEY_ITEM10",
    "REPORT_ITEM02",
    "REPORT_ITEM03",
    "REPORT_ITEM04",
    "REPORT_ITEM05",
    "REPORT_ITEM06",
    "REPORT_ITEM07",
    "REPORT_ITEM08",
    "REPORT_ITEM09",
    "REPORT_ITEM10",
    "REPORT_ITEM11",
    "REPORT_ITEM12",
    "REPORT_ITEM13",
    "REPORT_ITEM14",
    "ETresAbilityKind::REFLECT_GUARD",
    "ETresAbilityKind::DODGE",
    "ETresAbilityKind::AIRSLIDE",
    "ETresAbilityKind::POLE_SPIN",
    "ETresAbilityKind::DOUBLEFLIGHT",
    "ETresAbilityKind::LAST_LEAVE",
    "ETresAbilityKind::COMBO_LEAVE",
    "ETresAbilityKind::AIR_RECOVERY",
    "MAGIC_FIRE",
    "MAGIC_BLIZZARD",
    "MAGIC_THUNDER",
    "MAGIC_WATER",
    "MAGIC_AERO",
    "MAGIC_CURE",
}


def load_source_descriptions() -> dict:
    catalog = json.loads(DESCRIPTIONS_PATH.read_text(encoding="utf-8"))
    if catalog.get("format") != "kh3_source_descriptions" or catalog.get("format_version") != 1:
        raise ValueError(f"Unsupported KH3 source description catalog: {DESCRIPTIONS_PATH}")
    return catalog


def deep_merge_dict(target: dict, overrides: dict) -> dict:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge_dict(target[key], value)
        else:
            target[key] = value
    return target


def load_default_kh3() -> dict:
    defaults = json.loads(DEFAULT_KH3_PATH.read_text(encoding="utf-8"))
    if DEFAULT_KH3_OVERRIDE_PATH.exists():
        deep_merge_dict(defaults, json.loads(DEFAULT_KH3_OVERRIDE_PATH.read_text(encoding="utf-8")))
    return defaults


def chest_display_name(chest_id: str) -> str:
    prefix, chest_type, index = chest_id.split("_")
    kind = "Large Chest" if chest_type == "LBOX" else "Small Chest"
    return f"{kind} {int(index)}"


def vbonus_display_name(vbonus_id: str) -> str:
    parts = vbonus_id.split("_")
    if len(parts) == 3:
        return f"Victory Bonus {parts[1]} {int(parts[2])}"
    if "Minigame" in parts[1]:
        return f"Victory Bonus {parts[1]}"
    return f"Victory Bonus {parts[1].replace('DLC', 'DLC ').strip()}"


def level_up_display_name(level: str) -> str:
    return f"Sora Level {int(level)}"


def event_location_name(world: str, event_id: str) -> str:
    return f"{world} - {event_id}"


def build_items(defaults: dict, display_names: dict[str, str]) -> list[dict]:
    values: set[str] = set()
    for category in defaults.values():
        for subcategory in category.values():
            for value in subcategory.values():
                values.add(value.replace("\u0000", ""))

    for item_id in IMPORTANT_ITEM_IDS:
        values.add(item_id)

    items = []
    for value in sorted(values):
        items.append({
            "id": value,
            "name": display_names.get(value, value),
            "important": value in IMPORTANT_ITEM_IDS or display_names.get(value, "").startswith(("Proof of", "Secret Report", "Ability:")),
        })
    return items


def build_runtime_flag_rows_by_token(defaults: dict) -> dict[str, list[str]]:
    token_to_rows: dict[str, set[str]] = {}
    for category in defaults.values():
        if not isinstance(category, dict):
            continue
        for row_name, payload in category.items():
            if not isinstance(row_name, str) or not row_name.startswith("GIVESORA_"):
                continue
            if not isinstance(payload, dict):
                continue
            for field_name, value in payload.items():
                if not field_name.startswith("Sora_"):
                    continue
                runtime_token = str(value).replace("\u0000", "")
                if not runtime_token or runtime_token.endswith("::NONE"):
                    continue
                token_to_rows.setdefault(runtime_token, set()).add(row_name)

    return {
        token: sorted(row_names)
        for token, row_names in sorted(token_to_rows.items())
    }


def build_locations(
    defaults: dict,
    display_names: dict[str, str],
    key_display_names: dict[str, str],
    key_descriptions: dict[str, str],
    chest_location_descriptions: dict[str, dict[str, str]],
) -> list[dict]:
    locations: list[dict] = []

    for world_name, table_name in WORLD_TREASURE_TABLES.items():
        table = defaults.get(table_name, {})
        for chest_id, payload in sorted(table.items()):
            reward_id = payload["Treasure"].replace("\u0000", "")
            display_name = chest_display_name(chest_id)
            location_description = chest_location_descriptions.get(table_name, {}).get(display_name)
            locations.append({
                "name": f"{world_name} - {display_name}",
                "type": "treasure",
                "world": world_name,
                "source_table": table_name,
                "source_id": chest_id,
                **({"location_description": location_description} if location_description else {}),
                "vanilla_item": reward_id,
                "vanilla_item_name": display_names.get(reward_id, reward_id),
            })

    event_table = defaults.get("Event", {})
    for world_name, event_ids in WORLD_EVENT_IDS.items():
        for event_id in event_ids:
            payload = event_table.get(event_id)
            if not payload:
                continue
            location_description = (
                key_display_names.get(event_id)
                if event_id in FRIENDLY_EVENT_LOCATION_IDS
                else None
            )
            for category_name, reward in sorted(payload.items()):
                reward_id = reward.replace("\u0000", "")
                locations.append({
                    "name": f"{event_location_name(world_name, event_id)} - {category_name}",
                    "type": "event",
                    "world": world_name,
                    "source_table": "Event",
                    "source_id": event_id,
                    "subtype": category_name,
                    **({"location_description": location_description} if location_description else {}),
                    "vanilla_item": reward_id,
                    "vanilla_item_name": display_names.get(reward_id, reward_id),
                })

    for label, event_ids in {
        "Battlegate Reports": BATTLEGATE_REPORT_IDS,
        "Battlegate Rewards": BATTLEGATE_REWARD_IDS,
        "Data Battles": DATA_BATTLE_EVENT_IDS,
        "Always On": ALWAYS_ON_EVENT_IDS,
    }.items():
        for event_id in event_ids:
            payload = event_table.get(event_id)
            if not payload:
                continue
            for category_name, reward in sorted(payload.items()):
                reward_id = reward.replace("\u0000", "")
                locations.append({
                    "name": f"{label} - {event_id} - {category_name}",
                    **({"location_description": key_display_names[event_id]}
                       if label in {"Battlegate Reports", "Battlegate Rewards"} else {}),
                    "type": "event",
                    "world": label,
                    "source_table": "Event",
                    "source_id": event_id,
                    "subtype": category_name,
                    "vanilla_item": reward_id,
                    "vanilla_item_name": display_names.get(reward_id, reward_id),
                })

    vbonus_table = defaults.get("VBonus", {})
    for world_name, vbonus_ids in WORLD_VBONUS_IDS.items():
        for vbonus_id in vbonus_ids:
            payload = vbonus_table.get(vbonus_id)
            if not payload:
                continue
            location_description = key_descriptions.get(vbonus_id)
            locations.append({
                "name": f"{world_name} - {vbonus_display_name(vbonus_id)}",
                "type": "vbonus",
                "world": world_name,
                "source_table": "VBonus",
                "source_id": vbonus_id,
                **({"ap_enabled": False} if vbonus_id in DISABLED_AP_VBONUS_IDS else {}),
                **({"location_description": location_description} if location_description else {}),
                "vanilla_items": {
                    key: value.replace("\u0000", "")
                    for key, value in sorted(payload.items())
                },
                "vanilla_item_names": {
                    key: display_names.get(value.replace("\u0000", ""), value.replace("\u0000", ""))
                    for key, value in sorted(payload.items())
                },
            })

    lucky_mark_table = defaults.get("LuckyMark", {})
    for milestone, payload in sorted(lucky_mark_table.items(), key=lambda entry: int(entry[0])):
        reward_id = payload["Reward"].replace("\u0000", "")
        locations.append({
            "name": f"Lucky Emblems - Milestone {milestone}",
            "type": "lucky_emblem",
            "world": "Lucky Emblems",
            "source_table": "LuckyMark",
            "source_id": milestone,
            "vanilla_item": reward_id,
            "vanilla_item_name": display_names.get(reward_id, reward_id),
        })

    level_up_table = defaults.get("LevelUp", {})
    for level, payload in sorted(level_up_table.items(), key=lambda entry: int(entry[0])):
        locations.append({
            "name": f"Level Ups - {level_up_display_name(level)}",
            "type": "level_up",
            "world": "Level Ups",
            "source_table": "LevelUp",
            "source_id": level,
            "vanilla_items": {
                key: value.replace("\u0000", "")
                for key, value in sorted(payload.items())
            },
            "vanilla_item_names": {
                key: display_names.get(value.replace("\u0000", ""), value.replace("\u0000", ""))
                for key, value in sorted(payload.items())
            },
        })

    # Keep this fixed-capacity catalog at the end of the location list. That
    # preserves every published KH3 location ID while allowing the generation
    # option to expose only the requested prefix of Buy-menu checks.
    for shop_index in range(1, 21):
        locations.append({
            "name": f"Moogle Shop - Archipelago Item {shop_index}",
            "location_description": f"Moogle Shop - Archipelago Purchase {shop_index}",
            "type": "shop",
            "world": "Moogle Shop",
            "source_table": "MoogleShop",
            "source_id": f"AP_ITEM_{shop_index:02d}",
            "shop_index": shop_index,
        })

    return locations


def main() -> None:
    defaults = load_default_kh3()
    descriptions = load_source_descriptions()
    display_names = descriptions["value_display_names"]
    key_display_names = descriptions["key_display_names"]
    key_descriptions = descriptions["key_descriptions"]
    chest_location_descriptions = descriptions["chest_location_descriptions"]
    locations = build_locations(
        defaults,
        display_names,
        key_display_names,
        key_descriptions,
        chest_location_descriptions,
    )
    data = {
        "meta": {
            "source_default_kh3": DEFAULT_KH3_PATH.relative_to(WORLD_ROOT).as_posix(),
            "source_descriptions": DESCRIPTIONS_PATH.relative_to(WORLD_ROOT).as_posix(),
            "runtime_flag_rows_by_token": build_runtime_flag_rows_by_token(defaults),
        },
        "items": build_items(defaults, display_names),
        "locations": locations,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Items: {len(data['items'])}")
    print(f"Locations: {len(data['locations'])}")


if __name__ == "__main__":
    main()
