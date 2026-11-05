# Includes code ported and adapted from WaterKH/KH3RandomizerClient (Apache-2.0).
# Modified for the KH3 Archipelago Python seed generator.
# See ../LICENSE-KH3RandomizerClient.txt and ../THIRD_PARTY_NOTICES.txt.
"""Apply AP rewards and seed settings to the bundled cooked table templates."""

from __future__ import annotations

import copy
import struct
from pathlib import Path

from .assets import Asset, AssetError, get
from .rng import SeedRandom

PATHS = {
    "ChrInit": "Content/Load/Common/TresChrInitData",
    "EquipItem": "Content/Load/Tres/TresEquipItemData",
    "FullcourseAbility": "Content/Load/Tres/TresFullcourseAbilityList",
    "LevelUp": "Content/DataTable/Player/LevelUp/p_ex001_LevelUpData",
    "LevelUpAlt": "Content/DataTable/Player/LevelUp/p_ex001_LevelUpLookupData",
    "LuckyMark": "Content/Load/Tres/TresLuckyMarkMilestoneRewardData",
    "VBonus": "Content/Load/Tres/TresVBonusData",
    "VBonusAlt": "Content/Load/Tres/TresVBonusTableAlt",
    "WeaponEnhance": "Content/Load/Tres/ItemSynthesis/TresItemWeaponEnhanceData",
    "Event": "Content/Load/Tres/TresEventData",
    "MobilePortal": "Content/Game/UI/Data/MobilePortal/MobilePortalDataAsset",
    "SynthesisItem": "Content/Load/Tres/ItemSynthesis/TresItemSynthesisData",
    "BaseCharStat": "Content/DataTable/Player/Base/p_ex001_BaseParamData",
    "FoodItemEffectStat": "Content/Load/Tres/TresFoodItemEffectData",
    "QoL": "Content/Load/Tres/QualityOfLifeTable",
    "Completion": "Content/Load/Tres/TresCompletionData",
    "Hints": "Content/Load/Tres/SecretReportInfoTable",
    "WorldFlags": "Content/Blueprints/GoA/Data/InitialWorldFlags_DT",
    "Items": "Content/Load/Tres/TresItemData",
}

STAT_POOLS = {
    "Base Sora Stats": ("BaseCharStat", 80, 120, 120, 150),
    "Level Up Stats": ("LevelUpStat", 0, 3, 0, 7),
    "Keyblade Enhance Stats": ("WeaponEnhanceStat", 0, 2, 0, 3),
    "Equipment Stats": ("EquipItemStat", 0, 4, 0, 6),
    "Food Effect Stats": ("FoodItemEffectStat", 0, 4, 0, 6),
}
QOL_IDS = {
    "Easier Mini-UFO": "BOSS_001",
    "Faster Raging Vulture": "BOSS_002",
    "Dark Baymax Phase 1 Skip": "BOSS_003",
    "Faster Lich Sequence": "BOSS_004",
    "Frozen Chase Skip": "EVENT_001",
    "Faster Crab Collection": "EVENT_002",
    "Big Hero 6 Rescue Skip": "EVENT_003",
    "Faster Sora Collection": "EVENT_004",
    "Union χ Skip": "EVENT_005",
    "Union X Skip": "EVENT_005",
    "Guardians of Light Skip": "EVENT_006",
    "Slow Mickey Skip": "EVENT_007",
    "Max Level Ship": "ITEM_002",
    "All Maps Unlocked": "ITEM_003",
    "Easily Upgraded Keyblades": "ITEM_004",
}
QOL_ASSETS = {
    "BOSS_001": "Content/Blueprints/Gimmick/ts/g_ts_UFO/g_ts_UFO.uasset",
    "BOSS_002": "Content/DataTable/Enemy/Base/e_ex021_BaseDataTable.uasset",
    "BOSS_003": "Content/Cutscene/Localization/ja/bx/cutscene_bx02_bx562.umap",
    "BOSS_004": "Content/Maps/ew/umap/ew_28/ew_28_ENV.umap",
    "EVENT_001": "Content/Levels/fz/fz_03/umap/fz_03_gimmick_Avalanche.umap",
    "EVENT_002": "Content/DataTable/Gimmick/Prize/g_ca_KaniDiveGim_PrizeData.uasset",
    "EVENT_003": "Content/Maps/ew/umap/ew_01/QoL_BigSix.umap",
    "EVENT_004": "Content/Maps/ew/umap/ew_02/QoL_SoraCollection.umap",
    "EVENT_005": "Content/Cutscene/Localization/ja/kg/cutscene_kg01_kg852b.umap",
    "EVENT_006": "Content/Cutscene/Localization/ja/kg_DLC/cutscene_kg_06_kg974.umap",
    "ITEM_003": "Content/Maps/ew/umap/ew_01/QoL_Maps.umap",
}


