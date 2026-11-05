from __future__ import annotations

import json

from .Battlegates import BATTLEGATE_COMPLETION_SOURCES_BY_WORLD, BATTLEGATE_SOURCE_ID_REGION_LOCKS
from .Ingredients import INGREDIENT_JUNK_SPOTS
from .Logic import (
    compile_logic_rules,
    events,
    logic_rule,
    parse_requirement_summary,
    require_all_locations,
    require_any_location,
    treasures,
    validate_location_dependency_cycles,
    vbonuses,
)
from .Minigames import MINIGAME_ITEM_REWARDS, MINIGAME_REWARDS
from .PackageResources import read_package_text

"""
Public-editable KH3 logic rules.

Requirement strings use a small DSL:
- `A + B` means both A and B are required.
- `A OR B` means either route works.
- `A/B` inside one requirement slot means either item can satisfy that slot.
- `AnyOfN(A/B/C)` requires N distinct items from that slot.

Friendly names may omit the `Ability:`, `Magic:`, or `Unlock:` prefix, so
`Block`, `Fire`, and `Wall Run` are normalized by `Logic.py` to their AP item
names. Prefixes and item names are case-insensitive. Any route containing a
magic requirement automatically also requires `Unlock: Magic`.
Heartbinder routes additionally require `Unlock: Links`; explicit Magic
requirements are preserved.
"""


COMMON_COMBAT = "Block + Air Slide + Cure + Dodge Roll"
DATA_BATTLE_COUNTERS = "AnyOf1(Counter Slash/Counter Kick)"
DATA_BATTLE_COMMON_COMBAT = (
    f"{COMMON_COMBAT} + Second Chance + Withstand Combo + Glide + {DATA_BATTLE_COUNTERS}"
)
DATA_BATTLE_MARLUXIA_COMBAT = f"{DATA_BATTLE_COMMON_COMBAT} + Fire"
KEYBLADE_GRAVEYARD_COMMON_COMBAT = f"{COMMON_COMBAT} + Attraction Flow"
SAN_FRANSOKYO_POLE_SPIN_MAGIC = (
    "Pole Spin + Fire + Blizzard/Water + Thunder"
)
LINK_HEARTBINDER_ITEM_NAMES = (
    "Dream Heartbinder",
    "Pixel Heartbinder",
    "'Ohana Heartbinder",
    "Pride Heartbinder",
    "Ocean Heartbinder",
)
MONSTROPOLIS_FINAL_COMBAT = (
    "AnyOf2(Block/Air Slide/Dodge Roll) + Unlock: Magic"
)


LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENT_SUMMARIES = {
    "Olympus": "Wall Run OR Super Jump + Wall Kick + Air Slide + Wall Run + Double Flight",
}

LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENTS = {
    region_name: parse_requirement_summary(summary)
    for region_name, summary in LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENT_SUMMARIES.items()
}


