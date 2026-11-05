from BaseClasses import Location
from .CharacterUnlocks import ALL_CHARACTER_UNLOCK_LOCATIONS

from .ColiseumLogic import COLISEUM_POSTGAME_CUP_IDS
from .Data import is_always_on_location, load_extracted_data, normalize_locations
from .Ingredients import INGREDIENT_SPOT_DISPLAY_NAMES, INGREDIENT_SPOTS_BY_ID
from .Items import HEARTBINDER_ITEM_NAMES
from .kh02.locations import ALL_KH02_LOCATIONS, KH02_WORLD
from .MelodyOfMemory import (
    MOM_LOCATION_ID_BASE,
    MOM_SONGS,
    MUSIC_CHEST_BONUS_ID_OFFSET,
    MUSIC_CHEST_PLACEMENTS,
    music_chest_bonus_name,
    song_location_name,
)
from .Space import SPACE_LOCATION_IDS, SPACE_LOCATIONS


class KH3Location(Location):
    game = "Kingdom Hearts III"
    filler_only = False
    progression_allowed = True


BASE_LOCATION_ID = 0x4B483300
COMPLETION_LOCATION_NAME = "Defeat Master Xehanort"
ROCK_TITAN_VBONUS_BRIDGE_KEY = "VBonus:Vbonus_010"
ROCK_TITAN_VBONUS_SOURCE_KEY = ("VBonus", "Vbonus_010")
ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME = "Olympus - Rock Titan Sequence Break Bonus"
ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID = BASE_LOCATION_ID + 0xFFFF
ROCK_TITAN_BONUS_BRIDGE_LOCATION = {
    "bridge_key": "Conditional:RockTitanSequenceBreak", "source_table": "Conditional",
    "source_id": "RockTitanSequenceBreak", "type": "sequence_break_bonus", "world": "Olympus",
    "ap_location_id": ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
    "ap_location_name": ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
    "bridge_delivery_required": True,
}

# Each existing VBonus has two permanently named and addressed extra rewards.
EXTRA_VBONUS_LOCATION_ID_BASE = BASE_LOCATION_ID + 0x10000
EXTRA_VBONUS_REWARD_NUMBERS = (2, 3)
_NORMALIZED_LOCATIONS = normalize_locations(load_extracted_data())


def extra_vbonus_location_name(parent_name: str, reward_number: int) -> str:
    if reward_number not in EXTRA_VBONUS_REWARD_NUMBERS:
        raise ValueError(f"Invalid extra victory bonus reward number: {reward_number}")
    return f"{parent_name} - Reward {reward_number}"


def _build_location_name_to_id() -> dict[str, int]:
    names = [location["name"] for location in _NORMALIZED_LOCATIONS]
    return {
        name: BASE_LOCATION_ID + index
        for index, name in enumerate(names)
    }


location_name_to_id = {
    **{row["name"]: row["ap_location_id"] for row in ALL_CHARACTER_UNLOCK_LOCATIONS},
    **{row["name"]: row["ap_location_id"] for row in ALL_KH02_LOCATIONS},
    **SPACE_LOCATION_IDS,
    **_build_location_name_to_id(),
    **{p["name"]: p["ap_location_id"] for p in MUSIC_CHEST_PLACEMENTS},
    **{song_location_name(song): MOM_LOCATION_ID_BASE + song["song_id"] for song in MOM_SONGS},
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME: ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
}

# Universal Tracker adds these user-facing aliases beside the unique check names.
location_id_to_alias = {
    location_name_to_id[spot["name"]]: INGREDIENT_SPOT_DISPLAY_NAMES[spot["id"]]
    for spot in INGREDIENT_SPOTS_BY_ID.values()
}

location_name_to_id.update({
    extra_vbonus_location_name(row["name"], reward_number): (
        EXTRA_VBONUS_LOCATION_ID_BASE
        + 2 * (location_name_to_id[row["name"]] - BASE_LOCATION_ID)
        + reward_number - 2
    )
    for row in _NORMALIZED_LOCATIONS if row["type"] == "vbonus"
    for reward_number in EXTRA_VBONUS_REWARD_NUMBERS
})

