"""Original objective titles; locations.py selects the implemented AP checks."""
KH02_OBJECTIVE_ID_BASE = 0x4B4D3300
OBJECTIVE_TITLES = (
    "Defeat Shadows", "Defeat Flutterings", "Defeat Flame Cores", "Defeat Neoshadows",
    "Defeat Water Cores", "Defeat Earth Cores", "Reach Level 60", "Reach Level 70", "Reach Level 80",
    "Counter Blast Finish", "Firaga Finish", "Blizzaga Finish", "Thundaga Finish", "Ice Rail Slide",
    "Ice Breaker", "Magic Weakness", "Aerial Combat", "Excellent Shotlocks", "Style Change Kills",
    "Spellweaver Finish", "Spellweaver with Full Wardrobe", "Equip a Wardrobe Piece", "Strike Streetlamps",
    "Castle Town Highest Point", "Watch the Shooting Stars", "Clear the Mirror Rooms", "Dark Room Battle",
    "Find the True Treasure", "Collect the White Gems", "Connect the Mirror Pattern", "Phantom Aqua Spin",
    "Defeat the Darksides", "Destroy Thorns", "Find All Flowers", "Fight Alongside Mickey",
    "Demon Tower Spellweaver Finish", "Castle Town All Treasures", "Castle Town Secret",
    "World Within All Treasures", "World Within Secret", "Phantom Aqua No Damage", "Boss Rush",
    "Collect All Zodiac Relics", "Forest of Thorns All Treasures", "Forest of Thorns Secret",
    "Forest Rail No Damage", "Darkside No Damage", "Depths of Darkness All Treasures",
    "Depths of Darkness Secret", "Demon Tower Team Finish", "Demon Tide on Critical",
)


def objective_name(number: int) -> str:
    if not 1 <= number <= 51:
        raise ValueError("Objective number must be 1 through 51")
    return f"0.2 - Objective {number:02}: {OBJECTIVE_TITLES[number - 1]}"


def objective_locations(selected) -> list[dict]:
    numbers = sorted({int(value) for value in selected})
    return [dict(name=objective_name(n), type="kh02_objective", world="Kingdom Hearts 0.2",
                 source_table="KH02", source_id=f"Objective:{n:03}",
                 primary_item_name="", vanilla_item="NONE", vanilla_item_name="")
            for n in numbers]