CHEST_LOGIC_RULES = (
    logic_rule(
        "Advanced movement chests",
        treasures("TreasureHE", (
            "HE_SBOX_011",
            "HE_SBOX_012",
            "HE_SBOX_013",
            "HE_SBOX_014",
            "HE_SBOX_015",
            "HE_SBOX_016",
            "HE_LBOX_002",
        )) + treasures("TreasureMI", (
            "MI_LBOX_003",
            "MI_SBOX_011",
            "MI_SBOX_012",
            "MI_SBOX_013",
            "MI_SBOX_014",
            "MI_SBOX_018",
        )) + treasures("TreasureCA", (
            "CA_SBOX_016",
        )),
        "Wall Run OR Super Jump + Wall Kick + Air Slide + Double Flight",
    ),
    logic_rule(
        "Monstropolis elevator chest",
        treasures("TreasureMI", ("MI_SBOX_019",)),
        "Wall Run + AnyOf2(Block/Air Slide/Dodge Roll) OR "
        "Super Jump + Wall Kick + Air Slide + Double Flight + "
        "AnyOf2(Block/Air Slide/Dodge Roll)",
    ),
    logic_rule(
        "Wall Run chests",
        treasures("TreasureHE", (
            "HE_SBOX_017",
            "HE_SBOX_020",
            "HE_SBOX_021",
            "HE_SBOX_001",
            "HE_SBOX_002",
            "HE_SBOX_003",
            "HE_SBOX_004",
            "HE_LBOX_001",
            "HE_SBOX_006",
            "HE_LBOX_003",
            "HE_SBOX_008",
        )) + treasures("TreasureRA", (
            "RA_SBOX_015",
            "RA_SBOX_016",
            "RA_LBOX_006",
            "RA_SBOX_011",
            "RA_SBOX_012",
            "RA_SBOX_013",
            "RA_SBOX_014",
            "RA_SBOX_017",
            "RA_LBOX_004",
            "RA_SBOX_018",
            "RA_LBOX_005",
            "RA_SBOX_019",
            "RA_SBOX_020",
            "RA_SBOX_021",
            "RA_SBOX_022",
        )) + treasures("TreasureFZ", (
            "FZ_LBOX_002",
            "FZ_SBOX_012",
            "FZ_LBOX_003",
        )) + treasures("TreasureCA", (
            "CA_LBOX_008",
            "CA_SBOX_026",
            "CA_SBOX_023",
            "CA_SBOX_024",
            "CA_SBOX_013",
        )),
        "Wall Run",
    ),
    logic_rule(
        "Re Mind post-fight chests",
        treasures("TreasureBT", (
            "BT_LBOX_002",
            "BT_SBOX_001",
            "BT_SBOX_002",
            "BT_SBOX_003",
            "BT_SBOX_004",
            "BT_SBOX_005",
            "BT_SBOX_006",
            "BT_SBOX_007",
            "BT_LBOX_001",
        )),
        f"Wall Run + {COMMON_COMBAT}",
    ),
    logic_rule(
        "Olympus upper movement chests",
        treasures("TreasureHE", (
            "HE_SBOX_009",
            "HE_SBOX_010",
            "HE_SBOX_024",
            "HE_SBOX_022",
            "HE_SBOX_023",
            "HE_SBOX_027",
            "HE_SBOX_030",
            "HE_SBOX_028",
            "HE_SBOX_029",
            "HE_SBOX_031",
            "HE_SBOX_033",
            "HE_LBOX_004",
            "HE_SBOX_025",
            "HE_SBOX_026",
        )),
        "Wall Run OR Super Jump + Wall Kick + Air Slide + Double Flight OR "
        "Super Jump + High Jump + Wall Kick + Air Slide",
    ),
    logic_rule(
        "Toy Box lower vent chest",
        treasures("TreasureTS", ("TS_SBOX_001",)),
        "Wall Run OR Double Flight OR Super Jump + Aero OR Super Jump + Wall Kick + Air Slide",
    ),
    logic_rule(
        "Toy Box High Jump and Double Flight chests",
        treasures("TreasureTS", (
            "TS_SBOX_011",
            "TS_SBOX_012",
            "TS_SBOX_013",
            "TS_SBOX_014",
            "TS_SBOX_018",
            "TS_SBOX_019",
            "TS_SBOX_020",
            "TS_SBOX_022",
            "TS_LBOX_005",
            "TS_SBOX_008",
            "TS_SBOX_009",
            "TS_LBOX_004",
            "TS_SBOX_017",
        )),
        "Wall Run OR High Jump + Double Flight",
    ),
    logic_rule(
        "Toy Box flexible air movement chest",
        treasures("TreasureTS", ("TS_SBOX_015",)),
        "Wall Run OR Air Slide OR Super Jump + Wall Kick + Air Slide OR Super Jump + Aero",
    ),
    logic_rule(
        "Toy Box Wall Run or Double Flight chest",
        treasures("TreasureTS", ("TS_SBOX_007",)),
        "Wall Run OR Double Flight",
    ),
    logic_rule(
        "Kingdom of Corona basic vertical chest",
        treasures("TreasureRA", ("RA_SBOX_002",)),
        "High Jump OR Double Flight",
    ),
    logic_rule(
        "Kingdom of Corona tower chest",
        treasures("TreasureRA", ("RA_SBOX_010",)),
        "Wall Run + Double Flight OR Super Jump + Wall Kick + Air Slide + Wall Run OR "
        "Super Jump + Aero + Wall Run",
    ),
    logic_rule(
        "Arendelle Pole Spin chests",
        treasures("TreasureFZ", (
            "FZ_LBOX_005",
            "FZ_SBOX_011",
            "FZ_SBOX_013",
            "FZ_SBOX_029",
            "FZ_SBOX_023",
            "FZ_SBOX_024",
            "FZ_SBOX_025",
            "FZ_SBOX_004",
            "FZ_SBOX_005",
            "FZ_LBOX_004",
            "FZ_LBOX_006",
            "FZ_SBOX_016",
            "FZ_SBOX_019",
        )),
        "Air Slide + Wall Run + Pole Spin",
    ),
    logic_rule(
        "Arendelle Super Jump Pole Spin chest",
        treasures("TreasureFZ", ("FZ_SBOX_015",)),
        "Super Jump + Air Slide + Wall Run + Pole Spin",
    ),
    logic_rule(
        "Arendelle high route Pole Spin chest",
        treasures("TreasureFZ", ("FZ_SBOX_027",)),
        "High Jump + Air Slide + Wall Run + Pole Spin OR "
        "Air Slide + Wall Run + Double Flight + Pole Spin OR "
        "Super Jump + Wall Kick + Air Slide + Wall Run + Pole Spin OR "
        "Super Jump + Aero + Air Slide + Wall Run + Pole Spin",
    ),
    logic_rule(
        "Arendelle flexible Pole Spin chest",
        treasures("TreasureFZ", ("FZ_SBOX_007",)),
        "Air Slide + Glide + Wall Run + Pole Spin OR "
        "Air Slide + Wall Run + Pole Spin OR "
        "Super Jump + Aero + Air Slide + Wall Run + Pole Spin OR "
        "Super Jump + Wall Kick + Air Slide + Wall Run + Pole Spin",
    ),
    logic_rule(
        "Arendelle broad Pole Spin chest",
        treasures("TreasureFZ", ("FZ_SBOX_017",)),
        "Air Slide + Wall Run + Pole Spin OR "
        "High Jump + Air Slide + Wall Run + Pole Spin OR "
        "Air Slide + Combo Master + Wall Run + Pole Spin OR "
        "Super Jump + Wall Kick + Air Slide + Wall Run + Pole Spin OR "
        "Super Jump + Aero + Air Slide + Wall Run + Pole Spin OR "
        "Air Slide + Glide + Wall Run + Pole Spin",
    ),
    logic_rule(
        "Arendelle short Pole Spin route chest",
        treasures("TreasureFZ", ("FZ_SBOX_020",)),
        "Air Slide + Glide + Wall Run + Pole Spin OR "
        "Air Slide + Wall Run + Pole Spin OR "
        "Super Jump + Wall Kick + Air Slide + Wall Run + Pole Spin",
    ),
    logic_rule(
        "Arendelle Double Flight Pole Spin chest",
        treasures("TreasureFZ", ("FZ_SBOX_022",)),
        "Air Slide + Wall Run + Double Flight + Pole Spin",
    ),
    logic_rule(
        "Caribbean flexible movement chest",
        treasures("TreasureCA", ("CA_LBOX_011",)),
        "Wall Run OR Glide OR Air Slide",
    ),
    logic_rule(
        "Caribbean high movement chests",
        treasures("TreasureCA", (
            "CA_SBOX_017",
            "CA_SBOX_032",
            "CA_SBOX_033",
            "CA_SBOX_034",
            "CA_SBOX_035",
            "CA_SBOX_036",
            "CA_SBOX_037",
            "CA_SBOX_038",
            "CA_SBOX_039",
            "CA_SBOX_040",
            "CA_SBOX_041",
        )),
        "High Jump + Glide + Wall Run OR "
        "High Jump + Air Slide + Wall Run OR "
        "High Jump + Combo Master + Wall Run OR "
        "Super Jump + Aero",
    ),
    logic_rule(
        "Keyblade Graveyard and Re Mind shared combat chests",
        treasures("TreasureKG", (
            "KG_LBOX_002",
            "KG_SBOX_001",
            "KG_SBOX_002",
            "KG_SBOX_003",
        )),
        f"{COMMON_COMBAT} + Attraction Flow",
    ),
)