def prefix(properties, name):
    matches = [p for p in properties if p.name == name or p.name.startswith(name + "_")]
    if len(matches) != 1:
        raise AssetError(f"Expected one {name} property; found {len(matches)}")
    return matches[0]


def mode(value):
    if isinstance(value, str) and not value.isdecimal():
        try:
            return {"none": 0, "vanilla": 1, "randomize": 2, "replace": 3, "balanced": 4, "boosted": 5}[
                value.casefold()
            ]
        except KeyError as exc:
            raise ValueError(f"Unknown randomizer mode: {value}") from exc
    return int(value)


def randomize_options(seed, items, config, defaults):
    selections = config.get("pools", {})
    for table, selection, suffix in (
        ("EquipItem", "Equipment Abilities", "equipment-abilities"),
        ("FullcourseAbility", "Fullcourse Abilities", "fullcourse-abilities"),
    ):
        selection_mode = mode(selections.get(selection, "Vanilla"))
        if selection_mode == 1:
            continue
        if selection_mode not in (2, 3):
            raise ValueError(f"Unsupported {selection} mode: {selection_mode}")
        slots = [
            (row, field, token)
            for row, values in sorted(items.get(table, {}).items())
            for field, token in sorted(values.items())
            if field.startswith("Ability")
            and token.startswith("ETresAbilityKind::")
            and token.rstrip("\0") != "ETresAbilityKind::NONE"
        ]
        rng = SeedRandom(seed + ":" + suffix)
        tokens = [slot[2] for slot in slots]
        rng.shuffle(tokens)
        for i, (row, field, _) in enumerate(slots):
            items[table][row][field] = (
                tokens[i]
                if selection_mode == 2
                else (
                    "ETresAbilityKind::"
                    + rng.choice(["PRIZE_DRAW", "MAGIC_DRAW", "LUCK_UP", "HARVEST", "ITEM_UP", "CHARISMA_CHEF"])
                )
            )
    exceptions = config.get("exceptions", {})
    rng = SeedRandom(seed)
    for selected_mode in (4, 5):
        for pool in STAT_POOLS:
            if mode(selections.get(pool, "Vanilla")) != selected_mode:
                continue
            table, balanced_low, balanced_high, boosted_low, boosted_high = STAT_POOLS[pool]
            low, high = (balanced_low, balanced_high) if selected_mode == 4 else (boosted_low, boosted_high)
            previous = dict(AttackPower=11, MagicPower=12, DefensePower=8, AbilityPoint=28)
            for row, fields in defaults[table].items():
                for field, original in fields.items():
                    original = int(original)
                    if not original and not exceptions.get("Allow All Stats", False):
                        continue
                    if table == "BaseCharStat":
                        minimum, maximum = low, high
                    elif table == "LevelUpStat":
                        minimum, maximum = previous[field], previous[field] + high
                    elif table == "EquipItemStat":
                        spread = 25 if selected_mode == 4 else 50
                        minimum, maximum = (
                            (abs(original - spread), original + spread)
                            if "Resist" in field
                            else (abs(original - low), original + high)
                        )
                    else:
                        minimum, maximum = original, original + high
                    if not exceptions.get("Allow Negative Stats", False):
                        minimum = max(0, minimum)
                    rolled = rng.next(minimum, maximum)
                    if table != "BaseCharStat" and exceptions.get("Allow Negative Stats", False):
                        negative = rng.next(0, 100) % 2 == 0
                        if negative and field not in ("AP", "AbilityPoint"):
                            rolled = -rolled
                    if table == "LevelUpStat":
                        previous[field] = rolled
                    items.setdefault(table, {}).setdefault(row, {})[field] = str(rolled)


