# Includes code ported and adapted from WaterKH/KH3RandomizerClient (Apache-2.0).
# Modified for the KH3 Archipelago Python seed generator.
# See ../LICENSE-KH3RandomizerClient.txt and ../THIRD_PARTY_NOTICES.txt.
"""Shared catalog policy and compact native seed options; no encounter generation."""

from __future__ import annotations

import json
from pathlib import Path

from ..BossCatalog import resolve_boss_blacklist
from .tables import mode

EXCLUDED_ENEMY_FILES = {
    "he_01_enemy_01",
    "he_01_enemy_02",
    "ra_01_enemy",
    "ts_02_enemy_01",
    "ts_02_enemy_04",
    "ts_02_enemy_04_Amber",
    "mi_02_enemy_05",
    "mi_04_enemy_01",
    "fz_01_enemy_15",
    "fz_01_enemy_16",
    "fz_01_enemy_65",
    "ca_02_island01_mission_bigfishbattle",
    "bx_02_enemy_04",
    "kg_01_enemy_01",
}
PINNED_BOSSES = {
    "BOSS_004",
    "BOSS_007",
    "BOSS_018",
    "BOSS_054",
    "BOSS_055",
    "BOSS_056",
    "BOSS_058",
    *(f"BOSS_{n:03}" for n in range(35, 42)),
}


def load(root, name):
    return json.loads((root / "wwwroot" / name).read_text(encoding="utf-8-sig"))


def enemy_rules(root):
    data = load(root, "DefaultEnemyCompatibility.json")
    categories = ["ground", "flying", "aquatic", "stationary", "large"]
    if data["schema_version"] != 1 or data["category_order"] != categories:
        raise ValueError("Unsupported enemy compatibility catalog")
    rules = {}
    for category in categories:
        for path in data["categories"][category]:
            if path in rules or "\0" in path:
                raise ValueError(f"Duplicate or invalid enemy class: {path}")
            rules[path] = (category, path in data["mini_bosses"], path in data["pinned"])
    if len(rules) != data["enemy_count"]:
        raise ValueError("Enemy compatibility catalog count mismatch")
    return rules


def compatible_category(source, candidate):
    return source == candidate or source == "flying" and candidate == "ground"


def endgame(name):
    return any(part in name for part in ("Replica Xehanort", "Armored Xehanort", "Master Xehanort "))


def boss_compatible(boss, pawn):
    # TT Tide / Corona Guardianess: Data Xemnas idles, then moves Sora to an unset
    # destination and leaves the fight without a possessed player pawn.
    if boss in ("BOSS_005", "BOSS_009") and pawn == "e_ex353_Pawn":
        return False
    if boss == "BOSS_0106" and pawn in ("e_ex305_Pawn", "e_ex305_Pawn_s10", "e_ex354_Pawn"):
        return False
    if pawn == "e_ex711_Pawn" and boss in {
        f"BOSS_{n:03}" for n in (21, 22, 23, 24, 26, 27, 28, 63, 64, 65, 66, 68, 69, 70)
    }:
        return False
    if boss in ("BOSS_002", "BOSS_003") and pawn in ("e_ex302_Pawn", "e_ex302_Pawn_s10", "e_ex351_Pawn"):
        return False
    # Marshmallow's arena excludes every Data encounter, including Luxord
    # (currently absent from the shuffle pool).
    if boss == "BOSS_011" and pawn in (
        "e_ex721_Pawn", "e_ex711_Pawn",
        *(f"e_ex{n}_Pawn" for n in (*range(351, 363), 367)),
    ):
        return False
    if boss == "BOSS_010" and pawn in ("e_ex721_Pawn", "e_ex352_Pawn", "e_ex357_Pawn"):
        return False
    if boss in ("BOSS_006", "BOSS_007") and pawn in ("e_he902_Pawn", "e_he904_Pawn"):
        return False
    return boss != "BOSS_007" or pawn != "e_ex731_Pawn"


def boss_class(pawn):
    pawn = pawn.rstrip("\0")
    if "_Pawn" not in pawn:
        raise ValueError(f"Invalid boss pawn: {pawn}")
    folder = pawn.replace("_Pawn", "", 1)
    return f"/Game/Blueprints/Enemy/{folder}/{pawn}.{pawn}_C"



def add_runtime_settings(root: Path, seed, config, metadata):
    """Export seed/options only. The DLL owns all encounter mapping generation."""
    pools, exceptions = config.get("pools", {}), config.get("exceptions", {})
    blocked = resolve_boss_blacklist(config.get("boss_blacklist", ()))
    originals = {key: value.rstrip("\0") for key, value in load(root, "DefaultBosses.json").items()}
    pinned = set(PINNED_BOSSES)
    for boss in originals:
        numeric = int(boss[5:])
        if (exceptions.get("Default Data Battles", False) and 93 <= numeric <= 105
                or exceptions.get("Default End Game Battles", False) and numeric in (54, 55, 56, 57, 58, 77)
                or exceptions.get("Default Yozora Battle", False) and numeric == 106):
            pinned.add(boss)
    blocked_pawns = {originals[boss] for boss in blocked}
    allowed = {pawn for boss, pawn in originals.items() if boss not in pinned} - blocked_pawns
    for boss in originals.keys() - pinned:
        if not any(boss_compatible(boss, pawn) for pawn in allowed):
            raise ValueError(f"boss_blacklist leaves no compatible replacement for {boss}; allow more bosses or relax Keep Vanilla settings.")
    metadata["native_randomization"] = dict(version=1,
        enemy_chaos=bool(exceptions.get("Enemy Chaos", False)),
        keep_data=bool(exceptions.get("Default Data Battles", False)),
        keep_endgame=bool(exceptions.get("Default End Game Battles", False)),
        keep_yozora=bool(exceptions.get("Default Yozora Battle", False)),
        boss_blacklist=sorted(blocked))
    for key, pool in (("enemy", "Enemies"), ("boss", "Bosses"), ("party", "Party Members")):
        metadata[key + "_randomization_initially_enabled"] = mode(pools.get(pool, "Vanilla")) == 2
    metadata["party_randomize_on_room_change"] = bool(exceptions.get("Party Chaos", False))
