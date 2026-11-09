"""Checks supported by the local, in-KH3 0.2 integration.

All checks use the Dark World portal unlock; magic-dependent checks also require Unlock: Magic.
Aqua supplies the chapter's combat and movement.
Native progress supplies their completion; the standalone 0.2 bridge is unused.
"""
from .objectives import KH02_OBJECTIVE_ID_BASE, objective_name

KH02_WORLD = "Kingdom Hearts 0.2"
KH02_LOCATION_TYPES = frozenset({"kh02_chest", "kh02_boss", "kh02_objective"})
KH02_CHEST_ID_BASE = 0x4B4D3400
KH02_BOSS_ID_BASE = 0x4B4D3500

# dw_08_gimmick_thorn_F's fire-only B3 barriers enclose these chests:
# g_dw_ThornB3_C_2235 at (-2715, -58023, 3596): DW_BOX018;
# g_dw_ThornB3_C_529 at (-2040, -66429, 7289): DW_BOX019.
# Aqua supplies Fire, but the shared Magic command must be unlocked.
KH02_THORN_CHEST_IDS = frozenset({"DW_BOX018", "DW_BOX019"})
KH02_MAGIC_REQUIRED_SOURCE_IDS = frozenset({
    *(f"Treasure:{chest}" for chest in KH02_THORN_CHEST_IDS),
    "Objective:016",  # Magic Weakness
    "Objective:044",  # Forest of Thorns All Treasures includes both chests.
})

# Authored chest identities and stable addresses. A native row/journal binding
# alone does not establish that the imported map can spawn the chest.
SOURCE_CHESTS = (
    (1, "Castle Town", "DW_LBOX01"),
    (2, "Castle Town", "DW_BOX003"),
    (3, "Castle Town", "DW_BOX004"),
    (4, "Castle Town", "DW_BOX006"),
    (5, "Castle Town", "DW_BOX007"),
    (6, "The World Within", "DW_BOX008"),
    (7, "The World Within", "DW_LBOX02"),
    (8, "The World Within", "DW_BOX011"),
    (9, "The World Within", "DW_BOX013"),
    (10, "The World Within", "DW_BOX033"),
    (11, "The World Within", "DW_BOX015"),
    (12, "The World Within", "DW_BOX016"),
    (13, "Forest of Thorns", "DW_BOX017"),
    (14, "Forest of Thorns", "DW_BOX018"),
    (15, "Forest of Thorns", "DW_BOX019"),
    (16, "Forest of Thorns", "DW_BOX020"),
    (17, "Forest of Thorns", "DW_BOX021"),
    (18, "Forest of Thorns", "DW_BOX022"),
    (19, "Forest of Thorns", "DW_BOX023"),
    (20, "Forest of Thorns", "DW_BOX024"),
    (21, "Forest of Thorns", "DW_BOX025"),
    (22, "Forest of Thorns", "DW_LBOX03"),
    (23, "Forest of Thorns", "DW_BOX034"),
    (24, "Forest of Thorns", "DW_BOX035"),
    (25, "The World Within", "DW_BOX009"),
    (26, "Depths of Darkness", "DW_BOX026"),
    (27, "Depths of Darkness", "DW_BOX027"),
    (28, "Depths of Darkness", "DW_BOX028"),
    (29, "Depths of Darkness", "DW_BOX029"),
    (30, "Depths of Darkness", "DW_LBOX04"),
    (31, "Castle Town", "DW_BOX002"),
    (32, "Castle Town", "DW_BOX005"),
    (33, "The World Within", "DW_BOX010"),
    (34, "The World Within", "DW_BOX012"),
    (35, "The World Within", "DW_BOX014"),
    (36, "Depths of Darkness", "DW_BOX030"),
    (37, "Castle Town", "DW_BOX031"),
    (38, "The World Within", "DW_BOX032"),
    (39, "Castle Town", "DW_BOX036"),
    (40, "The World Within", "DW_BOX037"),
)

# The unreachable opening chest belongs to dw_01, before the playable campaign.
# Keep a permanent exclusion independent of the host-layer availability filter.
INTRO_CHEST_ID = "DW_BOX001"
EXCLUDED_CHEST_IDS = frozenset({INTRO_CHEST_ID})
UNAVAILABLE_CHEST_IDS = frozenset()
CHESTS = tuple(row for row in SOURCE_CHESTS
               if row[2] not in EXCLUDED_CHEST_IDS | UNAVAILABLE_CHEST_IDS)

# Permanent victory flags, never a boss actor's disappearance or the story phase.
BOSSES = (
    (1, "CastleDemonTower", "Castle Town - Demon Tower", "BattleBonus_DW_dw_02_DevilTower"),
    (2, "PhantomAqua1", "The World Within - Phantom Aqua I", "gameflow_DW_MIRROR_STAIRS_CLEAR"),
    (3, "PhantomAqua2", "The World Within - Phantom Aqua II", "gameflow_DW_MIRROR_CORRIDOR_CLEAR"),
    (4, "PhantomAqua3", "The World Within - Phantom Aqua III", "gameflow_DW_MIRROR_C_TUNNEL_CLEAR"),
    (5, "ForestDarkside1", "Forest of Thorns - Darkside I", "BattleBonus_DW_dw_08_Darkside"),
    (6, "ForestDarkside2", "Forest of Thorns - Darkside II", "gameflow_DW_AP_DARKSIDE_02_CLEAR"),
    (7, "DepthsDemonTower", "Depths of Darkness - Demon Tower", "BattleBonus_DW_dw_09_DevilTower"),
    (8, "DemonTide", "Destiny Islands - Demon Tide", "BattleBonus_DW_dw_10_DevilWave"),
)

# Original one-based objective numbers. Every objective has a native observer;
# complex combat/movement conditions also need live player validation.
SUPPORTED_CHALLENGES = frozenset(range(1, 52))


def _location(name, kind, source_id, address, **condition):
    return dict(name=name, type=kind, world=KH02_WORLD, source_table="KH02",
                source_id=source_id, ap_location_id=address, primary_item_name="",
                vanilla_item="NONE", vanilla_item_name="", **condition)


def kh02_locations(*, chests=False, bosses=False, challenges=False):
    locations = []
    if chests:
        locations.extend(
            _location(f"0.2 - {area} - Chest {treasure}", "kh02_chest", f"Treasure:{treasure}",
                      KH02_CHEST_ID_BASE + number, kh02_flag=f"gameflow_DW_CHALLENGE_CHEST_{treasure}")
            for number, area, treasure in CHESTS
        )
    if bosses:
        locations.extend(
            _location(f"0.2 - {name}", "kh02_boss", f"Boss:{boss}", KH02_BOSS_ID_BASE + number,
                      kh02_flag=flag)
            for number, boss, name, flag in BOSSES
        )
    if challenges:
        locations.extend(
            _location(objective_name(number), "kh02_objective", f"Objective:{number:03}",
                      KH02_OBJECTIVE_ID_BASE + number, kh02_challenge=number - 1)
            for number in sorted(SUPPORTED_CHALLENGES)
        )
    return locations


ALL_KH02_LOCATIONS = tuple(kh02_locations(chests=True, bosses=True, challenges=True))