class Tables:
    def __init__(self, root: Path):
        self.root = root
        self.assets = {}
        self.files = {}

    def load(self, table):
        if table not in self.assets:
            self.assets[table] = Asset.load(self.root / (PATHS[table] + ".uasset"))
        return self.assets[table]

    def copy(self, relative):
        self.files["KINGDOM HEARTS III/" + relative] = (self.root / relative).read_bytes()

    def encode(self):
        for table, asset in self.assets.items():
            primary, sidecar = asset.encode()
            self.files["KINGDOM HEARTS III/" + PATHS[table] + ".uasset"] = primary
            self.files["KINGDOM HEARTS III/" + PATHS[table] + ".uexp"] = sidecar
        return self.files

    def rewards(self, items, slot_data, easy_keyblades=False):
        for table, rows in items.items():
            if table == "EXP" or table.endswith("Stat"):
                continue
            if table.startswith("Treasure"):
                if any(
                    token.rstrip("\0") != "ETresVictoryBonusKind::NONE"
                    for fields in rows.values()
                    for token in fields.values()
                ):
                    raise ValueError("AP chest rewards must be suppressed before pak generation")
                continue
            if table not in PATHS:
                raise ValueError(f"Unsupported reward table: {table}")
            asset = self.load(table)
            if table == "LuckyMark":
                template = copy.deepcopy(next(iter(asset.rows.values())))
                asset.rows.clear()
                milestones = sorted((int(row), values) for row, values in rows.items())
                if any(not 1 <= n <= 90 for n, _ in milestones):
                    raise ValueError("Lucky emblem milestones must be between 1 and 90")
                for index, (n, values) in enumerate(milestones):
                    row = copy.deepcopy(template)
                    for name, value in [
                        ("RewardIndex", index),
                        ("MarkCount", n),
                        ("CompleteRewardFlag", n == 90),
                        ("RewardTreasureName", values["Reward"]),
                    ]:
                        get(row, name).set(value)
                    asset.rows[str(n)] = row
                continue
            for row_name, fields in rows.items():
                if table == "VBonus" and row_name.startswith("GIVESORA"):
                    continue
                if table == "Event" and row_name == "TresUIMobilePortalDataAsset":
                    get(self.load("MobilePortal").exports[-1].properties, "LSIGamePlayRewardItemName").set(
                        fields["Reward"]
                    )
                    continue
                row = get(asset.exports[-1].properties, row_name).value if table == "ChrInit" else asset.rows[row_name]
                for field, token in fields.items():
                    token = token.rstrip("\0")
                    if table == "ChrInit":
                        if field.startswith("CritEquipAbility"):
                            name, index = "m_CriticalEquipAbility", int(field[16:])
                        elif field.startswith("EquipAbility"):
                            name, index = "m_EquipAbility", int(field[12:])
                        else:
                            name, index = {
                                "Weapon": ("m_Weapons", 0),
                                "HaveAbility": ("m_HaveAbility", 0),
                                "CritHaveAbility": ("m_CriticalHaveAbility", 0),
                            }[field]
                        prop = get(row, name)
                        values = list(prop.value)
                        values[index] = token
                        prop.set(values)
                    elif table == "EquipItem":
                        prop = get(row, "m_AppendAbility")
                        values = list(prop.value)
                        values[int(field[7:])] = token
                        prop.set(values)
                    elif table == "FullcourseAbility":
                        get(row, "m_AbilityPlus").set(token)
                    elif table == "WeaponEnhance":
                        get(row, "m_AppendAbility").set(token)
                    elif table == "LevelUp":
                        index = "ABC".index(field[-1]) + 1
                        ability = token.startswith("ETresAbilityKind::")
                        get(row, f"m_AbilityKind{index}").set(token if ability else "ETresAbilityKind::NONE")
                        prefix(self.load("LevelUpAlt").rows[row_name], f"abilitySlot{index}").set(
                            "None" if ability else token
                        )
                    elif table == "VBonus":
                        which, index = field[5:-1], field[-1]
                        name = f"m_{which}Sora{index}"
                        namespace = "ETresAbilityKind" if which == "Ability" else "ETresVictoryBonusKind"
                        direct = token.startswith(namespace + "::")
                        get(row, name).set(token if direct else namespace + "::NONE")
                        prefix(self.load("VBonusAlt").rows[row_name], name).set("None" if direct else token)
                    elif table == "Event":
                        prefix(row, "randomizedItem").set(token)
                    elif table == "SynthesisItem":
                        get(row, "m_RewardItem").set(token)
        stat_targets = {
            "BaseCharStat": "BaseCharStat",
            "LevelUpStat": "LevelUp",
            "WeaponEnhanceStat": "WeaponEnhance",
            "EquipItemStat": "EquipItem",
            "FoodItemEffectStat": "FoodItemEffectStat",
        }
        for logical, physical in stat_targets.items():
            asset = self.load(physical)
            for name, fields in items.get(logical, {}).items():
                row = asset.rows["Default" if logical == "BaseCharStat" else name]
                for field, value in fields.items():
                    get(row, "m_" + field).set(int(value))
        # Keep the original sixteen movement/controller slots, appending the AP form unlock.
        sora = get(self.load("ChrInit").exports[-1].properties, "m_PlayerSora").value
        equip = get(sora, "m_EquipAbility")
        values = [v for v in equip.value if v != "ETresAbilityKind::AP_STYLE_CHANGE"]
        stylesanity = int(slot_data["stylesanity"])
        if stylesanity not in (0, 1):
            raise ValueError("Stylesanity must be on (1) or off (0)")
        if stylesanity == 0:
            values.append("ETresAbilityKind::AP_STYLE_CHANGE")
        equip.set(values)
        self.load("SynthesisItem").rows.pop("IS_58", None)
        enhancements = self.load("WeaponEnhance").rows
        if len(enhancements) != 170:
            raise AssetError("Expected all 170 keyblade enhancement rows")
        for row in enhancements.values():
            get(row, "m_bInitAchieved").set(int(slot_data.get("keyblade_levels", 0)) == 0)
            if easy_keyblades:
                for index in range(6):
                    get(row, f"m_MatNum{index}").set(0)

    def settings(self, config, seed, hint_values):
        qol = {name: bool(prefix(row, "Active").value) for name, row in self.load("QoL").rows.items()}
        qol.update(EVENT_005=False, EVENT_006=False, ITEM_004=False)
        for values in config.get("qol", {}).values():
            for name, active in values.items():
                if name not in QOL_IDS:
                    raise ValueError(f"Unknown quality-of-life setting: {name}")
                qol[QOL_IDS[name]] = bool(active)
        for row_name, row in self.load("QoL").rows.items():
            prefix(row, "Active").set(qol[row_name])
        for row_name, relative in QOL_ASSETS.items():
            if qol.get(row_name):
                self.copy(relative)
                self.copy(str(Path(relative).with_suffix(".uexp")).replace("\\", "/"))
        for row in self.load("WorldFlags").rows.values():
            name = prefix(row, "FlagName").value
            value = prefix(row, "FlagValue")
            if name.casefold() == "gameflow_he" or not config.get("skip_world_intro_cutscenes", False):
                value.set(2000 if name.casefold() == "gameflow_ew" else 0)
        conditions = config.get("clear_condition", {}).get(
            "conditions", {"KEY_ITEM17": 7}
        )
        prop = prefix(self.load("Completion").rows["CompletionItems_001"], "CompletionItems")
        template = prop.value[0]
        values = []
        for name, amount in conditions.items():
            row = copy.deepcopy(template)
            prefix(row, "Name").set(name.rstrip("\0"))
            prefix(row, "Amount").set(int(amount))
            values.append(row)
        prop.set(values)
        for row_name, row in self.load("Hints").rows.items():
            prefix(row, "ReportText").set("  -  ".join(hint_values.get(row_name, [])))
        path = "Content/Character/wep/w_so120/mdl/mat0/w_so120_mat2_Inst"
        self.copy(path + ".uasset")
        data = bytearray((self.root / (path + ".uexp")).read_bytes())
        rng = SeedRandom(seed)
        for offset in (0xCCC, 0xCD0, 0xCD4):
            struct.pack_into("<f", data, offset, (rng.next() % 21) * 0.05)
        self.files["KINGDOM HEARTS III/" + path + ".uexp"] = bytes(data)
        return qol