VICTORY_BONUS_LOGIC_RULES = (
    logic_rule(
        "Wall Run victory bonuses",
        vbonuses((
            "Vbonus_010",
            "Vbonus_023",
            "Vbonus_027",
            "Vbonus_041",
        )),
        "Wall Run",
    ),
    logic_rule(
        "Corona Grim Guardianess additional defense",
        vbonuses(("Vbonus_030",)),
        "AnyOf2(Block/Air Slide/Dodge Roll)",
    ),
    logic_rule(
        "Olympus Agora Flame Cores movement",
        vbonuses((
            "Vbonus_002",
        )),
        "Wall Run OR Super Jump + Wall Kick + Air Slide + Double Flight OR "
        "Super Jump + High Jump + Wall Kick + Air Slide",
    ),
    logic_rule(
        "Toy Box Angelic Amber movement",
        vbonuses((
            "Vbonus_020",
        )),
        "Wall Run OR High Jump + Double Flight",
    ),
    logic_rule(
        "Monstropolis Tank Yard Heartless movement",
        vbonuses((
            "Vbonus_038",
        )),
        "Wall Run OR Super Jump + Wall Kick + Air Dash x1",
    ),
    logic_rule(
        "Monstropolis Lump of Horror additional combat",
        vbonuses(("Vbonus_040",)),
        MONSTROPOLIS_FINAL_COMBAT,
    ),
    logic_rule(
        "Arendelle Ninjas II additional movement",
        vbonuses((
            "Vbonus_043",
        )),
        "Pole Spin + Air Slide",
    ),
    logic_rule(
        "San Fransokyo Pole Spin magic victory bonuses",
        vbonuses((
            "Vbonus_057",
            "Vbonus_055",
            "Vbonus_056",
        )),
        SAN_FRANSOKYO_POLE_SPIN_MAGIC,
    ),
    logic_rule(
        "Dark World combat victory bonus",
        vbonuses(("Vbonus_067",)),
        COMMON_COMBAT,
    ),
    logic_rule(
        "Keyblade Graveyard combat victory bonuses",
        vbonuses((
            "Vbonus_068",
            "Vbonus_069",
            "Vbonus_070",
            "Vbonus_071",
            "Vbonus_072",
            "Vbonus_073",
            "Vbonus_074",
            "Vbonus_076",
        )),
        KEYBLADE_GRAVEYARD_COMMON_COMBAT,
    ),
    logic_rule(
        "Keyblade Graveyard tower combat victory bonus",
        vbonuses(("Vbonus_075",)),
        f"Wall Run + {KEYBLADE_GRAVEYARD_COMMON_COMBAT}",
    ),
    logic_rule(
        "Keyblade Graveyard attraction-flow-only victory bonuses",
        vbonuses((
            "Vbonus_083",
            "Vbonus_084",
        )),
        "Attraction Flow",
    ),
    logic_rule(
        "Re Mind combat victory bonuses",
        vbonuses((
            "VBonus_DLC_001",
            "VBonus_DLC_002",
            "VBonus_DLC_003",
            "VBonus_DLC_004",
            "VBonus_DLC_005",
            "VBonus_DLC_006",
            "VBonus_DLC_007",
        )),
        COMMON_COMBAT,
    ),
    logic_rule(
        "Re Mind wall-run combat victory bonuses",
        vbonuses((
            "VBonus_DLC_015",
            "VBonus_DLC_010",
            "VBonus_DLC_008",
        )),
        f"Wall Run + {COMMON_COMBAT}",
    ),
)


