"""Public boss blacklist names and exact TresBossData row identifiers.

Named story bosses cover their base and Re Mind variants. Data fights are
separate choices. Row identifiers allow selecting an individual variant.
"""
BOSS_BLACKLIST_NAMES = {
    "Demon Tide": ("BOSS_005", "BOSS_020"),
    "Grim Guardianess": ("BOSS_009",),
    "Skoll": ("BOSS_012",),
    "King of Toys": ("BOSS_008",),
    "Lump of Horror": ("BOSS_010",),
    "Marshmallow": ("BOSS_011",),
    "Dark Riku": ("BOSS_021", "BOSS_063"),
    "Xigbar": ("BOSS_022", "BOSS_064"),
    "Larxene": ("BOSS_023", "BOSS_065"),
    "Marluxia": ("BOSS_024", "BOSS_066"),
    "Vanitas": ("BOSS_026", "BOSS_069"),
    "Terra-Xehanort": ("BOSS_027", "BOSS_068", "BOSS_078"),
    "Xion": ("BOSS_028", "BOSS_070"),
    "Yozora": ("BOSS_0106",),
    "Data Master Xehanort": ("BOSS_093",),
    "Data Ansem": ("BOSS_094",),
    "Data Xemnas": ("BOSS_095",),
    "Data Xigbar": ("BOSS_096",),
    "Data Larxene": ("BOSS_098",),
    "Data Marluxia": ("BOSS_099",),
    "Data Saix": ("BOSS_0100",),
    "Data Terra-Xehanort": ("BOSS_0101",),
    "Data Dark Riku": ("BOSS_0102",),
    "Data Vanitas": ("BOSS_0103",),
    "Data Young Xehanort": ("BOSS_0104",),
    "Data Xion": ("BOSS_0105",),
}
# Individual rows not yet named above remain selectable by their exact ID.
RANDOMIZABLE_BOSS_IDS = frozenset(
    [f"BOSS_{n:03}" for n in (2, 3, 5, 6, 8, 9, 10, 11, 12, 20, 21, 22, 23, 24,
                             26, 27, 28, 57, 59, 60, 61, 62, 63, 64, 65, 66, 68, 69,
                             70, 77, 78, 80, 93, 94, 95, 96, 98, 99)]
    + [f"BOSS_0{n}" for n in range(100, 107)]
)
BOSS_BLACKLIST_KEYS = frozenset(BOSS_BLACKLIST_NAMES) | RANDOMIZABLE_BOSS_IDS

# Player-facing labels for exact rows. Pawn identities are documented at
# https://openkh.dev/kh3/pawns.html; row assignments come from DefaultBosses.json.
BOSS_BLACKLIST_LABELS = {
    row: name if len(rows) == 1 else f"{name} (KG)"
    for name, rows in BOSS_BLACKLIST_NAMES.items()
    for row in rows
}
BOSS_BLACKLIST_LABELS.update({
    "BOSS_002": "Lava Titan",
    "BOSS_003": "Ice Titan",
    "BOSS_005": "Demon Tide (Twilight Town)",
    "BOSS_006": "Angelic Amber",
    "BOSS_020": "Demon Tide (Keyblade Graveyard)",
    "BOSS_057": "Master Xehanort (Scala ad Caelum)",
    "BOSS_059": "Demon Tower (Battlegate)",
    "BOSS_060": "Catastrochorus (Battlegate)",
    "BOSS_061": "Dark Inferno",
    "BOSS_062": "Dark Inferno χ (Re Mind)",
    "BOSS_077": "Armored Xehanort (Re Mind)",
    "BOSS_078": "Terra-Xehanort (Solo)",
    "BOSS_080": "Darkside (Re Mind)",
})
for _name, _rows in BOSS_BLACKLIST_NAMES.items():
    if len(_rows) > 1 and _name != "Demon Tide":
        BOSS_BLACKLIST_LABELS[_rows[1]] = f"{_name} (Re Mind)"
    BOSS_BLACKLIST_LABELS[_name] = (
        f"{_name} (All Variants)" if len(_rows) > 1 else _name
    )
# Offer the named choice once when it already selects this exact fight.
# Exact row IDs remain valid in YAML and keep readable labels in summaries.
BOSS_BLACKLIST_CATALOG_KEYS = BOSS_BLACKLIST_KEYS - {
    rows[0] for rows in BOSS_BLACKLIST_NAMES.values() if len(rows) == 1
}


def resolve_boss_blacklist(values):
    if isinstance(values, str):
        raise ValueError("boss_blacklist must be a YAML list of boss names or IDs")
    result = set()
    for value in values:
        if value in BOSS_BLACKLIST_NAMES:
            result.update(BOSS_BLACKLIST_NAMES[value])
        elif value in RANDOMIZABLE_BOSS_IDS:
            result.add(value)
        else:
            raise ValueError(f"Unknown boss_blacklist entry: {value!r}. Use a documented boss name or boss ID.")
    return result
