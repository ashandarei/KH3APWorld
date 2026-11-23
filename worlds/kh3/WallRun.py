"""Wall Run unlocks for new seeds. Ability IDs are shared with the game."""

WALL_RUN = "Unlock: Wall Run"
WORLD_WALL_RUN_ABILITIES = {
    "Olympus": ("wall_run_he", 135),
    "Twilight Town": ("wall_run_tt", 136),
    "Toy Box": ("wall_run_ts", 137),
    "Kingdom of Corona": ("wall_run_ra", 138),
    "Monstropolis": ("wall_run_mi", 139),
    "Arendelle": ("wall_run_fz", 140),
    "The Caribbean": ("wall_run_ca", 141),
    "San Fransokyo": ("wall_run_bx", 142),
    "Keyblade Graveyard": ("wall_run_kg", 143),
    "Re Mind": ("wall_run_remind", 144),
}
WORLD_WALL_RUN_ITEMS = {world: f"Wall Run: {world}" for world in WORLD_WALL_RUN_ABILITIES}

WALL_RUN_REGION_GROUPS = {
    "Olympus Coliseum": "Olympus",
    "The Final World": "Keyblade Graveyard",
}