# Register every possible parent, but instantiate only this seed's music bonuses.
location_name_to_id.update({
    music_chest_bonus_name(row["name"]): location_name_to_id[row["name"]] + MUSIC_CHEST_BONUS_ID_OFFSET
    for row in (*_NORMALIZED_LOCATIONS, *MUSIC_CHEST_PLACEMENTS)
    if row.get("type") == "treasure" or "placement_id" in row
})

# Group selection excludes synthesis and disabled ingredient checks; their
# catalog entries stay available individually and generation is unchanged.
_GROUP_LOCATIONS = [
    row for row in _NORMALIZED_LOCATIONS
    if row["type"] != "synthesis"
    and not (row["type"] == "ingredient" and row.get("ap_enabled") is False)
]

# Archipelago selects grouped checks present in the seed.
location_name_groups = {
    KH02_WORLD: {row["name"] for row in ALL_KH02_LOCATIONS},
    "0.2 Chests": {row["name"] for row in ALL_KH02_LOCATIONS if row["type"] == "kh02_chest"},
    "0.2 Bosses": {row["name"] for row in ALL_KH02_LOCATIONS if row["type"] == "kh02_boss"},
    "0.2 Challenges": {row["name"] for row in ALL_KH02_LOCATIONS if row["type"] == "kh02_objective"},
    "Heartbinders": {
        row["name"] for row in _GROUP_LOCATIONS
        if row["primary_item_name"] in HEARTBINDER_ITEM_NAMES
        and not is_always_on_location(row)
    },
    "Adversaries": {row["name"] for row in _GROUP_LOCATIONS if row["type"] == "adversary"},
    "Song Bonus": {
        music_chest_bonus_name(row["name"])
        for row in (*_GROUP_LOCATIONS, *MUSIC_CHEST_PLACEMENTS)
        if row.get("type") == "treasure" or "placement_id" in row
    },
    "Lucky Emblems": {
        row["name"] for row in _GROUP_LOCATIONS if row["type"] == "lucky_emblem"
    },
    "Cups": {
        row["name"] for row in _GROUP_LOCATIONS if row["type"] == "olympus_coliseum"
    },
    "Data Battles": {
        row["name"] for row in _GROUP_LOCATIONS if row["world"] == "Data Battles"
    },
}

# Pin story encounters by native identity rather than relying on display names.
# Includes minibosses, excludes mob waves, minigames, Re Mind and superbosses.
STORY_BOSS_SOURCE_IDS = frozenset(
    f"Vbonus_{number:03}" for number in (
        8, 10, 13, 14, 20, 21, 23, 27, 30, 40, 41, 47, 50,
        51, 54, 56, 57, 60, 61, 65, 66, 67, 68, 70, 71, 72,
        73, 74, 75, 82,
    )
)
location_name_groups["Story Bosses"] = {
    name
    for row in _GROUP_LOCATIONS
    if row["type"] == "vbonus" and row["source_id"] in STORY_BOSS_SOURCE_IDS
    for name in (row["name"], *(
        extra_vbonus_location_name(row["name"], number)
        for number in EXTRA_VBONUS_REWARD_NUMBERS
    ))
}
for group_name, postgame in (("Normal Cups", False), ("Postgame Cups", True)):
    location_name_groups[group_name] = {
        row["name"] for row in _GROUP_LOCATIONS
        if row["type"] == "olympus_coliseum"
        and (row["source_id"] in COLISEUM_POSTGAME_CUP_IDS) == postgame
    }

# Use catalog worlds, including category regions, and include every registered
# extra victory reward and music bonus in its parent's world. Ingredient type
# checks are global; Ingredients+ has a physical world in the spot catalog.
for row in (*_GROUP_LOCATIONS, *MUSIC_CHEST_PLACEMENTS, *SPACE_LOCATIONS):
    world = row["world"]
    if row.get("source_table") == "IngredientSpot":
        world = INGREDIENT_SPOTS_BY_ID[row["source_id"]]["world"]
    names = location_name_groups.setdefault(world, set())
    names.add(row["name"])
    if row.get("type") == "vbonus":
        names.update(extra_vbonus_location_name(row["name"], number)
                     for number in EXTRA_VBONUS_REWARD_NUMBERS)
    if row.get("type") == "treasure" or "placement_id" in row:
        names.add(music_chest_bonus_name(row["name"]))
location_name_groups["Olympus"].add(ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME)