BLOCK_AIR_SLIDE_CURE_EVENT_SOURCE_IDS = (
    "EVENT_REPORT_000",
    "EVENT_REPORT_001a",
    "EVENT_REPORT_002a",
    "EVENT_REPORT_003a",
    "EVENT_REPORT_004a",
    "EVENT_REPORT_005a",
    "EVENT_REPORT_006a",
    "EVENT_REPORT_007a",
    "EVENT_REPORT_008a",
    "EVENT_REPORT_009a",
    "EVENT_REPORT_010a",
    "EVENT_REPORT_011a",
    "EVENT_REPORT_012a",
    "EVENT_REPORT_013a",
    "EVENT_REPORT_001b",
    "EVENT_REPORT_002b",
    "EVENT_REPORT_003b",
    "EVENT_REPORT_004b",
    "EVENT_REPORT_005b",
    "EVENT_REPORT_006b",
    "EVENT_REPORT_007b",
    "EVENT_REPORT_008b",
    "EVENT_REPORT_009b",
    "EVENT_REPORT_010b",
    "EVENT_REPORT_011b",
    "EVENT_REPORT_012b",
    "EVENT_REPORT_013b",
    "EVENT_REPORT_014",
)

DATA_BATTLE_COMMON_COMBAT_EVENT_SOURCE_IDS = (
    "EVENT_DATAB_001",
    "EVENT_DATAB_002",
    "EVENT_DATAB_003",
    "EVENT_DATAB_004",
    "EVENT_DATAB_005",
    "EVENT_DATAB_006",
    "EVENT_DATAB_009",
    "EVENT_DATAB_010",
    "EVENT_DATAB_011",
    "EVENT_DATAB_012",
    "EVENT_DATAB_013",
)

EVENT_LOGIC_RULES = (
    logic_rule(
        "Monstropolis Pride Heartbinder additional movement",
        events((
            "EVENT_HEARTBINDER_003",
        )),
        "Wall Run OR Double Flight",
    ),
    logic_rule(
        "San Fransokyo Pole Spin magic events",
        events((
            "EVENT_009",
            "EVENT_KEYBLADE_009",
        )),
        SAN_FRANSOKYO_POLE_SPIN_MAGIC,
    ),
    logic_rule(
        "Keyblade Graveyard keyblade event",
        events(("EVENT_KEYBLADE_011",)),
        KEYBLADE_GRAVEYARD_COMMON_COMBAT,
    ),
    logic_rule(
        "Common combat events",
        events(BLOCK_AIR_SLIDE_CURE_EVENT_SOURCE_IDS),
        COMMON_COMBAT,
    ),
    logic_rule(
        "Data Battle common combat events",
        events(DATA_BATTLE_COMMON_COMBAT_EVENT_SOURCE_IDS),
        DATA_BATTLE_COMMON_COMBAT,
    ),
    logic_rule(
        "Unreality combat events",
        events(("EVENT_KEYITEM_005", "EVENT_YOZORA_001")),
        DATA_BATTLE_COMMON_COMBAT,
    ),
    logic_rule(
        "Data Marluxia combat event",
        events(("EVENT_DATAB_007",)),
        DATA_BATTLE_MARLUXIA_COMBAT,
    ),
    logic_rule(
        "Data Saix combat event",
        events(("EVENT_DATAB_008",)),
        f"{DATA_BATTLE_COMMON_COMBAT} + Blizzard",
    ),
)


