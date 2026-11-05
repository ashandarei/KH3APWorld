from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from typing import Iterable

from .Ingredients import append_ingredient_locations, ingredient_spot_is_junk
from .Adversaries import append_adversary_locations
from .Battlegates import BATTLEGATE_ZERO_LOCATION
from .CharacterUnlocks import (
    CHARACTER_UNLOCK_ITEM_NAMES,
    CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME,
    CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX,
)
from .Logic import (
    LOGIC_REQUIREMENT_ITEM_NAMES,
    MAGIC_UNLOCK_ITEM,
    RequirementAlternatives,
    parse_requirement_summary,
)
from .LogicData import (
    CHEST_LOGIC_REQUIREMENTS as CHEST_LOGIC_REQUIREMENTS,
)
from .LogicData import (
    EVENT_LOGIC_REQUIREMENTS as EVENT_LOGIC_REQUIREMENTS,
)
from .LogicData import (
    INGREDIENT_LOGIC_REQUIREMENTS as INGREDIENT_LOGIC_REQUIREMENTS,
)
from .LogicData import (
    LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENTS as LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENTS,
)
from .LogicData import (
    LOCATION_LOGIC_DEPENDENCIES as LOCATION_LOGIC_DEPENDENCIES,
)
from .LogicData import (
    VICTORY_BONUS_LOGIC_REQUIREMENTS as VICTORY_BONUS_LOGIC_REQUIREMENTS,
)
from .Minigames import append_minigame_item_locations, append_minigame_locations, minigame_pool_option
from .PackageResources import read_package_text
from .Workshop import (PHOTO_MISSION_COMPLETION_WORLDS, WORKSHOP_OPTIONS,
                       append_workshop_locations, synthesis_is_junk)

DATA_PACKAGE = __package__ or "worlds.kh3"
DATA_RESOURCE = "data/extracted_locations.json"
LUCKY_EMBLEM_SPOTS_RESOURCE = "data/lucky_emblem_spots.json"

PROOF_ITEMS = {
    "Proof of Promises",
    "Proof of Times Past",
    "Proof of Fantasy",
}
SECRET_REPORT_ITEM_NAMES = tuple(f"Secret Report {number}" for number in range(1, 14))
HEART_PIECE_ITEM_NAME = "Heart Piece"
HEART_PIECE_GOAL_COUNT = 7
STYLE_CHANGE_UNLOCK_ITEM = "Unlock: Style Change"
SPELLWEAVER_UNLOCK_ITEM = "Style Change: Spellweaver"
ALL_GOAL_KEY_ITEM_NAMES = frozenset({HEART_PIECE_ITEM_NAME, *PROOF_ITEMS})


def goal_key_item_counts(heart_piece_count: int) -> dict[str, int]:
    if not 3 <= heart_piece_count <= 30:
        raise ValueError(f"Heart piece count must be 3-30, got {heart_piece_count!r}.")
    return {HEART_PIECE_ITEM_NAME: heart_piece_count}


def goal_required_item_counts(heart_piece_count: int) -> dict[str, int]:
    return goal_key_item_counts(heart_piece_count)


def goal_required_item_names(heart_piece_count: int) -> frozenset[str]:
    return frozenset(goal_required_item_counts(heart_piece_count))


PORTAL_KEYBLADE_REGION_LOCKS = {
    "Re Mind": "Kingdom Key",
    "Keyblade Graveyard": "Starlight",
    "Olympus": "Hero's Origin",
    "The Caribbean": "Wheel of Fate",
    "Arendelle": "Crystal Snow",
    "Kingdom of Corona": "Ever After",
    "Dark World": "Oblivion",
    "Radiant Garden": "Oathkeeper",
    "Data Battles": "Oathkeeper",
    "San Fransokyo": "Nano Gear",
    "Monstropolis": "Happy Gear",
    "Toy Box": "Favorite Deputy",
    "Twilight Town": "Shooting Star",
}
KEYBLADE_ENHANCEMENT_GROUPS = (
    ("Kingdom Key", 0),
    ("Shooting Star", 20),
    ("Hero's Origin", 10),
    ("Favorite Deputy", 30),
    ("Ever After", 40),
    ("Wheel of Fate", 90),
    ("Crystal Snow", 60),
    ("Hunny Spout", 70),
    ("Nano Gear", 80),
    ("Happy Gear", 50),
    ("Classic Tone", 100),
    ("Grand Chef", 110),
    ("Ultima Weapon", 120),
    ("Pandora's Power", 150),
    ("Starlight", 160),
    ("Oathkeeper", 170),
    ("Oblivion", 180),
)
# AP-owned per-keyblade style unlocks.  These use the game's normal enum names
# so the runtime probe can map the equipped weapon without depending on the
# GoA data table.
KEYBLADE_STYLE_ITEMS = {
    "Unlock Style: Kingdom Key": {"key": "WEP_KEYBLADE00", "display_name": "Kingdom Key Style"},
    "Unlock Style: Shooting Star": {"key": "WEP_KEYBLADE01", "display_name": "Shooting Star Style"},
    "Unlock Style: Hero's Origin": {"key": "WEP_KEYBLADE02", "display_name": "Hero's Origin Style"},
    "Unlock Style: Favorite Deputy": {"key": "WEP_KEYBLADE03", "display_name": "Favorite Deputy Style"},
    "Unlock Style: Ever After": {"key": "WEP_KEYBLADE04", "display_name": "Ever After Style"},
    "Unlock Style: Wheel of Fate": {"key": "WEP_KEYBLADE05", "display_name": "Wheel of Fate Style"},
    "Unlock Style: Crystal Snow": {"key": "WEP_KEYBLADE06", "display_name": "Crystal Snow Style"},
    "Unlock Style: Hunny Spout": {"key": "WEP_KEYBLADE07", "display_name": "Hunny Spout Style"},
    "Unlock Style: Nano Gear": {"key": "WEP_KEYBLADE08", "display_name": "Nano Gear Style"},
    "Unlock Style: Happy Gear": {"key": "WEP_KEYBLADE09", "display_name": "Happy Gear Style"},
    "Unlock Style: Classic Tone": {"key": "WEP_KEYBLADE10", "display_name": "Classic Tone Style"},
    "Unlock Style: Grand Chef": {"key": "WEP_KEYBLADE11", "display_name": "Grand Chef Style"},
    "Unlock Style: Oathkeeper": {"key": "WEP_KEYBLADE12", "display_name": "Oathkeeper Style"},
    "Unlock Style: Oblivion": {"key": "WEP_KEYBLADE13", "display_name": "Oblivion Style"},
    "Unlock Style: Ultima Weapon": {"key": "WEP_KEYBLADE14", "display_name": "Ultima Weapon Style"},
    "Unlock Style: Starlight": {"key": "WEP_KEYBLADE17", "display_name": "Starlight Style"},
    "Unlock Style: Pandora's Power": {"key": "WEP_KEYBLADE18", "display_name": "Pandora's Power Style"},
}
KEYBLADE_STYLE_ITEM_NAMES = tuple(KEYBLADE_STYLE_ITEMS)
KEYBLADE_STYLE_RUNTIME_TOKEN_PREFIX = "KH3AP_KEYBLADE_STYLE::"
KEYBLADE_STYLE_RUNTIME_TOKEN_BY_ITEM_NAME = {
    item_name: f"{KEYBLADE_STYLE_RUNTIME_TOKEN_PREFIX}{metadata['key']}"
    for item_name, metadata in KEYBLADE_STYLE_ITEMS.items()
}
STATIC_KEYBLADE_UPGRADE_SANITY_KEYBLADE_NAMES = frozenset({
    "Pandora's Power",
})
PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME = "Progressive Keyblade Level"
PROGRESSIVE_KEYBLADE_LEVEL_ITEM_COUNT = 10
PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN = "KH3AP_PROGRESSIVE_KEYBLADE_LEVEL"
PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_PREFIX = (
    f"{PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN}::"
)
PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_RUNTIME_TOKEN_PREFIX = (
    f"{PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_PREFIX}KEYBLADE::"
)
PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE = {
    keyblade_name: f"Progressive {keyblade_name} Level"
    for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS
}
PROGRESSIVE_KEYBLADE_LEVEL_KEYBLADE_BY_ITEM_NAME = {
    item_name: keyblade_name
    for keyblade_name, item_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE.items()
}
PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME = {
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME: PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN,
    **{
        PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[keyblade_name]: (
            f"{PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_RUNTIME_TOKEN_PREFIX}{first_row_id}"
        )
        for keyblade_name, first_row_id in KEYBLADE_ENHANCEMENT_GROUPS
    },
}
PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES = frozenset(
    PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME
)
PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES = tuple(
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[keyblade_name]
    for keyblade_name, _ in KEYBLADE_ENHANCEMENT_GROUPS
)
PROGRESSIVE_KEYBLADE_LEVEL_BASE_RUNTIME_TOKENS = frozenset(
    PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME.values()
)

BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME = {
    "Sneeze Trap": "ETresBadStatusType::TRES_BADSTAT_SNEEZE",
    "Burn Trap": "ETresBadStatusType::TRES_BADSTAT_BURN",
}
BAD_STATUS_TRAP_ITEM_NAMES = tuple(BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME)
BAD_STATUS_TRAP_RUNTIME_TOKENS = frozenset(BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.values())
KING_MICKEY_TRAP_ITEM_NAME = "King Mickey Trap"
KING_MICKEY_TRAP_RUNTIME_TOKEN = "KH3AP_TRAP::KING_MICKEY"
TRAP_RUNTIME_TOKEN_BY_ITEM_NAME = {
    **BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME,
    KING_MICKEY_TRAP_ITEM_NAME: KING_MICKEY_TRAP_RUNTIME_TOKEN,
}
TRAP_ITEM_NAMES = tuple(TRAP_RUNTIME_TOKEN_BY_ITEM_NAME)
TRAP_RUNTIME_TOKENS = frozenset(TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.values())

ULTIMA_WEAPON_ITEM_NAME = "Ultima Weapon"
ULTIMA_WEAPON_RUNTIME_TOKEN = "WEP_KEYBLADE_SO_015"
ULTIMA_WEAPON_SHARD_ITEM_NAME = "Ultima Weapon Shard"
GATE_OPENER_ITEM_NAME = "Gate Opener"
GATE_OPENER_RUNTIME_TOKEN = "KEY_ITEM20"
ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN = "KH3AP_ULTIMA_WEAPON_SHARD"
ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN_PREFIX = f"{ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN}::"

PORTAL_KEYBLADE_ITEM_NAMES = frozenset(PORTAL_KEYBLADE_REGION_LOCKS.values())

# Receiving the designated member opens the world; party composition is irrelevant.
PORTAL_PARTY_REGION_LOCKS = {
    "Olympus": ("Party Member: Hercules",),
    "Toy Box": ("Party Member: Woody",),
    "Kingdom of Corona": ("Party Member: Rapunzel",),
    "Monstropolis": ("Party Member: Sulley",),
    "The Caribbean": ("Party Member: Captain Jack Sparrow",),
    "San Fransokyo": ("Party Member: Baymax",),
    "Arendelle": ("Party Member: Marshmallow",),
    "Twilight Town": ("Party Member: Roxas",),
    "Dark World": ("Party Member: Aqua",),
    "Keyblade Graveyard": ("Party Member: Ventus",),
    "Re Mind": ("Party Member: Kairi",),
    "Radiant Garden": ("Party Member: Riku",),
    "Data Battles": ("Party Member: Riku",),
}
PORTAL_PARTY_ITEM_NAMES = frozenset(
    name for names in PORTAL_PARTY_REGION_LOCKS.values() for name in names
)

# Exclude these vanilla rewards from generated seeds. Orichalcum+ is also
# absent from the public AP catalog because its storage is reserved for shards.
RANDOMIZED_ITEM_POOL_EXCLUSIONS = frozenset({
    "Ultima Weapon",
    "Golden Herc Figure",
    "Gummiphone",
    "AR Device",
    # MAT_ITEM57 is reserved for Ultima Weapon Shard storage in shard mode.
    # Vanilla chest rewards must not put it back into the AP filler pool.
    "Orichalcum+",
})

WORLD_DIFFICULTY_TIERS = {
    1: frozenset({
        "Olympus",
        "Toy Box",
        "100 Acre Wood",
        "Monstropolis",
        "Kingdom of Corona",
        "The Caribbean",
        "Arendelle",
        "San Fransokyo",
    }),
    2: frozenset({
        "Twilight Town",
        "Keyblade Graveyard",
        "Re Mind",
        "Dark World",
    }),
    3: frozenset({
        "Data Battles",
    }),
    4: frozenset({
        "Unreality",
    }),
}

WORLD_DIFFICULTY_TIER_BY_REGION = {
    region_name: tier
    for tier, region_names in WORLD_DIFFICULTY_TIERS.items()
    for region_name in region_names
}

TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES = frozenset(
    PORTAL_KEYBLADE_REGION_LOCKS[region_name]
    for region_name in WORLD_DIFFICULTY_TIERS[1]
    if region_name in PORTAL_KEYBLADE_REGION_LOCKS
)

# Avoid advancing through small late-game tiers after finding only one portal keyblade.
HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT = 3

TWILIGHT_TOWN_PORTAL_LOGIC_REQUIREMENTS = parse_requirement_summary(
    "Block OR Air Slide OR Dodge Roll"
)

THE_CARIBBEAN_PORTAL_LOGIC_REQUIREMENTS = parse_requirement_summary(
    "Air Slide OR Dodge Roll OR Glide OR Blizzard"
)

# A starting portal keyblade must open at least one world by itself. The Caribbean
# also needs a safety tool, so Wheel of Fate cannot be the sole starting keyblade.
STARTING_PORTAL_KEYBLADE_ITEM_NAMES = TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES.difference({
    PORTAL_KEYBLADE_REGION_LOCKS["The Caribbean"],
})

LEVEL_UP_XP_SOURCE_REGION_NAMES = frozenset({
    "The Caribbean",
    "Arendelle",
    "Kingdom of Corona",
    "San Fransokyo",
    "Monstropolis",
    "Toy Box",
    "Twilight Town"
})

LEVEL_UP_XP_SOURCE_KEYBLADE_ITEM_NAMES = frozenset(
    PORTAL_KEYBLADE_REGION_LOCKS[region_name]
    for region_name in LEVEL_UP_XP_SOURCE_REGION_NAMES
)

SCALED_STORY_LEVEL_CAPS_BY_XP_SOURCE_COUNT = (3, 5, 8, 12, 15, 19, 22)
SCALED_STORY_END_LEVEL = SCALED_STORY_LEVEL_CAPS_BY_XP_SOURCE_COUNT[-1]
assert len(SCALED_STORY_LEVEL_CAPS_BY_XP_SOURCE_COUNT) == len(LEVEL_UP_XP_SOURCE_REGION_NAMES)


def required_level_up_xp_source_count(level: int) -> int:
    """Return how many grindable story worlds are needed to support a Sora level check."""
    source_count = len(LEVEL_UP_XP_SOURCE_REGION_NAMES)
    if level < 2:
        return source_count

    for required_count, level_cap in enumerate(
        SCALED_STORY_LEVEL_CAPS_BY_XP_SOURCE_COUNT,
        start=1,
    ):
        if level <= level_cap:
            return required_count
    return source_count


ZERO_COUNT_LUCKY_EMBLEM_WORLDS = frozenset({
    "Keyblade Graveyard",
    "Re Mind",
    "Data Battles",
    "Unreality",
})


@lru_cache(maxsize=1)
def load_lucky_emblem_spot_data() -> dict:
    try:
        return json.loads(read_package_text(LUCKY_EMBLEM_SPOTS_RESOURCE, module_file=__file__))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Missing KH3 lucky emblem spot data file at {DATA_PACKAGE}:{LUCKY_EMBLEM_SPOTS_RESOURCE}."
        ) from exc


def _build_lucky_emblem_counts_by_world() -> dict[str, int]:
    data = load_lucky_emblem_spot_data()
    counts = Counter(
        str(spot.get("world") or "")
        for spot in data.get("spots", [])
        if spot.get("world")
    )
    for world in ZERO_COUNT_LUCKY_EMBLEM_WORLDS:
        counts.setdefault(world, 0)
    return dict(counts)


LUCKY_EMBLEM_COUNTS_BY_WORLD = _build_lucky_emblem_counts_by_world()

LUCKY_EMBLEM_TOTAL_COUNT = sum(LUCKY_EMBLEM_COUNTS_BY_WORLD.values())

VANILLA_LUCKY_EMBLEM_MILESTONES = (
    1,
    3,
    5,
    10,
    15,
    20,
    25,
    30,
    35,
    40,
    45,
    50,
    55,
    60,
    65,
    70,
    80,
    90,
)


