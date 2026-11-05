from __future__ import annotations

from typing import Any
from .Battlegates import BRIDGE_ONLY_BATTLEGATE_EVENT_IDS

from .Data import goal_required_item_counts, validate_locations
from .Locations import COMPLETION_LOCATION_NAME, location_name_to_id
from .Items import item_name_to_id

BRIDGE_PROTOCOL_VERSION = 1
BRIDGE_PROTOCOL_NAME = "kh3_ap_file_bridge"
KH3_WORLD_VERSION = "2.0.0"
MOOGLE_SHOP_NATIVE_ITEM_CATEGORY = 7
MOOGLE_SHOP_NATIVE_ITEM_ROW_MIN = 61
MOOGLE_SHOP_NATIVE_ITEM_ROW_MAX = 80


class BridgeProtocolError(RuntimeError):
    pass


def validate_protocol_payload(payload: dict[str, Any], source_description: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BridgeProtocolError(f"{source_description} is not a JSON object.")

    protocol_name = payload.get("protocol_name")
    protocol_version = payload.get("protocol_version")
    if protocol_name != BRIDGE_PROTOCOL_NAME:
        raise BridgeProtocolError(
            f"{source_description} uses unsupported KH3 bridge protocol name {protocol_name!r}; "
            f"expected {BRIDGE_PROTOCOL_NAME!r}."
        )
    if protocol_version != BRIDGE_PROTOCOL_VERSION:
        raise BridgeProtocolError(
            f"{source_description} uses unsupported KH3 bridge protocol version {protocol_version!r}; "
            f"expected {BRIDGE_PROTOCOL_VERSION!r}."
        )
    return payload


def validate_slot_data_payload(slot_data: dict[str, Any], source_description: str = "slot_data.json") -> dict[str, Any]:
    validate_protocol_payload(slot_data, source_description)

    bridge = slot_data.get("bridge")
    if not isinstance(bridge, dict):
        raise BridgeProtocolError(f"{source_description} is missing bridge metadata.")

    validate_protocol_payload(bridge, f"{source_description}.bridge")
    locations = bridge.get("locations")
    if not isinstance(locations, (list, tuple)):
        raise BridgeProtocolError(f"{source_description}.bridge is missing a locations list.")
    return slot_data


def make_bridge_key(location: dict) -> str:
    return f"{location['source_table']}:{location['source_id']}"


def moogle_shop_native_item_row_name(location: dict) -> str:
    """Return the reserved MATERIAL row used by KH3's native Buy-menu item id."""
    try:
        shop_index = int(location["shop_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeProtocolError(
            f"Moogle Shop location {location.get('name') or location.get('source_id')!r} "
            "is missing a numeric shop_index."
        ) from exc

    row_index = MOOGLE_SHOP_NATIVE_ITEM_ROW_MIN + shop_index - 1
    if row_index < MOOGLE_SHOP_NATIVE_ITEM_ROW_MIN or row_index > MOOGLE_SHOP_NATIVE_ITEM_ROW_MAX:
        raise BridgeProtocolError(
            f"Moogle Shop location {location.get('name') or location.get('source_id')!r} "
            f"has unsupported shop_index {shop_index}; expected 1-"
            f"{MOOGLE_SHOP_NATIVE_ITEM_ROW_MAX - MOOGLE_SHOP_NATIVE_ITEM_ROW_MIN + 1}."
        )
    return f"I{MOOGLE_SHOP_NATIVE_ITEM_CATEGORY:02d}{row_index:03d}"


def build_bridge_slot_data(
    locations: list[dict],
    heart_piece_count: int,
    *,
    worlds_required: int = 0,
    moogle_shop_price: int = 0,
    moogle_shop_token_namespace: str = "",
) -> dict[str, Any]:
    validate_locations(locations)
    required_item_counts = goal_required_item_counts(heart_piece_count)
    bridge_locations = []
    for location in locations:
        bridge_location = {
            "ap_location_id": location_name_to_id[location["name"]],
            "ap_location_name": location["name"],
            "bridge_key": make_bridge_key(location),
            "source_table": location["source_table"],
            "source_id": location["source_id"],
            "world": location["world"],
            "type": location["type"],
        }
        if location["source_table"] == "Event" and location["source_id"] in BRIDGE_ONLY_BATTLEGATE_EVENT_IDS:
            bridge_location["bridge_delivery_required"] = True
        if location["type"] == "character_unlock":
            bridge_location["bridge_delivery_required"] = True
            if location.get("required_item_names"):
                bridge_location["required_item_ids"] = [
                    item_name_to_id[name] for name in location["required_item_names"]
                ]
        if location["type"] in {"space", "olympus_coliseum", "level_up", "ingredient", "adversary", "melody_of_memory", "kh02_chest", "kh02_boss", "kh02_objective", "music_chest_bonus", "vbonus_extra", "minigame_reward", "synthesis", "photo_mission", "keyblade_upgrade"}:
            # These checks have no native item reward in the generated pak.
            # Every local assignment therefore has to use the runtime bridge.
            bridge_location["bridge_delivery_required"] = True
        for optional_key in (
            "kh02_flag",
            "kh02_challenge",
            "workshop_flag",
            "synthesis_order",
            "keyblade_name",
            "keyblade_token",
            "keyblade_level",
            "keyblade_chest_keyblade",
            "keyblade_chest_token",
            "keyblade_chest_native",
            "minigame_reward_crc",
            "minigame_reward_tokens",
            "subtype",
            "location_description",
            "vanilla_item",
            "vanilla_item_name",
            "vanilla_items",
            "vanilla_item_names",
            "classic_kingdom_check_kind",
            "mom_song_id",
            "mom_song_name",
            "mom_series",
            "mom_difficulty",
            "music_chest_kind",
            "music_chest_placement_id",
            "music_chest_placement_version",
            "music_chest_map",
            "music_chest_position",
            "music_chest_rotation",
            "music_chest_parent_location_id",
            "music_chest_parent_location_name",
            "trigger_bridge_key",
        ):
            if location["type"] == "level_up" and optional_key in {
                "vanilla_items",
                "vanilla_item_names",
            }:
                # Level-up checks are purely AP-delivered.  Do not publish the
                # retired Guardian/Warrior/Mystic route rewards in new seeds.
                continue
            if optional_key in location:
                bridge_location[optional_key] = location[optional_key]
        if location["type"] == "shop":
            token_suffix = f"_{moogle_shop_token_namespace}" if moogle_shop_token_namespace else ""
            native_row_name = moogle_shop_native_item_row_name(location)
            bridge_location.update({
                # KH3 derives the native item category and id from the numeric
                # TresItemData row name. The MATERIAL catalog reserves indexes
                # 61-99 below its compiled _MAX=100; using the first 20 avoids
                # both vanilla rows (1-60) and out-of-range CAMPITEM markers.
                # Encoding that row in the token lets the native AP resolver
                # map inventory checks to the same id.
                "shop_item_token": (
                    f"ITEM_AP_{native_row_name}_{bridge_location['ap_location_id']}{token_suffix}"
                ),
                "shop_item_row_name": native_row_name,
                "shop_price": max(0, int(moogle_shop_price)),
                "bridge_delivery_required": True,
            })
        bridge_locations.append(bridge_location)

    result = {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "heart_piece_count": heart_piece_count,
        "worlds_required": worlds_required,
        "goal_required_item_counts": dict(sorted(required_item_counts.items())),
        "completion_location_name": COMPLETION_LOCATION_NAME,
        "completion_location_id": None,
        "locations": bridge_locations,
    }
    bonuses = [row for row in bridge_locations
               if row["type"] in {"music_chest_bonus", "vbonus_extra", "character_unlock"}
               and "trigger_bridge_key" in row]
    if bonuses:
        result["conditional_checks"] = [{
            "trigger_bridge_key": row["trigger_bridge_key"],
            "ap_location_id": row["ap_location_id"],
            "ap_location_name": row["ap_location_name"],
            **({"required_item_ids": row["required_item_ids"]} if "required_item_ids" in row else {}),
        } for row in bonuses]
    return result