CHEST_LOGIC_REQUIREMENTS = compile_logic_rules(CHEST_LOGIC_RULES)
VICTORY_BONUS_LOGIC_REQUIREMENTS = compile_logic_rules(VICTORY_BONUS_LOGIC_RULES)
EVENT_LOGIC_REQUIREMENTS = compile_logic_rules(EVENT_LOGIC_RULES)

TEN_THOUSAND_HEARTLESS_FIGHT_DEPENDENCY = require_any_location(
    ("VBonus", "Vbonus_069"),
    ("VBonus", "Vbonus_076"),
)
KEYBLADE_GRAVEYARD_POST_TEN_THOUSAND_HEARTLESS_SOURCE_KEYS = (
    ("TreasureKG", "KG_LBOX_001"),
    ("TreasureKG", "KG_SBOX_004"),
    ("VBonus", "Vbonus_068"),
    ("VBonus", "Vbonus_070"),
    ("VBonus", "Vbonus_071"),
    ("VBonus", "Vbonus_072"),
    ("VBonus", "Vbonus_073"),
    ("VBonus", "Vbonus_074"),
    ("VBonus", "Vbonus_075"),
    ("VBonus", "Vbonus_083"),  # After collecting 222 Soras in The Final World
    ("VBonus", "Vbonus_084"),  # After collecting 333 Soras in The Final World
)

CARIBBEAN_PRE_LIGHTNING_ANGLER_CHEST_SOURCE_IDS = frozenset({
    "CA_LBOX_005",  # Chest 20: Huddled Isles map, near the save point
    "CA_SBOX_010",  # Chest 1: Huddled Isles sunken ship
    "CA_SBOX_021",  # Chest 21: same sunken ship
    "CA_SBOX_022",  # Chest 22: same sunken ship
    "CA_SBOX_011",  # Chest 2: Undersea Cavern second dead end
    "CA_SBOX_012",  # Chest 3: Undersea Cavern third dead end
    "CA_LBOX_007",  # Chest 4: Undersea Cavern fourth dead end
})
CARIBBEAN_POST_LIGHTNING_ANGLER_CHEST_SOURCE_KEYS = tuple(
    ("TreasureCA", source_id)
    for prefix, count in (("CA_LBOX", 11), ("CA_SBOX", 45))
    for number in range(1, count + 1)
    if (source_id := f"{prefix}_{number:03}") not in CARIBBEAN_PRE_LIGHTNING_ANGLER_CHEST_SOURCE_IDS
)