CLASSIC_KINGDOM_GAME_NAMES_BY_ID = {
    "LSIGAME01": "CK: Giantland",
    "LSIGAME02": "CK: Mickey, The Mail Pilot",
    "LSIGAME03": "CK: The Musical Farmer",
    "LSIGAME04": "CK: Building a Building",
    "LSIGAME05": "CK: The Mad Doctor",
    "LSIGAME06": "CK: Mickey's Kitten Catch",
    "LSIGAME07": "CK: The Klondike Kid",
    "LSIGAME08": "CK: Fishin' Frenzy",
    "LSIGAME09": "CK: The Karnival Kid",
    "LSIGAME10": "CK: Mickey Cuts Up",
    "LSIGAME11": "CK: Mickey's Prison Escape",
    "LSIGAME12": "CK: How to Play Baseball",
    "LSIGAME13": "CK: How to Play Golf",
    "LSIGAME14": "CK: Mickey's Circus",
    "LSIGAME15": "CK: Camping Out",
    "LSIGAME16": "CK: Taxi Troubles",
    "LSIGAME17": "CK: Beach Party",
    "LSIGAME18": "CK: The Wayward Canary",
    "LSIGAME19": "CK: Mickey's Mechanical Man",
    "LSIGAME20": "CK: The Barnyard Battle",
    "LSIGAME21": "CK: Cast Out to Sea",
    "LSIGAME22": "CK: Backyard Sports",
    "LSIGAME23": "CK: Mickey Steps Out",
}
CLASSIC_KINGDOM_GAME_IDS = tuple(CLASSIC_KINGDOM_GAME_NAMES_BY_ID)
CLASSIC_KINGDOM_BUILT_IN_GAME_IDS = (
    "LSIGAME02",
    "LSIGAME03",
    "LSIGAME04",
    "LSIGAME05",
)
CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID = dict(CLASSIC_KINGDOM_GAME_NAMES_BY_ID)
CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES = frozenset(CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME = {
    game_name: game_id
    for game_id, game_name in CLASSIC_KINGDOM_GAME_NAMES_BY_ID.items()
}
# These four native item rows were not referenced by a collectible location, so
# they are absent from the published extracted item catalog. Append their AP IDs
# without changing any existing item ID.
CLASSIC_KINGDOM_APPENDED_NATIVE_ITEM_NAMES = tuple(
    CLASSIC_KINGDOM_GAME_NAMES_BY_ID[game_id]
    for game_id in CLASSIC_KINGDOM_BUILT_IN_GAME_IDS
)
CLASSIC_KINGDOM_HIGH_SCORE_SOURCE_TABLE = "ClassicKingdomHighScore"
CLASSIC_KINGDOM_HIGH_SCORE_CHECK_KIND = "high_score"
CLASSIC_KINGDOM_CHECKS_OFF = 0
CLASSIC_KINGDOM_CHECKS_HIGH_SCORE = 1
CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID = "TresUIMobilePortalDataAsset"
CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME = "Classic Kingdom - Complete All Games"

# Third-party Olympus Coliseum 1.4.2 cup clears. These are synthetic AP
# locations observed by the UE4SS companion runtime; the Coliseum mod's own
# assets remain external to the world and are never bundled into an apworld.
# The secret Sephiroth entry requires an unreleased boss and is not a check.
OLYMPUS_COLISEUM_CUP_SOURCE_TABLE = "OlympusColiseumCup"
OLYMPUS_COLISEUM_CUP_NAMES_BY_ID = {
    "series_kh1/cup_phil": "KH1 - Phil Cup",
    "series_kh1/cup_pegasus": "KH1 - Pegasus Cup",
    "series_kh1/cup_hercules": "KH1 - Hercules Cup",
    "series_kh1/cup_hades": "KH1 - Hades Cup",
    "series_kh1/cup_gold": "KH1 - Gold Match",
    "series_kh1/cup_platinum": "KH1 - Platinum Match",
    "series_kh2/cup_painandpanic": "KH2 - Pain and Panic Cup",
    "series_kh2/cup_cerberus": "KH2 - Cerberus Cup",
    "series_kh2/cup_titan": "KH2 - Titan Cup",
    "series_kh2/cup_goddessoffate": "KH2 - Goddess of Fate Cup",
    "series_kh2/cup_paradox": "KH2 - Paradox Cup",
    "series_kh3/cup_hermes": "KH3 - Hermes Cup",
    "series_kh3/cup_apollo": "KH3 - Apollo Cup",
    "series_kh3/cup_athena": "KH3 - Athena Cup",
    "series_kh3/cup_zeus": "KH3 - Zeus Cup",
    "series_apro/cup_chef": "Apro - Chef Cup",
    "series_apro/cup_flan": "Apro - Flan Cup",
    "series_apro/cup_story": "Apro - The Story So Far Cup",
    "series_apro/cup_infernal": "Apro - Infernal Cup",
    "series_apro/cup_limitcut": "Apro - Limit Cut Cup",
    "series_apro/cup_vessels": "Apro - Vessels of Darkness Cup",
    "series_bbs/cup_terra": "BBS - Terra Cup",
    "series_bbs/cup_ventus": "BBS - Ventus Cup",
    "series_bbs/cup_aqua": "BBS - Aqua Cup",
}
OLYMPUS_COLISEUM_CUP_IDS = tuple(OLYMPUS_COLISEUM_CUP_NAMES_BY_ID)


def add_configurable_lucky_emblem_milestones(data: dict) -> dict:
    """Append the missing non-vanilla Lucky Emblem milestone rows."""
    locations = data.get("locations")
    if not isinstance(locations, list):
        return data

    existing_milestones = {
        int(str(location.get("source_id")))
        for location in locations
        if location.get("source_table") == "LuckyMark"
        and str(location.get("source_id", "")).isdigit()
    }
    for milestone in range(1, LUCKY_EMBLEM_TOTAL_COUNT + 1):
        if milestone in existing_milestones:
            continue
        locations.append({
            "name": f"Lucky Emblems - Milestone {milestone}",
            "type": "lucky_emblem",
            "world": "Lucky Emblems",
            "source_table": "LuckyMark",
            "source_id": str(milestone),
            # Synthetic milestone rows add checks rather than new vanilla
            # rewards, so contribute ordinary filler to the AP item pool.
            "vanilla_item": "ITEM_POTION",
            "vanilla_item_name": "Potion",
            "synthetic_milestone": True,
        })
    return data


def add_classic_kingdom_completion_locations(data: dict) -> dict:
    """Append event-driven Classic Kingdom checks after published catalogs."""
    locations = data.get("locations")
    if not isinstance(locations, list):
        return data

    for game_id, game_name in CLASSIC_KINGDOM_GAME_NAMES_BY_ID.items():
        display_name = game_name.removeprefix("CK: ")
        location_name = f"Classic Kingdom - {display_name} - High Score"
        locations.append({
            "name": location_name,
            "type": "classic_kingdom",
            "world": "Classic Kingdom",
            "source_table": CLASSIC_KINGDOM_HIGH_SCORE_SOURCE_TABLE,
            "source_id": game_id,
            "location_description": location_name,
            # Individual games have no native reward row. Each added AP location
            # contributes ordinary filler so the item/location totals stay even.
            "vanilla_item": "ITEM_POTION",
            "vanilla_item_name": "Potion",
            "classic_kingdom_check_kind": CLASSIC_KINGDOM_HIGH_SCORE_CHECK_KIND,
        })

    # The all-games reward already exists in vanilla and is independent of the
    # optional per-game checks. AP owns its reward so Classic Tone can be moved.
    locations.append({
        "name": CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
        "type": "event",
        "world": "Classic Kingdom",
        "source_table": "Event",
        "source_id": CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID,
        "subtype": "Reward",
        "location_description": CLASSIC_KINGDOM_ALL_GAMES_LOCATION_NAME,
        "vanilla_item": "WEP_KEYBLADE_SO_012",
        "vanilla_item_name": "Classic Tone",
    })
    return data


def add_olympus_coliseum_completion_locations(data: dict) -> dict:
    """Append opt-in third-party Coliseum cup-clear checks."""
    locations = data.get("locations")
    if not isinstance(locations, list):
        return data

    for cup_id, cup_name in OLYMPUS_COLISEUM_CUP_NAMES_BY_ID.items():
        location_name = f"Olympus Coliseum - {cup_name} Clear"
        locations.append({
            "name": location_name,
            "type": "olympus_coliseum",
            "world": "Olympus Coliseum",
            "source_table": OLYMPUS_COLISEUM_CUP_SOURCE_TABLE,
            "source_id": cup_id,
            "location_description": location_name,
            # A synthetic clear has no randomizer-owned native item row. Its
            # corresponding item is delivered through the bridge instead.
            "vanilla_item": "ITEM_POTION",
            "vanilla_item_name": "Potion",
        })
    return data


# These extracted catalogs were introduced after the synthetic Lucky Emblem
# locations had published AP IDs. They must remain after those legacy rows so
# adding a catalog cannot renumber an existing location.
POST_LEGACY_SYNTHETIC_LOCATION_TYPES = frozenset({"shop"})


def finalize_location_catalogs(data: dict) -> dict:
    """Assemble extracted and synthetic catalogs in published location-ID order."""
    locations = data.get("locations")
    if not isinstance(locations, list):
        return data

    appended_catalog_locations = [
        location
        for location in locations
        if location.get("type") in POST_LEGACY_SYNTHETIC_LOCATION_TYPES
    ]
    if appended_catalog_locations:
        locations[:] = [
            location
            for location in locations
            if location.get("type") not in POST_LEGACY_SYNTHETIC_LOCATION_TYPES
        ]

    add_configurable_lucky_emblem_milestones(data)
    locations.extend(appended_catalog_locations)
    # Keep these after every previously published extracted/synthetic catalog
    # so enabling the new option cannot renumber an existing AP location.
    add_classic_kingdom_completion_locations(data)
    # Keep third-party integration IDs at the newest published tail.
    add_olympus_coliseum_completion_locations(data)
    append_ingredient_locations(data)
    # The native reward catalog stops at 50; higher-level checks use bridge
    # delivery. Append them here to preserve every published location ID.
    for level in range(51, 100):
        locations.append({
            "name": f"Level Ups - Sora Level {level}",
            "type": "level_up", "world": "Level Ups",
            "source_table": "LevelUp", "source_id": str(level),
            "vanilla_items": {path: "ETresAbilityKind::NONE" for path in ("TypeA", "TypeB", "TypeC")},
            "vanilla_item_names": {path: "ETresAbilityKind::NONE" for path in ("TypeA", "TypeB", "TypeC")},
        })
    append_minigame_locations(data)
    append_minigame_item_locations(data)
    append_workshop_locations(data)
    append_adversary_locations(data)
    locations.append(dict(BATTLEGATE_ZERO_LOCATION))
    return data

FIXED_SORA_CHRINIT_ABILITY_FIELDS = (
    ("HaveAbility", "ETresAbilityKind::EXPZERO", "Ability: Zero EXP"),
)

FIXED_SORA_CHRINIT_ABILITY_ITEM_NAMES = frozenset(
    item_name
    for _field_name, _runtime_token, item_name in FIXED_SORA_CHRINIT_ABILITY_FIELDS
)

DEFAULT_BASE_ABILITY_CHRINIT_FIELDS = (
    ("EquipAbility0", "ETresAbilityKind::LIBRA", "Ability: Scan"),
    ("EquipAbility1", "ETresAbilityKind::DODGE", "Ability: Dodge Roll"),
    ("EquipAbility2", "ETresAbilityKind::AIRSLIDE", "Ability: Air Slide"),
    ("EquipAbility3", "ETresAbilityKind::AIRDODGE", "Ability: Aerial Dodge"),
    ("EquipAbility4", "ETresAbilityKind::REFLECT_GUARD", "Ability: Block"),
    ("EquipAbility5", "ETresAbilityKind::SUPERSLIDE", "Ability: Superslide"),
    ("EquipAbility6", "ETresAbilityKind::FRIEND_AID", "Ability: Assist Friends"),
    ("EquipAbility7", "ETresAbilityKind::SONIC_SLASH", "Ability: Sonic Slash"),
    ("EquipAbility8", "ETresAbilityKind::SONIC_DOWN", "Ability: Sonic Cleave"),
    ("EquipAbility9", "ETresAbilityKind::TURN_CUTTER", "Ability: Buzz Saw"),
    ("EquipAbility10", "ETresAbilityKind::SUMMERSALT", "Ability: Somersault"),
    ("EquipAbility11", "ETresAbilityKind::POLE_SPIN", "Ability: Pole Spin"),
    ("EquipAbility12", "ETresAbilityKind::POLE_SWING", "Ability: Pole Swing"),
    ("EquipAbility13", "ETresAbilityKind::WALL_KICK", "Ability: Wall Kick"),
    ("EquipAbility14", "ETresAbilityKind::CRITICAL_HALF", "Ability: Damage Control"),
    ("EquipAbility15", "ETresAbilityKind::AUTO_LOCK_MAGIC", "Ability: Magic Lock-On"),
)

DEFAULT_BASE_ABILITY_ITEM_NAMES = frozenset(
    item_name
    for _field_name, _runtime_token, item_name in DEFAULT_BASE_ABILITY_CHRINIT_FIELDS
)

def _lucky_emblem_spot_logic_summary(spot: dict) -> str:
    summary = spot.get("logic_requirement_summary")
    if summary is None:
        summary = spot.get("logic_requirements", "")
    if isinstance(summary, list):
        return ""
    return str(summary).strip()


def _build_lucky_emblem_logic_requirements_by_world() -> dict[str, tuple[RequirementAlternatives, ...]]:
    requirements_by_world: dict[str, list[RequirementAlternatives]] = {}
    for spot in load_lucky_emblem_spot_data().get("spots", []):
        world = str(spot.get("world") or "")
        if not world:
            continue
        summary = _lucky_emblem_spot_logic_summary(spot)
        requirements_by_world.setdefault(world, []).append(
            parse_requirement_summary(summary) if summary else ()
        )

    for world, count in LUCKY_EMBLEM_COUNTS_BY_WORLD.items():
        if count == 0:
            requirements_by_world.setdefault(world, [])

    return {
        world: tuple(requirements)
        for world, requirements in requirements_by_world.items()
    }


LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD = _build_lucky_emblem_logic_requirements_by_world()


INVALID_ITEM_NAMES = {
    "ETresAbilityKind::NONE",
    "ETresVictoryBonusKind::NONE",
}

MUNNY_POUCH_ITEM_NAME = "Munny Pouch"
MUNNY_POUCH_RUNTIME_TOKEN = "KEY_ITEM18"
MUNNY_POUCH_GRANT_METHOD = "bp_tres_get_item_add_munny"
DURABLE_SORA_ABILITY_GRANT_METHOD = "durable_sora_ability"
DURABLE_SORA_VBONUS_GRANT_METHOD = "durable_sora_vbonus"
MUNNY_POUCH_MUNNY_AMOUNT = 100
LARGE_MUNNY_POUCH_ITEM_NAME = "Large Munny Pouch"
LARGE_MUNNY_POUCH_RUNTIME_TOKEN = "KEY_ITEM19"
LARGE_MUNNY_POUCH_MUNNY_AMOUNT = 1000
MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME = {
    MUNNY_POUCH_ITEM_NAME: MUNNY_POUCH_RUNTIME_TOKEN,
    LARGE_MUNNY_POUCH_ITEM_NAME: LARGE_MUNNY_POUCH_RUNTIME_TOKEN,
}
MUNNY_POUCH_MUNNY_AMOUNT_BY_NAME = {
    MUNNY_POUCH_ITEM_NAME: MUNNY_POUCH_MUNNY_AMOUNT,
    LARGE_MUNNY_POUCH_ITEM_NAME: LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
}
MUNNY_POUCH_ITEM_NAMES = frozenset(MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME)
MUNNY_POUCH_RUNTIME_TOKENS = frozenset(MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME.values())

FILLER_ITEM_RUNTIME_TOKEN_BY_NAME = {
    "Potion": "ITEM_POTION",
    "Hi-Potion": "ITEM_HIGHPOTION",
    "Mega-Potion": "ITEM_MEGAPOTION",
    "Ether": "ITEM_ETHER",
    "Hi-Ether": "ITEM_HIGHETHER",
    "Mega-Ether": "ITEM_MEGAETHER",
    "Refocuser": "ITEM_FOCUSSUPPLY",
    "Hi-Refocuser": "ITEM_HIGHFOCUSSUPPLY",
    "Panacea": "ITEM_ALLCURE",
    "Tent": "ITEM_TENT",
    "Elixir": "ITEM_ELIXIR",
    "Megalixir": "ITEM_LASTELIXIR",
    **MUNNY_POUCH_RUNTIME_TOKEN_BY_NAME,
}
# Large pouches are budget items inserted by the Moogle Shop economy pass;
# they should not dilute the ordinary repeatable filler roll.
FILLER_ITEM_NAMES = tuple(
    item_name
    for item_name in FILLER_ITEM_RUNTIME_TOKEN_BY_NAME
    if item_name != LARGE_MUNNY_POUCH_ITEM_NAME
)
FILLER_ITEM_RUNTIME_TOKENS = tuple(FILLER_ITEM_RUNTIME_TOKEN_BY_NAME.values())

PROGRESSION_KEYWORDS = (
    "Proof of ",
    "Secret Report ",
    "Heartbinder",
    "Magic: ",
    "Ability: Air Slide",
    "Ability: Dodge Roll",
    "Ability: Block",
    "Ability: High Jump",
    "Ability: Glide",
    "Ability: Pole Spin",
    "Ability: Doubleflight",
    "Ability: Second Chance",
    "Ability: Withstand Combo",
    "Unlock: ",
    "Archipelago Item",
)

OPTIONAL_WORLD_NAMES = {
    "Lucky Emblems",
    "Battlegate Reports",
    "Battlegate Rewards",
    "Data Battles",
    "Re Mind",
    "Unreality",
    "Keyblade Graveyard",
    "The Final World",
}
ALWAYS_ON_WORLD_NAME = "Always On"
VANILLA_LOCATION_POOL_VALUE = 1
RANDOMIZE_LOCATION_POOL_VALUE = 2
REPLACE_LOCATION_POOL_VALUE = 3
OLYMPUS_COLISEUM_ACTIVE_POOL_VALUES = frozenset({
    RANDOMIZE_LOCATION_POOL_VALUE,
    REPLACE_LOCATION_POOL_VALUE,
})
GOLDEN_HERC_REWARD_SOURCE_IDS = frozenset({
    "EVENT_002",
    "EVENT_003",
    "EVENT_004",
    "EVENT_005",
    "EVENT_006",
    "EVENT_007",
})

WORLD_LOCATION_POOL_OPTIONS = {
    "Olympus": "olympus_pool",
    "Twilight Town": "twilight_town_pool",
    "Toy Box": "toy_box_pool",
    "Kingdom of Corona": "kingdom_of_corona_pool",
    "Monstropolis": "monstropolis_pool",
    "100 Acre Wood": "hundred_acre_wood_pool",
    "Arendelle": "arendelle_pool",
    "San Fransokyo": "san_fransokyo_pool",
    "The Caribbean": "the_caribbean_pool",
    "Keyblade Graveyard": "keyblade_graveyard_pool",
    "The Final World": "keyblade_graveyard_pool",
    "Re Mind": "remind_pool",
    "Dark World": "dark_world_pool",
    "Unreality": "unreality_pool",
    "Olympus Coliseum": "olympus_coliseum_pool",
}

REWARD_LOCATION_POOL_OPTIONS = {
    "Lucky Emblems": "lucky_emblems_pool",
    "Data Battles": "data_battle_rewards_pool",
    "Battlegate Reports": "battlegates_pool",
    "Battlegate Rewards": "battlegates_pool",
}

RAW_ITEM_DISPLAY_NAMES = {
    "MAGIC_AERO": "Magic: Aero",
    "MAGIC_BLIZZARD": "Magic: Blizzard",
    "MAGIC_CURE": "Magic: Cure",
    "MAGIC_FIRE": "Magic: Fire",
    "MAGIC_THUNDER": "Magic: Thunder",
    "MAGIC_WATER": "Magic: Water",
    "ETresVictoryBonusKind::HP_UP5": "Bonus: HP +5",
    "ETresVictoryBonusKind::HP_UP10": "Bonus: HP +10",
    "ETresVictoryBonusKind::HP_UP15": "Bonus: HP +15",
    "ETresVictoryBonusKind::HP_UP30": "Bonus: HP +30",
    "ETresVictoryBonusKind::MP_UP3": "Bonus: MP +3",
    "ETresVictoryBonusKind::MP_UP5": "Bonus: MP +5",
    "ETresVictoryBonusKind::MP_UP10": "Bonus: MP +10",
    "ETresVictoryBonusKind::MP_UP20": "Bonus: MP +20",
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

# These friendly names were added after the original sorted item-ID registry.
# Append them so correcting display-name aliases does not renumber existing IDs.
APPENDED_ITEM_ID_NAMES = frozenset({
    "Grand Chef",
})

# These AP-only items have no native KH3 inventory row. Append them after every
# previously published item ID so adding one never renumbers an existing item.
AP_ONLY_ITEM_ID_NAMES = (
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME,
    *PROGRESSIVE_KEYBLADE_LEVEL_INDIVIDUAL_ITEM_NAMES,
    *BAD_STATUS_TRAP_ITEM_NAMES,
    MUNNY_POUCH_ITEM_NAME,
    LARGE_MUNNY_POUCH_ITEM_NAME,
    ULTIMA_WEAPON_SHARD_ITEM_NAME,
    GATE_OPENER_ITEM_NAME,
)

KEYBLADE_DISPLAY_NAMES = frozenset(
    display_name
    for item_name, display_name in RAW_ITEM_DISPLAY_NAMES.items()
    if item_name.startswith("WEP_KEYBLADE_")
) | {"Grand Chef", "Ultima Weapon"}

RANDOMIZED_KEYBLADE_ITEM_NAMES = KEYBLADE_DISPLAY_NAMES.difference(
    RANDOMIZED_ITEM_POOL_EXCLUSIONS
)

CONTROLLER_UNLOCK_ITEMS = {
    SPELLWEAVER_UNLOCK_ITEM: {
        "key": "spellweaver",
        "ability_id": None,
        "option_name": None,
        "display_name": "Spellweaver",
    },
    "Unlock: Wall Run": {
        "key": "wall_run",
        "ability_id": 113,
        "option_name": "start_with_wall_run",
        "display_name": "Wall Run",
    },
    STYLE_CHANGE_UNLOCK_ITEM: {
        "key": "style_change",
        "ability_id": 114,
        "option_name": None,
        "display_name": "Style Change",
    },
    MAGIC_UNLOCK_ITEM: {
        "key": "magic",
        "ability_id": 115,
        "option_name": "start_with_magic",
        "display_name": "Magic",
    },
    "Unlock: Attraction Flow": {
        "key": "attraction_flow",
        "ability_id": 116,
        "option_name": "start_with_attraction_flow",
        "display_name": "Attraction Flow",
    },
    "Unlock: Airstep": {
        "key": "airstep",
        "ability_id": None,
        "option_name": None,
        "display_name": "Airstep",
    },
    "Unlock: Links": {
        "key": "links",
        "ability_id": 134,
        "option_name": "start_with_links",
        "display_name": "Links",
    },
}
CONTROLLER_UNLOCK_ITEM_NAMES = tuple(CONTROLLER_UNLOCK_ITEMS)
CONTROLLER_UNLOCK_ITEM_NAMES_BY_OPTION = {
    metadata["option_name"]: item_name
    for item_name, metadata in CONTROLLER_UNLOCK_ITEMS.items()
    if metadata["option_name"] is not None
}
CONTROLLER_UNLOCK_RUNTIME_TOKEN_PREFIX = "KH3AP_CONTROLLER_UNLOCK::"

# Preserve every published party-item ID position. TresPartyData has no
# PartyMember_021 row.
_PARTY_MEMBER_ID_DEFINITIONS = (
    ("PartyMember_001", "Donald", "n_ex001"),
    ("PartyMember_002", "Goofy", "n_ex002"),
    ("PartyMember_003", "Hercules", "n_he201"),
    ("PartyMember_004", "Woody", "n_ts201"),
    ("PartyMember_005", "Buzz Lightyear", "n_ts202"),
    ("PartyMember_006", "Rapunzel", "n_ra201"),
    ("PartyMember_007", "Flynn Rider", "n_ra203"),
    ("PartyMember_008", "Sulley", "n_mi201"),
    ("PartyMember_009", "Mike", "n_mi202"),
    ("PartyMember_010", "Marshmallow", "n_fz214"),
    ("PartyMember_011", "Captain Jack Sparrow", "n_ca201"),
    ("PartyMember_012", "Baymax", "n_bx202"),
    ("PartyMember_013", "Mickey", "n_ex003"),
    ("PartyMember_014", "Riku", "n_ex004"),
    ("PartyMember_015", "Aqua", "n_ex011"),
    ("PartyMember_016", "Ventus", "n_ex006"),
    ("PartyMember_017", "Lea", "n_ex010"),
    ("PartyMember_018", "Kairi", "n_ex005"),
    ("PartyMember_019", "Roxas", "n_ex009"),
    ("PartyMember_020", "Xion", "n_ex033"),
    ("PartyMember_022", "Donald (Caribbean)", "n_ca001"),
    ("PartyMember_023", "Goofy (Caribbean)", "n_ca002"),
    ("PartyMember_024", "Donald (Monstropolis)", "n_mi001"),
    ("PartyMember_025", "Goofy (Monstropolis)", "n_mi002"),
    ("PartyMember_026", "Donald (Toy Box)", "n_ts001"),
    ("PartyMember_027", "Goofy (Toy Box)", "n_ts002"),
)
PARTY_MEMBER_DEFINITIONS = _PARTY_MEMBER_ID_DEFINITIONS
# These forms are owned directly by the native party service, not AP receipts.
DEFAULT_UNLOCKED_PARTY_MEMBER_ROW_IDS = frozenset({
    "PartyMember_001", "PartyMember_002",
    "PartyMember_022", "PartyMember_023",
    "PartyMember_024", "PartyMember_025",
    "PartyMember_026", "PartyMember_027",
})
PARTY_MEMBER_UNLOCK_ITEMS = {
    f"Party Member: {display_name}": {
        "row_id": row_id,
        "display_name": display_name,
        "npc_id": npc_id,
    }
    for row_id, display_name, npc_id in PARTY_MEMBER_DEFINITIONS
    if row_id not in DEFAULT_UNLOCKED_PARTY_MEMBER_ROW_IDS
}
PARTY_MEMBER_UNLOCK_ITEM_NAMES = tuple(PARTY_MEMBER_UNLOCK_ITEMS)
PARTY_MEMBER_UNLOCK_ID_RESERVATIONS = tuple(
    f"Party Member: {display_name}"
    for _, display_name, _ in _PARTY_MEMBER_ID_DEFINITIONS
)
PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_PREFIX = "KH3AP_PARTY_MEMBER_UNLOCK::"
PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME = {
    item_name: f"{PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_PREFIX}{metadata['row_id']}"
    for item_name, metadata in PARTY_MEMBER_UNLOCK_ITEMS.items()
}

RAW_ITEM_RUNTIME_TOKEN_ALIASES = {
    "MAGIC_AERO": "Magic: Aero",
    "MAGIC_BLIZZARD": "Magic: Blizzard",
    "MAGIC_CURE": "Magic: Cure",
    "MAGIC_FIRE": "Magic: Fire",
    "MAGIC_THUNDER": "Magic: Thunder",
    "MAGIC_WATER": "Magic: Water",
}


def load_extracted_data() -> dict:
    try:
        return finalize_location_catalogs(
            json.loads(read_package_text(DATA_RESOURCE, module_file=__file__))
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Missing KH3 extracted data file at {DATA_PACKAGE}:{DATA_RESOURCE}. "
            f"Run worlds/kh3/extract_data.py to generate it."
        ) from exc


def resolve_item_display_name(item_name: str) -> str:
    display_name = RAW_ITEM_DISPLAY_NAMES.get(item_name)
    if display_name is not None:
        return display_name
    if "\\'" in item_name:
        return item_name.replace("\\'", "'")
    return item_name


def progressive_keyblade_level_base_runtime_token(runtime_token: str | None) -> str | None:
    if not runtime_token:
        return None
    normalized_token = normalize_runtime_token(str(runtime_token))
    if normalized_token in PROGRESSIVE_KEYBLADE_LEVEL_BASE_RUNTIME_TOKENS:
        return normalized_token
    for base_runtime_token in sorted(PROGRESSIVE_KEYBLADE_LEVEL_BASE_RUNTIME_TOKENS, key=len, reverse=True):
        ordered_prefix = f"{base_runtime_token}::"
        if normalized_token.startswith(ordered_prefix) and normalized_token[len(ordered_prefix):].isdigit():
            return base_runtime_token
    return None


def normalize_runtime_token(token: str) -> str:
    return token.rstrip("\x00")


@lru_cache(maxsize=1)
def _build_item_name_to_runtime_token() -> dict[str, str]:
    from .Space import SPACE_TOKENS
    extracted_data = load_extracted_data()
    mapping: dict[str, str] = dict(SPACE_TOKENS)
    for item in extracted_data["items"]:
        item_name = item["name"]
        if _is_invalid_item_name(item_name):
            continue
        runtime_token = normalize_runtime_token(str(item["id"]))
        if runtime_token.isdecimal():
            continue
        mapping[item_name] = runtime_token

        display_name = resolve_item_display_name(item_name)
        if display_name != item_name:
            existing = mapping.get(display_name)
            if existing is None or existing == runtime_token:
                mapping[display_name] = runtime_token

    mapping.update(CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME)
    mapping["Archipelago Item"] = "AP_ITEM_000"
    mapping.update(FILLER_ITEM_RUNTIME_TOKEN_BY_NAME)
    mapping.update(PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME)
    mapping.update(TRAP_RUNTIME_TOKEN_BY_ITEM_NAME)
    mapping.update(KEYBLADE_STYLE_RUNTIME_TOKEN_BY_ITEM_NAME)
    for item_name, metadata in CONTROLLER_UNLOCK_ITEMS.items():
        mapping[item_name] = f"{CONTROLLER_UNLOCK_RUNTIME_TOKEN_PREFIX}{metadata['key']}"
    mapping.update(PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME)
    mapping.update(CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME)
    for raw_item_name, target_item_name in RAW_ITEM_RUNTIME_TOKEN_ALIASES.items():
        target_token = mapping.get(target_item_name)
        if target_token:
            mapping[raw_item_name] = target_token
    return mapping


@lru_cache(maxsize=1)
def _build_runtime_token_to_item_name() -> dict[str, str]:
    from .Space import SPACE_NAMES_BY_TOKEN
    extracted_data = load_extracted_data()
    mapping: dict[str, str] = dict(SPACE_NAMES_BY_TOKEN)
    for item in extracted_data["items"]:
        item_name = item["name"]
        if _is_invalid_item_name(item_name):
            continue
        runtime_token = normalize_runtime_token(str(item["id"]))
        if runtime_token:
            mapping[runtime_token] = resolve_item_display_name(item_name)
    mapping.update({
        runtime_token: item_name
        for item_name, runtime_token in CLASSIC_KINGDOM_NATIVE_ITEM_RUNTIME_TOKEN_BY_NAME.items()
    })
    mapping["AP_ITEM_000"] = "Archipelago Item"
    mapping.update({
        runtime_token: item_name
        for item_name, runtime_token in FILLER_ITEM_RUNTIME_TOKEN_BY_NAME.items()
    })
    mapping.update({
        runtime_token: item_name
        for item_name, runtime_token in PROGRESSIVE_KEYBLADE_LEVEL_RUNTIME_TOKEN_BY_ITEM_NAME.items()
    })
    mapping.update({
        runtime_token: item_name
        for item_name, runtime_token in TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.items()
    })
    mapping.update({
        runtime_token: item_name
        for item_name, runtime_token in KEYBLADE_STYLE_RUNTIME_TOKEN_BY_ITEM_NAME.items()
    })
    for item_name, metadata in CONTROLLER_UNLOCK_ITEMS.items():
        mapping[f"{CONTROLLER_UNLOCK_RUNTIME_TOKEN_PREFIX}{metadata['key']}"] = item_name
    mapping.update({
        runtime_token: item_name
        for item_name, runtime_token in PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME.items()
    })
    mapping.update({token: name for name, token in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME.items()})
    return mapping


def resolve_item_runtime_token(item_name: str) -> str | None:
    if item_name == GATE_OPENER_ITEM_NAME:
        return GATE_OPENER_RUNTIME_TOKEN
    if resolve_item_display_name(item_name) == ULTIMA_WEAPON_SHARD_ITEM_NAME:
        return ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN
    normalized_name = normalize_runtime_token(item_name)
    mapping = _build_item_name_to_runtime_token()
    runtime_token = mapping.get(normalized_name)
    if runtime_token is not None:
        return runtime_token
    return mapping.get(resolve_item_display_name(normalized_name))


def resolve_runtime_token_display_name(runtime_token: str | None) -> str | None:
    if not runtime_token:
        return None
    normalized_token = normalize_runtime_token(runtime_token)
    if normalized_token == GATE_OPENER_RUNTIME_TOKEN:
        return GATE_OPENER_ITEM_NAME
    if not normalized_token or normalized_token.endswith("::NONE"):
        return None
    direct_name = _build_runtime_token_to_item_name().get(normalized_token)
    if direct_name is not None:
        return direct_name
    progressive_base_token = progressive_keyblade_level_base_runtime_token(normalized_token)
    if progressive_base_token is not None:
        return _build_runtime_token_to_item_name().get(progressive_base_token, normalized_token)
    return normalized_token


@lru_cache(maxsize=1)
def _build_runtime_token_to_flag_rows() -> dict[str, list[str]]:
    extracted_data = load_extracted_data()
    raw_mapping = extracted_data.get("meta", {}).get("runtime_flag_rows_by_token", {})
    if not isinstance(raw_mapping, dict):
        return {}

    mapping: dict[str, list[str]] = {}
    for runtime_token, row_names in raw_mapping.items():
        if not isinstance(row_names, list):
            continue
        normalized_token = normalize_runtime_token(str(runtime_token))
        normalized_rows = [str(row_name) for row_name in row_names if row_name]
        if normalized_token and normalized_rows:
            mapping[normalized_token] = normalized_rows
    return mapping


def resolve_runtime_flag_name(runtime_token: str | None) -> str | None:
    if not runtime_token:
        return None
    flag_rows = _build_runtime_token_to_flag_rows().get(normalize_runtime_token(runtime_token), [])
    return flag_rows[0] if flag_rows else None


def resolve_runtime_flag_names(runtime_token: str | None) -> list[str]:
    if not runtime_token:
        return []
    return list(_build_runtime_token_to_flag_rows().get(normalize_runtime_token(runtime_token), []))


def resolve_item_runtime_flag_name(item_name: str) -> str | None:
    return resolve_runtime_flag_name(resolve_item_runtime_token(item_name))


def _normalize_runtime_token_list(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            normalize_runtime_token(str(value))
            for value in values
            if value and not normalize_runtime_token(str(value)).endswith("::NONE")
        )
    )


@lru_cache(maxsize=1)
def _build_vbonus_bundle_lookup() -> dict[tuple[str, ...], str]:
    extracted_data = load_extracted_data()
    bundle_to_source: dict[tuple[str, ...], str] = {}
    ambiguous_bundles: set[tuple[str, ...]] = set()

    for location in extracted_data.get("locations", []):
        if location.get("type") != "vbonus":
            continue
        vanilla_items = location.get("vanilla_items")
        if not isinstance(vanilla_items, dict):
            continue

        bundle = _normalize_runtime_token_list(vanilla_items.values())
        if not bundle:
            continue

        source_id = str(location.get("source_id") or "")
        if not source_id:
            continue

        if bundle in ambiguous_bundles:
            continue

        existing = bundle_to_source.get(bundle)
        if existing is None:
            bundle_to_source[bundle] = source_id
        elif existing != source_id:
            bundle_to_source.pop(bundle, None)
            ambiguous_bundles.add(bundle)

    return bundle_to_source


def resolve_vbonus_source_id_for_tokens(runtime_tokens: Iterable[str]) -> str | None:
    bundle = _normalize_runtime_token_list(runtime_tokens)
    if not bundle:
        return None
    return _build_vbonus_bundle_lookup().get(bundle)


LIVE_BONUS_STAT_RUNTIME_TOKENS = frozenset({
    "ETresVictoryBonusKind::HP_UP5",
    "ETresVictoryBonusKind::HP_UP10",
    "ETresVictoryBonusKind::HP_UP15",
    "ETresVictoryBonusKind::HP_UP30",
    "ETresVictoryBonusKind::MP_UP3",
    "ETresVictoryBonusKind::MP_UP5",
    "ETresVictoryBonusKind::MP_UP10",
    "ETresVictoryBonusKind::MP_UP20",
})


def local_lucky_emblem_runtime_token_requires_bridge_delivery(
    location_type: str | None,
    runtime_token: str | None,
) -> bool:
    if location_type != "lucky_emblem" or not runtime_token:
        return False
    normalized = normalize_runtime_token(str(runtime_token).rstrip("\0"))
    if not normalized or normalized in INVALID_ITEM_NAMES or normalized.endswith("::NONE"):
        return False
    # Every Lucky Emblem milestone must carry its exact AP location identity into
    # the runtime resolver.  Route local native items through the bridge too so
    # the location check is reported before the received item is granted.
    return True


BRIDGE_DELIVERED_LOCAL_REWARD_BRIDGE_KEYS = frozenset({
    "Event:EVENT_007",
    f"Event:{CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID}",
    # Completion/event rows whose checks are already reported through their
    # RandomizerEvents flags. Suppress their native item fields so AP receipt
    # is the single authoritative delivery path.
    "Event:EVENT_008",
    "Event:EVENT_009",
    "Event:EVENT_CKGAME_001",
    "Event:EVENT_HEARTBINDER_002",
    "Event:EVENT_HEARTBINDER_003",
    "Event:EVENT_HEARTBINDER_004",
    "Event:EVENT_KEYBLADE_001",
    "Event:EVENT_KEYBLADE_002",
    "Event:EVENT_KEYBLADE_003",
    "Event:EVENT_KEYBLADE_004",
    "Event:EVENT_KEYBLADE_005",
    "Event:EVENT_KEYBLADE_006",
    "Event:EVENT_KEYBLADE_007",
    "Event:EVENT_KEYBLADE_008",
    "Event:EVENT_KEYBLADE_009",
    "Event:EVENT_KEYBLADE_011",
    "Event:EVENT_KEYITEM_002",
})


def _local_seeded_reward_requires_bridge_delivery(
    bridge_key: str | None,
    location_type: str | None,
    runtime_token: str | None,
    bridge_delivered_keys: frozenset[str],
) -> bool:
    if location_type == "space" or (runtime_token and runtime_token.startswith("GUMI")):
        return True
    if location_type == "character_unlock" or (runtime_token and runtime_token.startswith(CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX)):
        return True
    if location_type in {"event", "classic_kingdom", "olympus_coliseum", "level_up", "ingredient", "adversary", "melody_of_memory", "kh02_chest", "kh02_boss", "kh02_objective", "music_chest_bonus", "vbonus_extra", "minigame_reward"}:
        # These checks have no randomizer-owned native reward row carrying the
        # assigned local item. EventPostActor is bookkeeping-only in AP GoA.
        # AP receipt is the authoritative delivery path.
        return True
    if bridge_key in bridge_delivered_keys:
        return True
    if runtime_token and normalize_runtime_token(str(runtime_token)) in MUNNY_POUCH_RUNTIME_TOKENS:
        # Pouch item rows provide presentation/inventory only; the bridge's
        # composite runtime grant is what applies their munny value.
        return True
    if runtime_token and normalize_runtime_token(str(runtime_token)) in TRAP_RUNTIME_TOKENS:
        return True
    if location_type == "vbonus" and runtime_token:
        normalized = normalize_runtime_token(str(runtime_token).rstrip("\0"))
        if normalized and normalized not in INVALID_ITEM_NAMES and not normalized.endswith("::NONE"):
            # GoA special-battle completion can persist the VBonus flag without
            # reliably applying the native reward.  Suppress every local VBonus
            # reward in newly generated paks and let the AP delivery pipeline
            # grant and acknowledge it instead.
            return True
    return local_lucky_emblem_runtime_token_requires_bridge_delivery(location_type, runtime_token)


def local_seeded_reward_requires_bridge_delivery(
    bridge_key: str | None,
    location_type: str | None,
    runtime_token: str | None,
    *,
    persistent_chests: bool = True,
) -> bool:
    """Generation policy for seeds carrying explicit bridge-delivery metadata."""
    if location_type == "treasure" and persistent_chests:
        # The seed suppresses the native chest reward. The durable chest check
        # reports completion, and AP receipts deliver/replay the assigned item.
        return True
    return _local_seeded_reward_requires_bridge_delivery(
        bridge_key,
        location_type,
        runtime_token,
        BRIDGE_DELIVERED_LOCAL_REWARD_BRIDGE_KEYS,
    )


def classify_runtime_grant_method(runtime_token: str | None) -> str | None:
    if not runtime_token:
        return None
    runtime_token = str(runtime_token).rstrip("\0")
    if runtime_token in INVALID_ITEM_NAMES or runtime_token.endswith("::NONE"):
        return None
    if runtime_token in MUNNY_POUCH_RUNTIME_TOKENS:
        return MUNNY_POUCH_GRANT_METHOD
    if runtime_token == ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN or runtime_token.startswith(
        ULTIMA_WEAPON_SHARD_RUNTIME_TOKEN_PREFIX
    ):
        return "ultima_weapon_shard"
    if runtime_token == KING_MICKEY_TRAP_RUNTIME_TOKEN:
        return "king_mickey_trap"
    if runtime_token in BAD_STATUS_TRAP_RUNTIME_TOKENS:
        return "direct_player_bad_status"
    if runtime_token.startswith(CONTROLLER_UNLOCK_RUNTIME_TOKEN_PREFIX):
        return "controller_unlock"
    if runtime_token.startswith(KEYBLADE_STYLE_RUNTIME_TOKEN_PREFIX):
        return "keyblade_style"
    if runtime_token.startswith(PARTY_MEMBER_UNLOCK_RUNTIME_TOKEN_PREFIX):
        return "party_member_unlock"
    if runtime_token.startswith(CHARACTER_UNLOCK_RUNTIME_TOKEN_PREFIX):
        return "character_unlock"
    if progressive_keyblade_level_base_runtime_token(runtime_token) is not None:
        return "progressive_keyblade_level"
    if runtime_token.startswith("ETresAbilityKind::"):
        return DURABLE_SORA_ABILITY_GRANT_METHOD
    if runtime_token.startswith("ETresVictoryBonusKind::"):
        return DURABLE_SORA_VBONUS_GRANT_METHOD
    return "bp_tres_get_item"


def is_keyblade_item_name(item_name: str) -> bool:
    return item_name.startswith("WEP_KEYBLADE_") or item_name in KEYBLADE_DISPLAY_NAMES


def _is_progression_name(item_name: str) -> bool:
    return (
        item_name in PROOF_ITEMS
        or item_name in LOGIC_REQUIREMENT_ITEM_NAMES
        or item_name.startswith(PROGRESSION_KEYWORDS)
    )


def _is_invalid_item_name(item_name: str) -> bool:
    return not item_name or item_name in INVALID_ITEM_NAMES or item_name.endswith("::NONE")


def _pick_primary_item(location: dict) -> str:
    if "vanilla_item_name" in location:
        item_name = resolve_item_display_name(location["vanilla_item_name"])
        return item_name if not _is_invalid_item_name(item_name) else ""

    item_names = [
        resolve_item_display_name(item_name)
        for item_name in location.get("vanilla_item_names", {}).values()
        if not _is_invalid_item_name(item_name)
    ]
    if not item_names:
        return ""

    for item_name in item_names:
        if _is_progression_name(item_name):
            return item_name

    for item_name in item_names:
        if item_name.startswith("Bonus: "):
            return item_name

    return item_names[0]


def display_location_name(location: dict) -> str:
    location_description = str(location.get("location_description") or "").strip()
    if location_description:
        return location_description
    return str(location.get("name") or "").strip()


def normalize_locations(data: dict) -> list[dict]:
    normalized: list[dict] = []
    for location in data["locations"]:
        entry = dict(location)
        entry["name"] = display_location_name(location)
        entry["primary_item_name"] = _pick_primary_item(location)
        normalized.append(entry)
    return normalized


def validate_locations(locations: list[dict]) -> None:
    seen_names: set[str] = set()
    seen_bridge_keys: set[tuple[str, str]] = set()

    for location in locations:
        name = location["name"]
        if name in seen_names:
            raise ValueError(f"Duplicate KH3 location name detected: {name}")
        seen_names.add(name)

        bridge_key = (location["source_table"], location["source_id"])
        if bridge_key in seen_bridge_keys:
            raise ValueError(
                "Duplicate KH3 bridge source detected: "
                f"{location['source_table']}:{location['source_id']}"
            )
        seen_bridge_keys.add(bridge_key)


def is_always_on_location(location: dict) -> bool:
    world = str(location.get("world") or "")
    name = str(location.get("name") or "")
    return world == ALWAYS_ON_WORLD_NAME or name.startswith("Always On - ")


def _option_value(options, option_name: str):
    option = getattr(options, option_name, None)
    if option is None:
        return None
    return getattr(option, "value", option)


def _is_location_pool_option_value(options, option_name: str, expected_value: int, expected_name: str) -> bool:
    raw_value = _option_value(options, option_name)
    try:
        return int(raw_value) == expected_value
    except (TypeError, ValueError):
        return str(raw_value).casefold() == expected_name


def _is_replace_location_pool_option(options, option_name: str) -> bool:
    return _is_location_pool_option_value(
        options,
        option_name,
        REPLACE_LOCATION_POOL_VALUE,
        "junk",
    )


def uses_level_up_junk(location: dict, options) -> bool:
    if location.get("type") != "level_up":
        return False
    limit = _option_value(options, "level_up_limit")
    return limit is not None and int(location["source_id"]) > int(limit)


def uses_replace_location_pool(location: dict, options) -> bool:
    if uses_junk_chests(location, options):
        return True
    if ingredient_spot_is_junk(location):
        return True
    workshop_option = WORKSHOP_OPTIONS.get(location.get("type"))
    if workshop_option:
        return synthesis_is_junk(location, options) or _is_replace_location_pool_option(options, workshop_option)
    if uses_level_up_junk(location, options) or uses_minigame_rank_junk(location, options):
        return True
    minigame_option = minigame_pool_option(location)
    if minigame_option:
        return _is_replace_location_pool_option(options, minigame_option)
    world = str(location.get("world") or "")
    for option_name in (
        REWARD_LOCATION_POOL_OPTIONS.get(world),
        WORLD_LOCATION_POOL_OPTIONS.get(world),
    ):
        if option_name and _is_replace_location_pool_option(options, option_name):
            return True
    return False


def uses_minigame_rank_junk(location: dict, options) -> bool:
    from .Minigames import MINIGAME_RANK_OPTIONS, minigame_reward_rank
    for option in MINIGAME_RANK_OPTIONS:
        rank = minigame_reward_rank(location, option)
        if rank is not None:
            cap = _option_value(options, option)
            # Old seeds and tracker metadata retain the default A cap.
            return rank > (4 if cap is None else int(cap))
    return False


def uses_replace_golden_herc_rewards(location: dict, options) -> bool:
    return (
        location.get("source_table") == "Event"
        and location.get("source_id") in GOLDEN_HERC_REWARD_SOURCE_IDS
        and bool(_option_value(options, "junk_golden_herc_rewards"))
    )


def uses_replace_classic_kingdom_reward(location: dict, options) -> bool:
    return (
        location.get("source_table") == "Event"
        and location.get("source_id") == CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID
        and bool(_option_value(options, "junk_classic_kingdom_reward"))
    )


def uses_junk_chests(location: dict, options) -> bool:
    option = {
        "TreasureBX": "junk_san_fransokyo_chests",
        "TreasureCA": "junk_the_caribbean_chests",
    }.get(location.get("source_table"))
    return location.get("type") == "treasure" and bool(option and _option_value(options, option))


def uses_strict_junk_setting(location: dict, options) -> bool:
    if uses_junk_chests(location, options):
        return True
    if uses_replace_golden_herc_rewards(location, options) or uses_replace_classic_kingdom_reward(location, options):
        return True
    return False


def build_item_name_to_id(data: dict) -> dict[str, int]:
    item_names = {
        resolve_item_display_name(item["name"])
        for item in data["items"]
        if not _is_invalid_item_name(resolve_item_display_name(item["name"]))
    }
    item_names.update(PROOF_ITEMS)
    from .Minigames import MINIGAME_APPENDED_ITEMS
    item_names.difference_update(MINIGAME_APPENDED_ITEMS)
    appended_item_names = item_names.intersection(APPENDED_ITEM_ID_NAMES)
    item_names.difference_update(appended_item_names)
    base_id = 0x4B483000
    # Numeric DataTable values and the unsupported vanilla Orichalcum+ are not
    # AP items. Reserve their source offsets when building the item table.
    reserved_item_name_to_id = {
        name: base_id + index
        for index, name in enumerate(sorted(item_names))
    }
    item_name_to_id = {
        name: item_id
        for name, item_id in reserved_item_name_to_id.items()
        if not name.strip().isdecimal() and name != "Orichalcum+" and name not in PROOF_ITEMS
    }
    next_index = len(reserved_item_name_to_id)
    for item_name in CONTROLLER_UNLOCK_ITEM_NAMES:
        # Links was published after the existing item tails; do not shift IDs.
        if item_name in {"Unlock: Links", SPELLWEAVER_UNLOCK_ITEM}:
            continue
        if item_name not in item_name_to_id:
            item_name_to_id[item_name] = base_id + next_index
            next_index += 1
    for item_name in sorted(DEFAULT_BASE_ABILITY_ITEM_NAMES):
        if item_name not in item_name_to_id:
            item_name_to_id[item_name] = base_id + next_index
            next_index += 1
    for item_name in sorted(appended_item_names):
        item_name_to_id[item_name] = base_id + next_index
        next_index += 1
    for item_name in AP_ONLY_ITEM_ID_NAMES:
        if item_name not in item_name_to_id:
            item_name_to_id[item_name] = base_id + next_index
            next_index += 1
    for item_name in CLASSIC_KINGDOM_APPENDED_NATIVE_ITEM_NAMES:
        if item_name not in item_name_to_id:
            item_name_to_id[item_name] = base_id + next_index
            next_index += 1
    # Party member unlock IDs were published after every prior AP/native tail.
    # Advance across retired reservations so roster
    # removals cannot renumber existing items.
    for item_name in PARTY_MEMBER_UNLOCK_ID_RESERVATIONS:
        if (
            item_name in PARTY_MEMBER_UNLOCK_ITEMS
            and item_name not in item_name_to_id
        ):
            item_name_to_id[item_name] = base_id + next_index
        next_index += 1
    for item_name in KEYBLADE_STYLE_ITEM_NAMES:
        item_name_to_id[item_name] = base_id + next_index
        next_index += 1
    for item_name in MINIGAME_APPENDED_ITEMS:
        item_name_to_id[item_name] = base_id + next_index
        next_index += 1
    item_name_to_id["Unlock: Links"] = base_id + next_index
    next_index += 1
    for item_name in CHARACTER_UNLOCK_ITEM_NAMES:
        if item_name == "Playable Character: Riku":
            continue  # Appended below to preserve the published item IDs.
        item_name_to_id[item_name] = base_id + next_index
        next_index += 1
    item_name_to_id[SPELLWEAVER_UNLOCK_ITEM] = base_id + next_index
    next_index += 1
    item_name_to_id[KING_MICKEY_TRAP_ITEM_NAME] = base_id + next_index
    next_index += 1
    item_name_to_id["Playable Character: Riku"] = base_id + next_index
    next_index += 1
    from .Space import SPACE_ITEM_IDS
    item_name_to_id.update(SPACE_ITEM_IDS)
    return item_name_to_id


def filter_locations_for_options(locations: list[dict], options) -> list[dict]:
    filtered: list[dict] = []
    for location in locations:
        workshop_option = WORKSHOP_OPTIONS.get(location.get("type"))
        if location.get("type") == "adversary" and not _option_value(options, "adversary_checks"):
            continue
        if workshop_option and _option_value(options, workshop_option) in (None, VANILLA_LOCATION_POOL_VALUE):
            continue
        if location.get("type") == "photo_mission" and any(
            _is_replace_location_pool_option(options, WORLD_LOCATION_POOL_OPTIONS[world])
            for world in PHOTO_MISSION_COMPLETION_WORLDS[location["source_id"]]
        ):
            # A junked subject world must not create an AP reason to visit it.
            # Keep catalog identities reserved, but omit the check and its pool item.
            continue
        # Disabled rows remain in extracted_locations.json so their published AP
        # location IDs stay reserved, but they must never enter a generated world.
        if location.get("ap_enabled") is False:
            continue
        if location.get("type") == "ingredient":
            required_mode = 2 if location["source_table"] == "IngredientSpot" else 1
            if _option_value(options, "ingredients") != required_mode:
                continue
        if is_always_on_location(location):
            continue
        classic_kingdom_check_kind = location.get("classic_kingdom_check_kind")
        if classic_kingdom_check_kind:
            try:
                classic_kingdom_check_mode = int(
                    _option_value(options, "classic_kingdom_checks") or 0
                )
            except (TypeError, ValueError):
                classic_kingdom_check_mode = CLASSIC_KINGDOM_CHECKS_OFF
            if (
                classic_kingdom_check_kind == CLASSIC_KINGDOM_HIGH_SCORE_CHECK_KIND
                and classic_kingdom_check_mode < CLASSIC_KINGDOM_CHECKS_HIGH_SCORE
            ):
                continue
        if location.get("world") == "Olympus Coliseum" and _is_location_pool_option_value(
            options, "olympus_coliseum_pool", VANILLA_LOCATION_POOL_VALUE, "off"
        ):
            continue
        world = location["world"]
        if location.get("type") == "shop":
            try:
                shop_index = int(location.get("shop_index") or 0)
            except (TypeError, ValueError):
                shop_index = 0
            if shop_index <= 0 or shop_index > int(options.moogle_shop_checks.value):
                continue
        if world == "Lucky Emblems":
            try:
                milestone = int(str(location.get("source_id") or "0"))
            except ValueError:
                milestone = 0
            if milestone > int(options.lucky_emblem_limit.value):
                continue
            raw_batch_size = _option_value(options, "lucky_emblems_per_check")
            try:
                batch_size = int(raw_batch_size or 0)
            except (TypeError, ValueError):
                batch_size = 0
            if batch_size > 0:
                if milestone <= 0 or milestone % batch_size != 0:
                    continue
            elif milestone not in VANILLA_LUCKY_EMBLEM_MILESTONES:
                continue
        filtered.append(location)
    return filtered


def build_item_counts(locations: list[dict]) -> Counter[str]:
    return Counter(
        location["primary_item_name"]
        for location in locations
        if not _is_invalid_item_name(location["primary_item_name"])
    )
