"""Story gates read from the supported Olympus Coliseum 1.4.2 pak.

AP requires the native Unlock Features setting to be Series (value 1), which
opens extra series while preserving individual UnlockGameFlags. See
docs/olympus_coliseum_mod.md for asset provenance.
"""

# World completion is a gameplay milestone. Reuse the final encounter/event
# rules regardless of whether its reward pool is Randomize or Junk.
COLISEUM_WORLD_COMPLETION_SOURCES = {
    "Olympus": (("VBonus", "Vbonus_013"),),
    "Twilight Town": (("Event", "EVENT_KEYBLADE_002"),),
    "Toy Box": (("VBonus", "Vbonus_023"),),
    "Kingdom of Corona": (("VBonus", "Vbonus_030"),),
    "Monstropolis": (("VBonus", "Vbonus_040"),),
    "Arendelle": (("VBonus", "Vbonus_050"),),
    "The Caribbean": (("VBonus", "Vbonus_066"),),
    "San Fransokyo": (("VBonus", "Vbonus_057"),),
}

# Each tuple is an AND, not an alternative route. gameflow_FZ >= 8820 is
# Arendelle's ending; the other world flags use 9999. gameflow_BT >= 100
# is the base-game clear, represented by Defeat Master Xehanort in AP.
COLISEUM_CUP_REQUIRED_WORLDS = {
    **dict.fromkeys((
        "series_kh1/cup_phil", "series_kh2/cup_painandpanic",
        "series_kh3/cup_hermes", "series_apro/cup_chef",
        "series_apro/cup_flan", "series_bbs/cup_terra",
    ), ("Olympus", "Twilight Town")),
    **dict.fromkeys((
        "series_kh1/cup_pegasus", "series_kh2/cup_cerberus",
        "series_kh3/cup_apollo", "series_bbs/cup_ventus",
    ), ("Toy Box", "Kingdom of Corona")),
    **dict.fromkeys((
        "series_kh1/cup_hercules", "series_kh2/cup_titan",
        "series_kh3/cup_athena", "series_bbs/cup_aqua",
    ), ("Monstropolis", "Arendelle")),
    "series_kh2/cup_goddessoffate": ("The Caribbean", "San Fransokyo"),
    **dict.fromkeys((
        "series_kh1/cup_hades", "series_kh1/cup_gold",
        "series_kh1/cup_platinum", "series_kh2/cup_paradox",
        "series_kh3/cup_zeus", "series_apro/cup_story",
        "series_apro/cup_infernal", "series_apro/cup_limitcut",
        "series_apro/cup_vessels",
    ), ()),
}

COLISEUM_POSTGAME_CUP_IDS = frozenset({
    "series_kh1/cup_hades", "series_kh1/cup_gold",
    "series_kh1/cup_platinum", "series_kh2/cup_paradox",
    "series_kh3/cup_zeus", "series_apro/cup_story",
    "series_apro/cup_infernal", "series_apro/cup_limitcut",
    "series_apro/cup_vessels",
})