# A dependency is added to a location's own region and item requirements. Use
# require_all_locations(...) when every prerequisite must be logically
# reachable, or require_any_location(...) when any one prerequisite is enough.
LOCATION_LOGIC_DEPENDENCIES = {
    # Darkside is the unconditional opening fight before GoA.
    # Olympus starts with Cliff Ascent after obtaining its portal keyblade.
    # Agora Flame Cores owns the movement alternatives; Rock Titan adds Wall Run.
    ("VBonus", "Vbonus_002"): require_all_locations(
        ("VBonus", "Vbonus_001"),  # Cliff Ascent Heartless
    ),
    ("VBonus", "Vbonus_005"): require_all_locations(
        ("VBonus", "Vbonus_002"),  # Agora Flame Cores
    ),
    ("VBonus", "Vbonus_006"): require_all_locations(
        ("VBonus", "Vbonus_005"),  # Overlook Flame Cores
    ),
    ("VBonus", "Vbonus_007"): require_all_locations(
        ("VBonus", "Vbonus_006"),  # Gardens Flame Cores
    ),
    ("VBonus", "Vbonus_008"): require_all_locations(
        ("VBonus", "Vbonus_007"),  # Alleyway Heartless
    ),
    ("VBonus", "Vbonus_010"): require_all_locations(
        ("VBonus", "Vbonus_008"),  # Agora Rock Troll
    ),
    ("VBonus", "Vbonus_011"): require_all_locations(
        ("VBonus", "Vbonus_010"),  # Rock Titan
    ),
    ("VBonus", "Vbonus_013"): require_all_locations(
        ("VBonus", "Vbonus_011"),  # Courtyard Satyrs
    ),
    ("Event", "EVENT_KEYBLADE_001"): require_all_locations(
        ("VBonus", "Vbonus_013"),  # Tornado Titan
    ),
    ("Event", "EVENT_001"): require_all_locations(
        ("VBonus", "Vbonus_011"),  # Forge branch after Courtyard Satyrs
    ),
    **{
        ("Event", source_id): require_all_locations(("Event", "EVENT_KEYBLADE_001"))
        for source_id in ("EVENT_002", "EVENT_003", "EVENT_004", "EVENT_005", "EVENT_006")
    },
    ("Event", "EVENT_007"): require_all_locations(
        ("Event", "EVENT_002"),  # All five figures; no order among them
        ("Event", "EVENT_003"),
        ("Event", "EVENT_004"),
        ("Event", "EVENT_005"),
        ("Event", "EVENT_006"),
    ),
    # Twilight Town story order. The opening fight inherits the existing
    # portal defense gate; later checks inherit each earlier fight's logic.
    ("VBonus", "Vbonus_015"): require_all_locations(
        ("VBonus", "Vbonus_014"),  # Tram Common Demon Tide
    ),
    ("VBonus", "Vbonus_016"): require_all_locations(
        ("VBonus", "Vbonus_015"),  # The Woods Powerwilds
    ),
    ("Event", "EVENT_CKGAME_001"): require_all_locations(
        ("VBonus", "Vbonus_016"),  # Old Mansion Heartless and Nobodies
    ),
    ("Event", "EVENT_KEYBLADE_002"): require_all_locations(
        ("Event", "EVENT_CKGAME_001"),  # Giantland
    ),
    # Toy Box story order. Angelic Amber owns the shared movement alternatives;
    # King of Toys still adds its own mandatory Wall Run requirement.
    ("VBonus", "Vbonus_018"): require_all_locations(
        ("VBonus", "Vbonus_017"),  # Andy's House Heartless
    ),
    ("VBonus", "Vbonus_019"): require_all_locations(
        ("VBonus", "Vbonus_018"),  # Galaxy Toys 1F Gigas
    ),
    ("VBonus", "Vbonus_020"): require_all_locations(
        ("VBonus", "Vbonus_019"),  # Action Figures Supreme Smashers
    ),
    ("VBonus", "Vbonus_021"): require_all_locations(
        ("VBonus", "Vbonus_020"),  # Angelic Amber
    ),
    ("VBonus", "Vbonus_022"): require_all_locations(
        ("VBonus", "Vbonus_021"),  # UFO mini-boss
    ),
    ("Event", "EVENT_HEARTBINDER_002"): require_all_locations(
        ("VBonus", "Vbonus_022"),  # Story Verum Rex
    ),
    ("VBonus", "Vbonus_023"): require_all_locations(
        ("Event", "EVENT_HEARTBINDER_002"),  # Pixel Heartbinder story event
    ),
    ("Event", "EVENT_KEYBLADE_003"): require_all_locations(
        ("VBonus", "Vbonus_023"),  # King of Toys
    ),
    # Corona story order. Chaos Carriage owns Wall Run; Grim Guardianess
    # adds two distinct defensive abilities, also inherited by Ever After.
    ("VBonus", "Vbonus_025"): require_all_locations(
        ("VBonus", "Vbonus_024"),  # Forest Hills Heartless
    ),
    ("VBonus", "Vbonus_026"): require_all_locations(
        ("VBonus", "Vbonus_025"),  # Chief Puffs and Puffballs
    ),
    ("VBonus", "Vbonus_027"): require_all_locations(
        ("VBonus", "Vbonus_026"),  # Forest Hills Reapers
    ),
    ("VBonus", "Vbonus_028"): require_all_locations(
        ("VBonus", "Vbonus_027"),  # Chaos Carriage
    ),
    ("VBonus", "Vbonus_029"): require_all_locations(
        ("VBonus", "Vbonus_028"),  # Wharf Nobodies
    ),
    ("VBonus", "Vbonus_030"): require_all_locations(
        ("VBonus", "Vbonus_029"),  # Tower Heartless
    ),
    ("Event", "EVENT_KEYBLADE_004"): require_all_locations(
        ("VBonus", "Vbonus_030"),  # Grim Guardianess
    ),
    # Monstropolis story order. Pride Heartbinder adds Double Flight on the
    # non-Wall Run route; Lump of Horror adds its own combat requirements.
    ("VBonus", "Vbonus_033"): require_all_locations(
        ("VBonus", "Vbonus_032"),  # Lobby and Offices Unversed
    ),
    ("VBonus", "Vbonus_034"): require_all_locations(
        ("VBonus", "Vbonus_033"),  # Laugh Floor Unversed
    ),
    ("VBonus", "Vbonus_035"): require_all_locations(
        ("VBonus", "Vbonus_034"),  # Door Vault Upper Level Heartless
    ),
    ("VBonus", "Vbonus_036"): require_all_locations(
        ("VBonus", "Vbonus_035"),  # Factory Second Floor Heartless and Unversed
    ),
    ("Event", "EVENT_008"): require_all_locations(
        ("VBonus", "Vbonus_036"),  # Factory Second Floor Unversed
    ),
    ("VBonus", "Vbonus_037"): require_all_locations(
        ("Event", "EVENT_008"),  # Laughter Pin story event
    ),
    ("VBonus", "Vbonus_038"): require_all_locations(
        ("VBonus", "Vbonus_037"),  # Power Plant Accessway Heartless and Unversed
    ),
    ("Event", "EVENT_HEARTBINDER_003"): require_all_locations(
        ("VBonus", "Vbonus_038"),  # Tank Yard Heartless
    ),
    ("VBonus", "Vbonus_039"): require_all_locations(
        ("Event", "EVENT_HEARTBINDER_003"),  # Pride Heartbinder story event
    ),
    ("VBonus", "Vbonus_040"): require_all_locations(
        ("VBonus", "Vbonus_039"),  # Tank Yard Unversed
    ),
    ("Event", "EVENT_KEYBLADE_005"): require_all_locations(
        ("VBonus", "Vbonus_040"),  # Lump of Horror
    ),
    # Arendelle story order. The opening fight owns Wall Run; Ninjas II
    # adds Pole Spin and Air Slide, inherited by the rest of the story.
    ("VBonus", "Vbonus_042"): require_all_locations(
        ("VBonus", "Vbonus_041"),  # Rock Troll and Winterhorns
    ),
    ("VBonus", "Vbonus_043"): require_all_locations(
        ("VBonus", "Vbonus_042"),  # Labyrinth Ninjas I
    ),
    ("VBonus", "Vbonus_044"): require_all_locations(
        ("VBonus", "Vbonus_043"),  # Labyrinth Ninjas II
    ),
    ("VBonus", "Vbonus_045"): require_all_locations(
        ("VBonus", "Vbonus_044"),  # Labyrinth Ninjas III
    ),
    ("VBonus", "Vbonus_047"): require_all_locations(
        ("VBonus", "Vbonus_045"),  # Labyrinth Ninjas IV
    ),
    ("VBonus", "Vbonus_048"): require_all_locations(
        ("VBonus", "Vbonus_047"),  # Marshmallow
    ),
    ("VBonus", "Vbonus_049"): require_all_locations(
        ("VBonus", "Vbonus_048"),  # Story Frozen Slider Frost Serpents
    ),
    ("VBonus", "Vbonus_050"): require_all_locations(
        ("VBonus", "Vbonus_049"),  # Valley of Ice Heartless
    ),
    ("Event", "EVENT_KEYBLADE_007"): require_all_locations(
        ("VBonus", "Vbonus_050"),  # Skoll
    ),
    # Caribbean story order. The opening fight uses the existing portal
    # requirements; each later check inherits any earlier encounter logic.
    ("VBonus", "Vbonus_059"): require_all_locations(
        ("VBonus", "Vbonus_058"),  # Davy Jones' Locker Anchor Raiders
    ),
    ("VBonus", "Vbonus_060"): require_all_locations(
        ("VBonus", "Vbonus_059"),  # First Ship Battle: Over the Edge
    ),
    ("VBonus", "Vbonus_061"): require_all_locations(
        ("VBonus", "Vbonus_060"),  # Raging Vulture
    ),
    ("VBonus", "Vbonus_062"): require_all_locations(
        ("VBonus", "Vbonus_061"),  # Lightning Angler
    ),
    ("VBonus", "Vbonus_063"): require_all_locations(
        ("VBonus", "Vbonus_062"),  # Race Luxord to Port Royal
    ),
    ("VBonus", "Vbonus_064"): require_all_locations(
        ("VBonus", "Vbonus_063"),  # Second Ship Battle: Port Royal Waters
    ),
    ("VBonus", "Vbonus_065"): require_all_locations(
        ("VBonus", "Vbonus_064"),  # Third Ship Battle: Forsaken Isle
    ),
    ("VBonus", "Vbonus_066"): require_all_locations(
        ("VBonus", "Vbonus_065"),  # Kraken
    ),
    ("Event", "EVENT_KEYBLADE_008"): require_all_locations(
        ("VBonus", "Vbonus_066"),  # Davy Jones
    ),
    **{
        source_key: require_all_locations(("VBonus", "Vbonus_061"))
        for source_key in CARIBBEAN_POST_LIGHTNING_ANGLER_CHEST_SOURCE_KEYS
    },
    **{
        source_key: TEN_THOUSAND_HEARTLESS_FIGHT_DEPENDENCY
        for source_key in KEYBLADE_GRAVEYARD_POST_TEN_THOUSAND_HEARTLESS_SOURCE_KEYS
    },
    ("TreasureEW", "EW_LBOX_001"): TEN_THOUSAND_HEARTLESS_FIGHT_DEPENDENCY,
    ("TreasureMI", "MI_SBOX_014"): require_all_locations(
        ("VBonus", "Vbonus_040"),
    ),
}
# Approximate physical anchors are editable independently of their item rules.
# Chest anchors share their area, movement and story access, not their
# Chestsanity opening locks. Authored aliases offer alternative anchors.
# Ingredient types accept ANY of their catalogued field sources.
_ingredient_dependencies = json.loads(read_package_text(
    "data/ingredient_logic_dependencies.json", module_file=__file__))
