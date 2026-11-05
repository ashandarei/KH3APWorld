# Includes code ported and adapted from WaterKH/KH3RandomizerClient (Apache-2.0).
# Modified for the KH3 Archipelago Python seed generator.
# See ../LICENSE-KH3RandomizerClient.txt and ../THIRD_PARTY_NOTICES.txt.
"""Frozen pre-port generator oracle for native parity tests. Never used in production."""

from __future__ import annotations

import json
from pathlib import Path

from ..BossCatalog import resolve_boss_blacklist
from ..seedgen.rng import SeedRandom

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


def enemy_remap(root, seed, exceptions):
    groups = {name: group for world in load(root, "DefaultEnemies.json").values() for name, group in world.items()}
    rules = enemy_rules(root)
    rng = SeedRandom(seed)
    lookup = {}

    def shuffle_pool(paths, mini):
        paths = list(dict.fromkeys(p.rstrip("\0") for p in paths))
        for path in paths:
            if path not in rules or rules[path][1] != mini:
                raise ValueError(f"Enemy class lacks a matching compatibility rule: {path}")
        for category in ("ground", "flying", "aquatic", "stationary", "large"):
            sources = [p for p in paths if rules[p][0] == category]
            for path in sources:
                if rules[path][2]:
                    lookup[path] = path
            sources = [p for p in sources if not rules[p][2]]
            candidates = [p for p in paths if not rules[p][2] and compatible_category(category, rules[p][0])]
            rng.shuffle(candidates)
            if len(candidates) < len(sources):
                raise ValueError(f"No complete compatible enemy shuffle for {category}")
            lookup.update(zip(sources, candidates))

    shuffle_pool([g["EnemyPath"] for n, g in groups.items() if "(Mini-Boss)" in n], True)
    bosses = [
        n
        for n in groups
        if "(Boss)" in n
        and not (
            exceptions.get("Default Data Battles", False)
            and "Data " in n
            or exceptions.get("Default End Game Battles", False)
            and endgame(n)
            or exceptions.get("Default Yozora Battle", False)
            and "Yozora" in n
        )
    ]
    shuffled_bosses = bosses[:]
    rng.shuffle(shuffled_bosses)
    boss_lookup = dict(zip(bosses, shuffled_bosses))
    shuffle_pool([g["EnemyPath"] for n, g in groups.items() if "(Mini-Boss)" not in n and "(Boss)" not in n], False)
    replacements = {}
    for name, group in groups.items():
        if "(Boss)" in name:
            if name not in boss_lookup:
                continue
            replacement = groups[boss_lookup[name]]["EnemyPath"].rstrip("\0")
        else:
            replacement = lookup[group["EnemyPath"].rstrip("\0")]
        if Path(group["FilePath"].replace("\\", "/")).stem in EXCLUDED_ENEMY_FILES:
            continue
        replacements[name] = replacement
    targets = load(root, "DefaultEnemyRuntimeTargets.json")
    if targets["schema_version"] != 1:
        raise ValueError("Unsupported runtime enemy target catalog")
    packages, classes, entries = [], [], []
    package_indices, class_indices = {}, {}
    chaos_rng = SeedRandom(seed)
    chaos_pools = {}
    for name, replacement in replacements.items():
        source = groups[name]["EnemyPath"].rstrip("\0")
        if source not in rules or rules[source][2]:
            continue
        category, mini, _ = rules[source]
        chaos_pools.setdefault(
            (category, mini),
            [
                p
                # The catalog has one entry per class. Encounter/shuffle
                # results both weight common classes and omit unused ones.
                for p in rules
                if not rules[p][2] and rules[p][1] == mini and compatible_category(category, rules[p][0])
            ],
        )
    for name, replacement in replacements.items():
        group = groups[name]
        target = targets["targets"][name]
        if len(target["actors"]) != len(group["Addresses"]):
            raise ValueError(f"Enemy target count mismatch: {name}")
        package = target["package"]
        package_index = package_indices.setdefault(package, len(packages))
        if package_index == len(packages):
            packages.append(package)
        source = group["EnemyPath"].rstrip("\0")
        for actor in target["actors"]:
            if exceptions.get("Enemy Chaos", False) and source in rules and not rules[source][2]:
                replacement = chaos_rng.choice(chaos_pools[rules[source][:2]])
            class_index = class_indices.setdefault(replacement, len(classes))
            if class_index == len(classes):
                classes.append(replacement)
            if "|" in actor or "|" in package:
                raise ValueError("Invalid runtime enemy target")
            entries.append(f"{package_index}|{actor}|{class_index}")
    if not entries:
        raise ValueError("Runtime enemy generation produced no targets")
    return dict(
        enemy_runtime_format_version=2,
        mode="chaos" if exceptions.get("Enemy Chaos") else "shuffle",
        entry_count=len(entries),
        enemy_runtime_packages=packages,
        enemy_runtime_classes=classes,
        enemy_runtime_entries=entries,
    )


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


def boss_remap(root, seed, exceptions, blacklist=()):
    originals = {key: value.rstrip("\0") for key, value in load(root, "DefaultBosses.json").items()}
    pinned = set(PINNED_BOSSES)
    for boss in originals:
        numeric = int(boss[5:])
        if (
            exceptions.get("Default Data Battles", False)
            and 93 <= numeric <= 105
            or exceptions.get("Default End Game Battles", False)
            and numeric in (54, 55, 56, 57, 58, 77)
            or exceptions.get("Default Yozora Battle", False)
            and numeric == 106
        ):
            pinned.add(boss)
    blocked = resolve_boss_blacklist(blacklist)
    blocked_pawns = {originals[boss] for boss in blocked}
    eligible = [boss for boss in originals if boss not in pinned]
    values = [originals[boss] for boss in eligible]
    rng = SeedRandom(seed)
    rng.shuffle(values)
    replacements = dict(zip(eligible, values))
    if blocked_pawns:
        allowed = sorted({originals[boss] for boss in eligible} - blocked_pawns)
        for boss in eligible:
            if replacements[boss] not in blocked_pawns and boss_compatible(boss, replacements[boss]):
                continue
            candidates = [pawn for pawn in allowed if boss_compatible(boss, pawn)]
            if not candidates:
                raise ValueError(f"boss_blacklist leaves no compatible replacement for {boss}; "
                                 "allow more bosses or relax Keep Vanilla settings.")
            replacements[boss] = rng.choice(candidates)
    else:
        for boss in eligible:
            if boss_compatible(boss, replacements[boss]):
                continue
            candidates = [
                other
                for other in eligible
                if other != boss
                and boss_compatible(boss, replacements[other])
                and boss_compatible(other, replacements[boss])
            ]
            if not candidates:
                raise ValueError(f"No compatible boss swap for {boss}")
            other = rng.choice(candidates)
            replacements[boss], replacements[other] = replacements[other], replacements[boss]
    classes, indices, entries = [], {}, []

    def index(pawn):
        path = boss_class(pawn)
        result = indices.setdefault(path, len(classes))
        if result == len(classes):
            classes.append(path)
        return result

    for boss, replacement in replacements.items():
        if replacement != originals[boss]:
            entries.append(f"{boss}|{index(originals[boss])}|{index(replacement)}")
    return dict(
        boss_runtime_format_version=1,
        mode="serialized_class_substitution",
        boss_runtime_entry_count=len(entries),
        boss_runtime_classes=classes,
        boss_runtime_entries=entries,
        pinned_boss_ids=sorted(PINNED_BOSSES - {"BOSS_018", "BOSS_058"}),
    )

