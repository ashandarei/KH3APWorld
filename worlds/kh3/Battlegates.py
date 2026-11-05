"""Physical worlds and story milestones for Battlegates 0 through 14."""

BATTLEGATE_ZERO_SOURCE_ID = "EVENT_REPORT_000"
BATTLEGATE_ZERO_LOCATION = {
    "name": "Keyblade Graveyard - Battlegate 0",
    "type": "event",
    "world": "Battlegate Rewards",
    "source_table": "Event",
    "source_id": BATTLEGATE_ZERO_SOURCE_ID,
    "location_description": "Keyblade Graveyard - Battlegate 0",
    "vanilla_item": "ITEM_POTION",
    "vanilla_item_name": "Potion",
}
# BG0 has no native prize row. Its real cleared flag supplies the AP check,
# and the AP receipt supplies its single assigned reward.
BRIDGE_ONLY_BATTLEGATE_EVENT_IDS = frozenset({BATTLEGATE_ZERO_SOURCE_ID})

BATTLEGATE_WORLDS_BY_NUMBER = {
    1: "Olympus",
    2: "Olympus",
    3: "Twilight Town",
    4: "Toy Box",
    5: "Toy Box",
    6: "Kingdom of Corona",
    7: "Kingdom of Corona",
    8: "Monstropolis",
    9: "Arendelle",
    10: "The Caribbean",
    11: "San Fransokyo",
    12: "San Fransokyo",
    13: "Keyblade Graveyard",
    14: "Keyblade Graveyard",
}

# These are gameplay milestones, not possession of the shuffled reward.
# KG finishes at the Tower of Endings, before the separate Scala final boss.
BATTLEGATE_COMPLETION_SOURCES_BY_WORLD = {
    "Olympus": ("Event", "EVENT_KEYBLADE_001"),
    "Twilight Town": ("Event", "EVENT_KEYBLADE_002"),
    "Toy Box": ("Event", "EVENT_KEYBLADE_003"),
    "Kingdom of Corona": ("Event", "EVENT_KEYBLADE_004"),
    "Monstropolis": ("Event", "EVENT_KEYBLADE_005"),
    "Arendelle": ("Event", "EVENT_KEYBLADE_007"),
    "The Caribbean": ("Event", "EVENT_KEYBLADE_008"),
    "San Fransokyo": ("Event", "EVENT_KEYBLADE_009"),
    "Keyblade Graveyard": ("VBonus", "Vbonus_075"),
}

BATTLEGATE_SOURCE_ID_REGION_LOCKS = {
    f"EVENT_REPORT_{number:03}{suffix}": world
    for number, world in BATTLEGATE_WORLDS_BY_NUMBER.items()
    for suffix in (("a", "b") if number < 14 else ("",))
}
BATTLEGATE_SOURCE_ID_REGION_LOCKS[BATTLEGATE_ZERO_SOURCE_ID] = "Keyblade Graveyard"