for _spot_id, _anchors in _ingredient_dependencies["spots"].items():
    # These reviewed pickup routes do not pass through their old chest's
    # movement gate. Rules.py still enforces the physical source world.
    if _spot_id in {"TS_02_PF_003", "TS_02_PF_006"}:
        continue
    _keys = tuple(tuple(anchor.split(":", 1)) for anchor in _anchors)
    LOCATION_LOGIC_DEPENDENCIES[("IngredientSpot", _spot_id)] = (
        require_all_locations(*_keys) if len(_keys) == 1 else require_any_location(*_keys)
    )
for _ingredient_id, _spot_ids in _ingredient_dependencies["ingredients"].items():
    LOCATION_LOGIC_DEPENDENCIES[("Ingredient", _ingredient_id)] = require_any_location(
        # A random/event-disabled actor must not be the only in-logic route
        # to a standard ingredient check carrying a progression item.
        *(("IngredientSpot", spot_id) for spot_id in _spot_ids if spot_id not in INGREDIENT_JUNK_SPOTS)
    )

# Accepted player review (2026-09-23). Keep chest rules intact: a pickup
# beside a chest can have a different route. The two vent pickups
# TS_02_PF_004/009 deliberately retain their existing chest requirements.
INGREDIENT_LOGIC_REQUIREMENTS = {
    ("IngredientSpot", spot_id): parse_requirement_summary(summary)
    for spot_id, summary in {
        "BX_02_PF_003": "Wall Run",
        "BX_02_PF_016": "Wall Run OR Double Flight",
        "CA_01_PF_013": "Wall Run OR Double Flight",
        "CA_02h_F_013": "Wall Run",
        "TS_02_PF_003": "High Jump OR Double Flight OR Wall Run",
    }.items()
}
# Isle of Luck uses the physical emblem's route, never a photograph or
# LuckyMark reward threshold. Retain the anchor's island/story access.
_isle_of_luck = next(spot for spot in json.loads(read_package_text(
    "data/lucky_emblem_spots.json", module_file=__file__))["spots"]
    if spot["world"] == "The Caribbean" and spot["world_index"] == 8)
for _number in range(1, 8):
    INGREDIENT_LOGIC_REQUIREMENTS[("IngredientSpot", f"CA_02c_F_{_number:03}")] = (
        parse_requirement_summary(_isle_of_luck["logic_requirement_summary"]))

for _event, _spots in {
    "EVENT_KEYBLADE_001": (
        "HE_03_F_018", "HE_03_PF_001", "HE_03_PF_003", "HE_03_PF_014",
        "HE_03_PF_015", "HE_03_PF_024", "HE_03_PF_025",
    ),
    "EVENT_KEYBLADE_004": (
        "RA_02_PF_017", "RA_02_PF_018", "RA_02_PF_019", "RA_02_PF_020",
    ),
}.items():
    for _spot_id in _spots:
        _key = ("IngredientSpot", _spot_id)
        # These reviewed spots each have one chest anchor. Both the original
        # route and the world's final event must be reachable.
        _dependency = LOCATION_LOGIC_DEPENDENCIES[_key]
        assert _dependency.require_all
        LOCATION_LOGIC_DEPENDENCIES[_key] = require_all_locations(
            *_dependency.source_keys, ("Event", _event))
# Use world completion as a conservative route requirement for these optional
# challenges. Depend on its reachability, never the shuffled keyblade reward.
for _suffix, _world, _description, _ability, _completion in MINIGAME_REWARDS:
    LOCATION_LOGIC_DEPENDENCIES[("VBonus", f"VBonus_Minigame{_suffix}")] = require_all_locations(
        ("Event", _completion))
for _reward in MINIGAME_ITEM_REWARDS:
    LOCATION_LOGIC_DEPENDENCIES[("MinigameReward", _reward["source_id"])] = require_all_locations(
        ("Event", _reward["completion"]))
for _source_id, _world in BATTLEGATE_SOURCE_ID_REGION_LOCKS.items():
    LOCATION_LOGIC_DEPENDENCIES[("Event", _source_id)] = require_all_locations(
        BATTLEGATE_COMPLETION_SOURCES_BY_WORLD[_world])
from .Adversaries import ADVERSARIES
for _entry in ADVERSARIES:
    if _entry["name"] == "Sorcerer":
        LOCATION_LOGIC_DEPENDENCIES[("Adversary", _entry["id"])] = require_all_locations(
            ("Event", "EVENT_KEYBLADE_002"))
validate_location_dependency_cycles(LOCATION_LOGIC_DEPENDENCIES)

ALL_LOGIC_RULES = CHEST_LOGIC_RULES + VICTORY_BONUS_LOGIC_RULES + EVENT_LOGIC_RULES
