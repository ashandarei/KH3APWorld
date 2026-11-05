from dataclasses import dataclass

from Options import (
    Choice,
    DeathLink,
    DefaultOnToggle,
    NamedRange,
    OptionGroup,
    OptionSet,
    PerGameCommonOptions,
    Range,
    StartInventoryPool,
    Toggle,
)

from .BossCatalog import BOSS_BLACKLIST_CATALOG_KEYS, BOSS_BLACKLIST_KEYS, BOSS_BLACKLIST_LABELS
from .MoMSongCatalog import MOM_SELECTOR_KEYS, MOM_SONG_CATALOG

from .Data import (
    ALL_GOAL_KEY_ITEM_NAMES,
    VANILLA_LOCATION_POOL_VALUE,
    goal_key_item_counts,
)


RANDOMIZER_POOL_OPTION_NAMES = (
    "olympus_pool",
    "twilight_town_pool",
    "toy_box_pool",
    "kingdom_of_corona_pool",
    "monstropolis_pool",
    "hundred_acre_wood_pool",
    "arendelle_pool",
    "san_fransokyo_pool",
    "the_caribbean_pool",
    "keyblade_graveyard_pool",
    "remind_pool",
    "dark_world_pool",
    "unreality_pool",
    "equipment_abilities_pool",
    "data_battle_rewards_pool",
    "synthesis_pool",
    "photo_missions_pool",
    "keyblade_upgrade_rewards_pool",
    "fullcourse_abilities_pool",
    "lucky_emblems_pool",
    "flantastic_flans_pool",
    "verum_rex_pool",
    "festival_dance_pool",
    "frozen_slider_pool",
    "flash_tracer_course_a_pool",
    "flash_tracer_course_b_pool",
    "battlegates_pool",
    "base_sora_stats_pool",
    "level_up_stats_pool",
    "keyblade_enhance_stats_pool",
    "equipment_stats_pool",
    "food_effect_stats_pool",
    "enemies_pool",
    "bosses_pool",
    "party_members_pool",
)

RANDOMIZER_EXCEPTION_OPTION_NAMES = (
    "default_base_abilities",
    "junk_classic_kingdom_reward",
    "junk_golden_herc_rewards",
    "allow_all_stats",
    "allow_negative_stats",
    "default_data_battles",
    "default_end_game_battles",
    "default_yozora_battle",
    "enemy_chaos",
    "party_chaos",
)

RANDOMIZER_LIMITER_OPTION_NAMES = (
    "level_up_limit",
    "lucky_emblem_limit",
    "moogle_synthesis_limit",
    "exp_multiplier",
)

RANDOMIZER_POOL_NAMES = {
    "olympus_pool": "Olympus",
    "twilight_town_pool": "Twilight Town",
    "toy_box_pool": "Toy Box",
    "kingdom_of_corona_pool": "Kingdom of Corona",
    "monstropolis_pool": "Monstropolis",
    "hundred_acre_wood_pool": "100 Acre Wood",
    "arendelle_pool": "Arendelle",
    "san_fransokyo_pool": "San Fransokyo",
    "the_caribbean_pool": "The Caribbean",
    "keyblade_graveyard_pool": "Keyblade Graveyard",
    "remind_pool": "Re+Mind",
    "dark_world_pool": "Dark World",
    "unreality_pool": "Unreality",
    "equipment_abilities_pool": "Equipment Abilities",
    "data_battle_rewards_pool": "Data Battle Rewards",
    "fullcourse_abilities_pool": "Fullcourse Abilities",
    "lucky_emblems_pool": "Lucky Emblems",
    "flantastic_flans_pool": "Flantastic Flans",
    "battlegates_pool": "Battlegates",
    "base_sora_stats_pool": "Base Sora Stats",
    "level_up_stats_pool": "Level Up Stats",
    "keyblade_enhance_stats_pool": "Keyblade Enhance Stats",
    "equipment_stats_pool": "Equipment Stats",
    "food_effect_stats_pool": "Food Effect Stats",
    "enemies_pool": "Enemies",
    "bosses_pool": "Bosses",
    "party_members_pool": "Party Members",
}

RANDOMIZER_EXCEPTION_NAMES = {
    "default_base_abilities": "Default Base Abilities",
    "junk_classic_kingdom_reward": "Replace Classic Kingdom Reward",
    "junk_golden_herc_rewards": "Replace Golden Herc Rewards",
    "allow_all_stats": "Allow All Stats",
    "allow_negative_stats": "Allow Negative Stats",
    "default_data_battles": "Default Data Battles",
    "default_end_game_battles": "Default End Game Battles",
    "default_yozora_battle": "Default Yozora Battle",
    "enemy_chaos": "Enemy Chaos",
    "party_chaos": "Party Chaos",
}

RANDOMIZER_LIMITER_NAMES = {
    "level_up_limit": "Level Up Limit",
    "lucky_emblem_limit": "Lucky Emblem Limit",
    "moogle_synthesis_limit": "Moogle Synthesis Limit",
}

RANDOMIZER_QOL_NAMES = {
    "easier_mini_ufo": ("Bosses", "Easier Mini-UFO"),
    "faster_raging_vulture": ("Bosses", "Faster Raging Vulture"),
    "faster_lich_sequence": ("Bosses", "Faster Lich Sequence"),
    "dark_baymax_phase_1_skip": ("Bosses", "Dark Baymax Phase 1 Skip"),
    "frozen_chase_skip": ("Events", "Frozen Chase Skip"),
    "faster_crab_collection": ("Events", "Faster Crab Collection"),
    "faster_sora_collection": ("Events", "Faster Sora Collection"),
    "guardians_of_light_skip": ("Events", "Guardians of Light Skip"),
    "max_level_ship": ("Items", "Max Level Ship"),
}

DEFAULT_HINT_CHECKS = (
    "Secret Report 1",
    "Secret Report 2",
    "Secret Report 3",
    "Secret Report 4",
    "Secret Report 5",
    "Secret Report 6",
    "Secret Report 7",
    "Secret Report 8",
    "Secret Report 9",
    "Secret Report 10",
    "Secret Report 11",
    "Secret Report 12",
    "Secret Report 13",
    "Magic: Aero",
    "Magic: Blizzard",
    "Magic: Cure",
    "Magic: Fire",
    "Magic: Thunder",
    "Magic: Water",
    "'Ohana Heartbinder",
    "Dream Heartbinder",
    "Ocean Heartbinder",
    "Pixel Heartbinder",
    "Pride Heartbinder",
    "Ability: Air Slide",
    "Ability: Block",
    "Ability: Dodge Roll",
    "Ability: Pole Spin",
    "Ability: Second Chance",
    "Ability: Withstand Combo",
    "Ability: Doubleflight",
)

GOAL_MANAGED_HINT_CHECKS = frozenset(ALL_GOAL_KEY_ITEM_NAMES)

HINT_CHECK_KEYS = tuple(sorted(
    set(DEFAULT_HINT_CHECKS)
    .union({
        "Ability: High Jump",
        "Ability: Superjump",
        "Ability: Water Boost",
        "Ability: Blizzard Boost",
        "Ability: Thunder Boost",
        "Ability: Fire Boost",
        "Ability: Aero Boost",
    })
    .difference(GOAL_MANAGED_HINT_CHECKS)
))


class HeartPieceCount(Range):
    """Number of Heart Pieces in the pool. Collect all of them to open Portal 1.
    Victory requires defeating Master Xehanort.
    """
    display_name = "Heart Pieces Required"
    range_start = 3
    range_end = 30
    default = 7


class WorldsRequired(Range):
    """Completed worlds required alongside all Heart Pieces to open the Goal Portal.
    Counts Olympus, Twilight Town, Toy Box, Kingdom of Corona, Monstropolis,
    Arendelle, The Caribbean, San Fransokyo, and the KH3 Dark Aqua fight.
    Zero disables the world completion requirement.
    """
    display_name = "Worlds Required"
    range_start = 0
    range_end = 7
    default = 0


class LuckyEmblemsPerCheck(NamedRange):
    """
    Sets how many Lucky Emblem photos are required for each check. For example,
    3 creates checks at 3, 6, 9, and so on. Set this to 1 to make every Lucky
    Emblem its own check. Choose `vanilla milestones` to keep KH3's original
    irregular milestones.
    """

    display_name = "Lucky Emblems Per Check"
    range_start = 1
    range_end = 90
    default = 0
    special_range_names = {"vanilla milestones": 0}


class KeybladeLevels(Choice):
    """
    Controls how Keyblade levels are handled. `start at max` begins every
    Keyblade fully upgraded. `shuffled` adds ten shared Progressive Keyblade
    Level items to the randomized item pool. `keyblade upgrade sanity` instead
    adds ten useful progressive level items for each Keyblade
    available in the seed
    """

    display_name = "Keyblade Levels"
    option_start_at_max = 0
    option_shuffled = 1
    option_keyblade_upgrade_sanity = 2
    default = option_shuffled
    display_names = {
        option_start_at_max: "Start at Max",
        option_shuffled: "Shuffled",
        option_keyblade_upgrade_sanity: "Keyblade Upgrade Sanity",
    }

    @classmethod
    def get_option_name(cls, value: int) -> str:
        return cls.display_names.get(value, super().get_option_name(value))


class Stylesanity(Choice):
    """
    On: each Keyblade requires its own style unlock item.
    Off: Sora starts with Style Change owned and equipped, enabling styles for
    every Keyblade.
    """

    display_name = "Stylesanity"
    option_off = 0
    option_on = 1
    default = option_on


class UltimaWeaponMode(Choice):
    """
    Controls how Ultima Weapon is obtained.

    `shards` adds the selected number of Ultima Weapon Shards to the randomized
    item pool and grants Ultima Weapon after every shard is received. `shop`
    adds Ultima Weapon to the Moogle Shop after every other Keyblade is fully
    upgraded. `yozora` awards it for defeating Yozora. `randomized pool` adds
    Ultima Weapon itself to the randomized item pool. `banished` adds neither
    Ultima Weapon nor its shards to the randomized pool or Moogle Shop.
    """

    display_name = "Ultima Weapon"
    option_shards = 0
    option_shop = 1
    option_yozora = 2
    option_randomized_pool = 3
    option_banished = 4
    default = option_shards


class UltimaWeaponShardCount(Range):
    """Number of shards required when Ultima Weapon is set to `shards`."""

    display_name = "Ultima Weapon Shard Count"
    range_start = 2
    range_end = 13
    default = 7


class Chestsanity(Toggle):
    """Assign a random Keyblade requirement to every chest in randomized worlds.
    Melody chests take priority and never require a Keyblade. Junk world pools
    are excluded. Chests use the required blade's colors.
    With Keyblade portal unlocks, chest keys are Classic Tone, Grand Chef, and
    Hunny Spout. With character portal unlocks, chest keys are the Keyblades
    normally used for world doors. Pandora's Power and Ultima Weapon are excluded.
    Ownership is sufficient; map icons appear only for owned chest keys.
    """

    display_name = "Chestsanity"


class JunkSanFransokyoChests(Toggle):
    """Allow only filler or traps in San Fransokyo (bx) chests, independently
    of its other rewards.
    """

    display_name = "Junk San Fransokyo Chests"


class JunkTheCaribbeanChests(Toggle):
    """Allow only filler or traps in Caribbean (ca) chests, independently
    of its other rewards.
    """

    display_name = "Junk The Caribbean Chests"


class TrapPercentage(Range):
    """
    Percentage of remaining filler items replaced by KH3 traps. Trap types are
    selected with equal probability between Sneeze Trap, Burn Trap, and King Mickey Trap.
    """

    display_name = "Trap Percentage"
    range_start = 0
    range_end = 100
    default = 0


class KH3LocationPoolMode(Choice):
    """
    Controls how a KH3 randomizer item pool participates in item placement.

    `randomize` uses these checks normally. `junk` allows only filler or traps.
    Excess KH3 useful and progression items are granted in starting inventory
    when the normal checks run out.
    """

    option_randomize = 2
    option_junk = 3
    alias_replace = option_junk
    default = option_randomize
    choice_descriptions = {
        "randomize": "Shuffle this pool's rewards into the randomized KH3/AP item placement.",
        "junk": (
            "Allow only filler or traps; grant excess KH3 items in starting inventory."
        ),
    }


class KH3StatPoolMode(Choice):
    """
    Controls stat table randomization using the standalone KH3 randomizer modes.
    """

    option_vanilla = 1
    option_balanced = 4
    option_boosted = 5
    default = option_vanilla


class KH3EnemyPoolMode(Choice):
    """
    Controls enemy, boss, or party randomization.
    """

    option_vanilla = 1
    option_randomize = 2
    default = option_vanilla
    choice_descriptions = {
        "vanilla": "Keep this pool on its original KH3 enemy, boss, or party behavior.",
        "randomize": "Shuffle this enemy, boss, or party pool.",
    }


class OlympusPool(KH3LocationPoolMode):
    """Choose how Olympus rewards are randomized."""

    display_name = "Olympus Pool"


class TwilightTownPool(KH3LocationPoolMode):
    """Choose how Twilight Town rewards are randomized."""

    display_name = "Twilight Town Pool"


class ToyBoxPool(KH3LocationPoolMode):
    """Choose how Toy Box rewards are randomized."""

    display_name = "Toy Box Pool"


class KingdomOfCoronaPool(KH3LocationPoolMode):
    """Choose how Kingdom of Corona rewards are randomized."""

    display_name = "Kingdom of Corona Pool"


class MonstropolisPool(KH3LocationPoolMode):
    """Choose how Monstropolis rewards are randomized."""

    display_name = "Monstropolis Pool"


class HundredAcreWoodPool(KH3LocationPoolMode):
    """Choose how 100 Acre Wood rewards are randomized."""

    display_name = "100 Acre Wood Pool"
    default = KH3LocationPoolMode.option_junk


class ArendellePool(KH3LocationPoolMode):
    """Choose how Arendelle rewards are randomized."""

    display_name = "Arendelle Pool"


class SanFransokyoPool(KH3LocationPoolMode):
    """Choose how San Fransokyo rewards are randomized."""

    display_name = "San Fransokyo Pool"


class TheCaribbeanPool(KH3LocationPoolMode):
    """Choose how Caribbean rewards are randomized."""

    display_name = "The Caribbean Pool"


class KeybladeGraveyardPool(KH3LocationPoolMode):
    """Controls Keyblade Graveyard and The Final World progression checks."""

    display_name = "Keyblade Graveyard Pool"


class ReMindPool(KH3LocationPoolMode):
    """Choose how Re Mind rewards are randomized."""

    display_name = "Re+Mind Pool"


class DarkWorldPool(KH3LocationPoolMode):
    """Choose how Dark World rewards are randomized."""

    display_name = "Dark World Pool"


class UnrealityPool(KH3LocationPoolMode):
    """
    Choose how Unreality rewards are randomized.
    """

    display_name = "Unreality Pool"
    default = KH3LocationPoolMode.option_junk


class EquipmentAbilitiesPool(KH3LocationPoolMode):
    """
    Vanilla keeps the original abilities attached to each piece of equipment.
    Randomize shuffles existing abilities between equipment ability slots.
    Junk replaces them with utility abilities. Abilities remain attached to
    equipment; this does not add AP checks or change stats or upgrade rewards.
    """

    display_name = "Equipment Abilities Pool"
    option_vanilla = VANILLA_LOCATION_POOL_VALUE
    choice_descriptions = {
        "vanilla": "Keep each piece of equipment's original abilities.",
        "randomize": "Shuffle equipment abilities between existing occupied slots.",
        "junk": "Replace equipment abilities with utility abilities.",
    }


class DataBattleRewardsPool(KH3LocationPoolMode):
    """Choose how Data Battle rewards are randomized."""

    display_name = "Data Battle Rewards Pool"


class SynthesisPool(KH3LocationPoolMode):
    """Vanilla keeps normal synthesis. Randomize adds a one-time AP reward for
    the first successful craft of each ordinary recipe. Crafted items remain
    available normally. Junk allows only filler or traps, never progression or
    useful items, even when other pools need extra placement slots.
    """

    display_name = "Synthesis Pool"
    option_vanilla = VANILLA_LOCATION_POOL_VALUE
    default = option_vanilla


class PhotoMissionsPool(SynthesisPool):
    """Vanilla keeps normal photo missions. Randomize adds one AP reward when
    each included photo mission is completed. Photos requiring a world set to
    Junk are omitted, including the teammate photo if any of its required worlds
    is Junk. Junk allows only filler or traps,
    never progression or useful items, even
    when other pools need extra placement slots.
    """

    display_name = "Photo Missions Pool"


class KeybladeUpgradeRewardsPool(SynthesisPool):
    """Vanilla keeps Keyblade upgrade abilities. Randomize replaces each
    upgrade ability with an AP check requiring that Keyblade and its level.
    Junk allows only filler or traps.
    """

    display_name = "Keyblade Upgrade Rewards Pool"


class FullcourseAbilitiesPool(KH3LocationPoolMode):
    """Vanilla keeps the original full-course meal abilities. Randomize shuffles
    them between full-course entries. Junk replaces them with utility abilities.
    These remain meal bonuses; this does not add AP checks or change food stats.
    """

    display_name = "Fullcourse Abilities Pool"
    option_vanilla = VANILLA_LOCATION_POOL_VALUE
    default = KH3LocationPoolMode.option_junk
    choice_descriptions = {
        "vanilla": "Keep the original full-course meal abilities.",
        "randomize": "Shuffle the existing full-course meal abilities.",
        "junk": "Replace full-course meal abilities with utility abilities.",
    }


class LuckyEmblemsPool(KH3LocationPoolMode):
    """Randomize allows normal Lucky Emblem rewards. Junk allows only filler
    or traps, never progression or useful items, even when other pools need
    extra placement slots.
    """

    display_name = "Lucky Emblems Pool"
    default = KH3LocationPoolMode.option_junk
    choice_descriptions = {
        "randomize": KH3LocationPoolMode.choice_descriptions["randomize"],
        "junk": "Only filler or traps; never progression or useful items.",
    }


class FlantasticFlansPool(KH3LocationPoolMode):
    """Seven AP checks for the Flantastic Seven's one-time high-score rewards.
    Fruit rewards remain native. Junk allows only filler or traps.
    """

    display_name = "Flantastic Flans Pool"
    default = KH3LocationPoolMode.option_junk


class VerumRexPool(KH3LocationPoolMode):
    """Controls the one-time AP ability and item rewards from Verum Rex.
    Randomize allows normal placements; junk allows only filler or traps.
    The maximum AP rank is set separately.
    """

    display_name = "Verum Rex Pool"
    default = KH3LocationPoolMode.option_junk


class FestivalDancePool(KH3LocationPoolMode):
    """Controls the one-time AP ability and item rewards from Festival Dance.
    Randomize allows normal placements; junk allows only filler or traps.
    The maximum AP rank is set separately.
    """

    display_name = "Festival Dance Pool"
    default = KH3LocationPoolMode.option_junk


class FrozenSliderPool(KH3LocationPoolMode):
    """Controls the one-time AP ability and item rewards from Frozen Slider.
    Randomize allows normal placements; junk allows only filler or traps.
    The maximum AP rank is set separately.
    Also controls both all-treasures rewards, which have no rank cap.
    """

    display_name = "Frozen Slider Pool"
    default = KH3LocationPoolMode.option_junk


class FlashTracerCourseAPool(KH3LocationPoolMode):
    """Controls the one-time AP ability and item rewards from Flash Tracer Course A.
    Randomize allows normal placements; junk allows only filler or traps.
    The maximum AP rank is set separately.
    """

    display_name = "Flash Tracer Course A Pool"
    default = KH3LocationPoolMode.option_junk


class FlashTracerCourseBPool(KH3LocationPoolMode):
    """Controls the one-time AP ability and item rewards from Flash Tracer Course B.
    Randomize allows normal placements; junk allows only filler or traps.
    The maximum AP rank is set separately.
    """

    display_name = "Flash Tracer Course B Pool"
    default = KH3LocationPoolMode.option_junk


class FestivalDanceMaxRank(Choice):
    """Highest Festival Dance rank eligible for normal AP rewards.
    Ranks above this are restricted to filler or traps, including the A-rank
    ability and first-A bonus. Junk restricts every dance reward. Ranks at or
    below the limit still follow Festival Dance Pool. Higher scores also clear all
    lower tiers. Festival Dance has no S rank.
    """

    display_name = "Festival Dance Maximum AP Rank"
    option_junk = 0
    option_d = 1
    option_c = 2
    option_b = 3
    option_a = 4
    default = option_junk

    @classmethod
    def get_option_name(cls, value):
        return {0: "Junk", 1: "D", 2: "C", 3: "B", 4: "A"}[value]


class VerumRexMaxRank(FestivalDanceMaxRank):
    """Highest Verum Rex rank eligible for normal AP rewards.
    Ranks above this are restricted to filler or traps, including the A-rank
    ability and first-A bonus. Junk restricts every Verum Rex reward. Ranks at
    or below the limit still follow Verum Rex Pool. Higher scores also clear
    all lower tiers. Verum Rex has no S rank.
    """

    display_name = "Verum Rex Maximum AP Rank"


class FrozenSliderMaxRank(FestivalDanceMaxRank):
    """Highest Frozen Slider rank eligible for normal AP rewards.
    Higher ranks are restricted to filler or traps, including the A-rank
    ability and first-A bonus. Junk restricts all ranked rewards. Ranks at or
    below the limit follow Frozen Slider Pool. Higher scores clear all lower tiers.
    Treasure collection rewards are unaffected. There is no S-rank prize.
    """

    display_name = "Frozen Slider Maximum AP Rank"


class FlashTracerCourseAMaxRank(FestivalDanceMaxRank):
    """Highest Flash Tracer Course A rank eligible for normal AP rewards.
    Higher ranks are restricted to filler or traps, including the A-rank
    ability and first-A bonus. Junk restricts all ranked rewards. Ranks at or
    below the limit follow Flash Tracer Course A Pool. Higher scores clear all lower tiers.
    Course B is configured separately. There is no S-rank prize.
    """

    display_name = "Flash Tracer Course A Maximum AP Rank"


class FlashTracerCourseBMaxRank(FestivalDanceMaxRank):
    """Highest Flash Tracer Course B rank eligible for normal AP rewards.
    Higher ranks are restricted to filler or traps, including the A-rank
    ability and first-A bonus. Junk restricts all ranked rewards. Ranks at or
    below the limit follow Flash Tracer Course B Pool. Higher scores clear all lower tiers.
    Course A is configured separately. There is no S-rank prize.
    """

    display_name = "Flash Tracer Course B Maximum AP Rank"


class AllyCharacters(DefaultOnToggle):
    """Award playable Roxas after Keyblade Graveyard with Oblivion and Oathkeeper
    owned, Kairi after Re Mind,
    Riku after all Data battles, and Aqua after 0.2 (when enabled).
    These allies provide Doubleflight and
    Air Slide for logic. Off removes only their completion rewards; starting
    inventory and granted allies remain usable and count for movement logic.
    """
    display_name = "Ally Characters"


class KH02Enabled(DefaultOnToggle):
    """Enable the 0.2 chapter, including its 40 chest and 8 boss checks.
    Requires 0.2 from Kingdom Hearts 2.8 on Steam or Epic Games Store.
    Off disables all 0.2 content, including challenges, and keeps the normal
    Dark World route.
    """
    display_name = "0.2 Enabled (Experimental)"


class KH02Challenges(DefaultOnToggle):
    """Add 51 0.2 challenges as checks when 0.2 Enabled is on.
    Off keeps the 0.2 chapter's chest and boss checks.
    """
    display_name = "0.2 Challenges (Experimental)"


class AdversaryChecks(Toggle):
    """Add one check for the first defeat of each eligible ordinary enemy type.
    Only filler and useful items can be placed here, including foreign items.
    Boss-only enemies and unverified Sniper are excluded. Journal variants share
    a check. Counts follow the actual defeated enemy under enemy randomization;
    randomized encounters may make some optional checks unavailable.
    """
    display_name = "Adversary Checks"


class Ingredients(Choice):
    """Off adds no ingredient checks. Ingredientsanity adds 51 checks for the
    first field acquisition of each ingredient. Ingredientsanity+ instead adds
    checks for individual pickup spots, excluding one-time tutorial pickups.
    Known chance % spots contain junk only. Defaults to Off.
    """
    display_name = "Ingredients"
    option_off = 0
    option_ingredientsanity = 1
    option_ingredientsanity_plus = 2
    default = option_off

    @classmethod
    def from_text(cls, text):
        return super().from_text("ingredientsanity_plus" if text.lower() == "ingredientsanity+" else text)

    @classmethod
    def get_option_name(cls, value):
        return {0: "Off", 1: "Ingredientsanity", 2: "Ingredientsanity+"}[value]


class ClassicKingdomChecks(Choice):
    """
    High Score adds one check per Classic Kingdom game (23 total). Record a
    score during this seed to earn each check; no minimum score is required.
    Quitting before a result is recorded does not count. The Classic Tone
    reward remains a separate randomized check in every mode.
    """

    display_name = "Classic Kingdom Checks"
    option_off = 0
    option_high_score = 1
    default = option_off


class OlympusColiseumPool(KH3LocationPoolMode):
    """
    Controls Archipelago integration for the third-party Olympus Coliseum mod.
    Off disables this integration: the mod is optional. If installed, it keeps its native cup rewards.
    Randomize and Junk require the supported Coliseum mod. 
    These modes replace each cup's native reward with one AP check; Junk
    allows only filler or traps.
    Set the mod's Settings > Unlock Features to Series.
    """

    display_name = "Olympus Coliseum Mod Pool"
    option_off = VANILLA_LOCATION_POOL_VALUE
    default = option_off
    choice_descriptions = {
        "off": (
            "Disabled: no Archipelago cup checks; the mod does not need to be installed. "
            "If installed, it keeps its native cup rewards."
        ),
        "randomize": (
            "Replace each cup's native reward bundle with one unrestricted AP check. "
            "Enable Unlock Everything in the Coliseum settings if its normal story gates "
            "do not match your AP route."
        ),
        "junk": (
            "Replace each cup's native bundle with one AP check. Allow only filler or traps."
        ),
    }


class BattlegatesPool(KH3LocationPoolMode):
    """Choose how the reports and rewards from Battlegates 1–14 are randomized.
    Each Battlegate requires completing its world's story in logic.
    """

    display_name = "Battlegates Pool"
    default = KH3LocationPoolMode.option_junk


class BaseSoraStatsPool(KH3StatPoolMode):
    """Choose vanilla, balanced, or boosted Sora base stats.

    These are the base Sora stats; level-based Strength, Magic,
    Defense, and AP are controlled separately by Level Up Stats.
    """

    display_name = "Base Sora Stats"


class LevelUpStatsPool(KH3StatPoolMode):
    """Choose vanilla, balanced, or boosted level-based Strength, Magic,
    Defense, and AP.
    """

    display_name = "Level Up Stats"


class KeybladeEnhanceStatsPool(KH3StatPoolMode):
    """Choose vanilla, balanced, or boosted Keyblade upgrade stat bonuses.
    """

    display_name = "Keyblade Enhance Stats"
    default = KH3StatPoolMode.option_vanilla


class EquipmentStatsPool(KH3StatPoolMode):
    """Choose vanilla, balanced, or boosted equipment stats and resistances.
    Equipment ability randomization is controlled separately.
    """

    display_name = "Equipment Stats"


class FoodEffectStatsPool(KH3StatPoolMode):
    """Choose vanilla, balanced, or boosted food stat bonuses.
    This does not change full-course meal abilities.
    """

    display_name = "Food Effect Stats"


class EnemiesPool(KH3EnemyPoolMode):
    """
    Experimental: controls whether the enemy randomization starts
    enabled. Every AP seed includes the remap, which can also be toggled from
    the Garden of Assemblage settings menu.
    This can cause softlocks or unusual fight behavior.
    """

    display_name = "Enemies (Experimental)"
    choice_descriptions = {
        "vanilla": (
            "Start Enemy Randomization disabled. "
            "It can be enabled later from the Garden of Assemblage settings menu."
        ),
        "randomize": (
            "Start Enemy Randomization enabled. "
            "It can be disabled later from the Garden of Assemblage settings menu."
        ),
    }


class BossesPool(KH3EnemyPoolMode):
    """
    Experimental: controls whether the boss randomization starts enabled.
    Every AP seed includes the remap, which can also be toggled from the Garden
    of Assemblage settings menu. Victory bonus checks should remain tied to the
    original fight scripts, but boss swaps can still crash or break scripted
    fight completion.
    """

    display_name = "Bosses (Experimental)"
    choice_descriptions = {
        "vanilla": (
            "Start Boss Randomization disabled. "
            "It can be enabled later from the Garden of Assemblage settings menu."
        ),
        "randomize": (
            "Start Boss Randomization enabled. "
            "It can be disabled later from the Garden of Assemblage settings menu."
        ),
    }


class PartyMembersPool(KH3EnemyPoolMode):
    """
    Sets the initial state of Randomize Party Members in the Garden of
    Assemblage runtime settings menu. Vanilla starts Friend 1 and Friend 2
    on Vanilla, using the game's party choices. These can be changed in-game.
    """

    display_name = "Randomize Party Members"
    choice_descriptions = {
        "vanilla": (
            "Start party randomization disabled with Friend 1 and Friend 2 "
            "set to Vanilla, using the game's party choices. You can select "
            "received Party Member items or None later in GoA Options."
        ),
        "randomize": (
            "Start party randomization enabled. The runtime chooses from "
            "received Party Member items; it can be disabled in GoA Options."
        ),
    }


class AutoEquipMovementAbilities(DefaultOnToggle):
    """Automatically equip Glide, Air Slide, High Jump, Doubleflight,
    Superjump, Superslide, Dodge Roll, Pole Spin, and Pole Swing abilities when
    enough AP is available.
    """

    display_name = "Auto Equip Movement Abilities"


class DefaultBaseAbilities(Toggle):
    """
    Prevents default starting abilities such as Block, Dodge Roll, and Pole Spin
    from being randomized. Zero EXP is always a starting ability and is
    never included in the randomized pool, regardless of this option.
    """

    display_name = "Default Base Abilities"


class StartWithWallRun(Toggle):
    """
    Start with the Wall Run unlock item so Archipelago delivers it at the
    start of the game.
    """

    display_name = "Start With Wall Run"


class StartWithMagic(Toggle):
    """
    Start with the Magic unlock item so Archipelago delivers it at the start
    of the game. This unlocks spellcasting; Links have their own unlock.
    """

    display_name = "Start With Magic"


class StartWithAttractionFlow(Toggle):
    """
    Start with the Attraction Flow unlock item so Archipelago delivers it at
    the start of the game.
    """

    display_name = "Start With Attraction Flow"


class StartWithLinks(Toggle):
    """Start with the Links unlock item. Individual Heartbinders are still required.
    This does not unlock spellcasting.
    """

    display_name = "Start With Links"


class JunkClassicKingdomReward(DefaultOnToggle):
    """Allow only filler or traps in the reward for completing all Classic Kingdom games.
    Game unlocks become useful when high-score checks are off. Enabling high-score
    checks keeps them progression because those checks can contain progression rewards.
    """

    display_name = "Junk Classic Kingdom Reward"


class JunkGoldenHercRewards(DefaultOnToggle):
    """Allow only filler or traps in Golden Herc pickups and the turn-in reward."""

    display_name = "Junk Golden Herc Rewards"


class AllowAllStats(Toggle):
    """Include normally zero stats when a stat pool is balanced or boosted.
    Vanilla stat pools are unaffected.
    """

    display_name = "Allow All Stats"


class AllowNegativeStats(Toggle):
    """Allow balanced or boosted stat rolls to be negative, except Sora base
    stats and AP (including equipment AP). Vanilla stat pools are unaffected.
    """

    display_name = "Allow Negative Stats"


class BossBlacklist(OptionSet):
    """Bosses excluded from randomized replacements. Accepts boss names or exact
    boss IDs. Named story bosses include Re Mind variants;
    Data bosses are separate names such as Data Ansem. Remaining bosses may repeat.
    Does not remove original fights protected by Keep Vanilla settings or safety
    restrictions, and has no effect while boss randomization is disabled.
    """

    display_name = "Boss Blacklist"
    valid_keys = BOSS_BLACKLIST_KEYS
    valid_key_labels = BOSS_BLACKLIST_LABELS
    catalog_keys = BOSS_BLACKLIST_CATALOG_KEYS


class DefaultDataBattles(Toggle):
    """
    Keeps all Data Organization XIII fights out of the boss shuffle, including
    when boss randomization is enabled later in-game.
    """

    display_name = "Keep Data Battles Vanilla (Boss Randomizer)"


class DefaultEndGameBattles(Toggle):
    """
    Keeps Replica and Master Xehanort out of the boss shuffle, including when
    boss randomization is enabled later in-game. Armored Xehanort is always
    excluded for stability.
    """

    display_name = "Keep Endgame Bosses Vanilla (Boss Randomizer)"


class DefaultYozoraBattle(Toggle):
    """
    Keeps Yozora out of the boss shuffle, including when boss randomization
    is enabled later in-game.
    """

    display_name = "Keep Yozora Vanilla (Boss Randomizer)"


class EnemyChaos(Toggle):
    """
    Experimental: randomizes enemies per entity instead of per enemy type.
    """

    display_name = "Enemy Chaos (Experimental)"


class PartyChaos(Toggle):
    """
    Sets the initial state of Reroll Party Every Room in GoA Options. When ON,
    the received random party rerolls on every room change. When OFF, the
    random pair stays stable until the available Party Member items change.
    """

    display_name = "Reroll Party Every Room"


class KH3NumericChoice(Choice):
    @classmethod
    def get_option_name(cls, value: int) -> str:
        return str(value)


class LevelUpLimit(Range):
    """
    Highest Sora level eligible for non-junk AP items. Levels above this limit contain
    only filler or traps, never progression or useful items. Set to 1 for junk
    at every level.
    """

    display_name = "Level Up Limit"
    range_start = 1
    range_end = 99
    default = 35



class LuckyEmblemLimit(KH3NumericChoice):
    """
    Randomizes Lucky Emblem milestone rewards up to and including this count;
    later rewards become junk.
    """

    display_name = "Lucky Emblem Limit"
    option_emblems_10 = 10
    option_emblems_20 = 20
    option_emblems_30 = 30
    option_emblems_40 = 40
    option_emblems_50 = 50
    option_emblems_60 = 60
    option_emblems_70 = 70
    option_emblems_80 = 80
    option_emblems_90 = 90
    default = 50


class MoogleSynthesisLimit(Range):
    """
    Number of ordinary recipes, in native recipe order, eligible for normal
    AP rewards. Later recipes only receive filler or traps.
    """

    display_name = "Moogle Synthesis Limit"
    range_start = 0
    range_end = 67
    default = 15


class MoogleShopChecks(Range):
    """
    Adds this many one-time Archipelago purchases to the Moogle Shop Buy menu.
    Each purchase reports a location check and becomes sold out permanently.
    Set this to 0 to disable the checks.
    """

    display_name = "Moogle Shop Checks"
    range_start = 0
    range_end = 20
    default = 0


class MoogleShopPrice(Range):
    """
    Base Munny price for Archipelago items in the Moogle Shop Buy menu.
    Filler uses this price, useful items cost 5x, progression items cost 10x,
    and traps cost half. The seed may lower the base if its item pool cannot
    provide enough Munny Pouches for the planned shop budget.
    """

    display_name = "Moogle Shop Base Price"
    range_start = 0
    range_end = 10000
    default = 100


class ExpMultiplier(Choice):
    """
    Enemy EXP multiplier.
    """

    display_name = "EXP Multiplier"
    option_0_5x = 1
    option_1_0x = 2
    option_2_0x = 4
    option_3_0x = 6
    option_4_0x = 8
    option_5_0x = 10
    option_10_0x = 20
    option_20_0x = 40
    default = 2

    @classmethod
    def get_option_name(cls, value: int) -> str:
        return f"{value / 2:g}x"


class EasierMiniUfo(DefaultOnToggle):
    """Make the Mini-UFO fight easier."""

    display_name = "Easier Mini-UFO"


class FasterRagingVulture(DefaultOnToggle):
    """Shorten the Raging Vulture fight."""

    display_name = "Faster Raging Vulture"


class FasterLichSequence(DefaultOnToggle):
    """Shorten the Lich chase sequence."""

    display_name = "Faster Lich Sequence"


class DarkBaymaxPhase1Skip(DefaultOnToggle):
    """Skip the first phase of the Dark Baymax fight."""

    display_name = "Dark Baymax Phase 1 Skip"


class FrozenChaseSkip(DefaultOnToggle):
    """Skip the Frozen chase sequence in Arendelle."""

    display_name = "Frozen Chase Skip"


class FasterCrabCollection(DefaultOnToggle):
    """Speed up crab collection in the Caribbean."""

    display_name = "Faster Crab Collection"


class FasterSoraCollection(DefaultOnToggle):
    """Speed up Sora collection in The Final World."""

    display_name = "Faster Sora Collection"


class GuardiansOfLightSkip(Toggle):
    """Skip the Guardians of Light sequence in Re Mind."""

    display_name = "Guardians of Light Skip"


class Fun10kHeartless(Toggle):
    """
    Spawn 200 enemies at once at the start of the 10k Heartless fight,
    with a larger arena and Shadows that respawn as they die.
    A powerful computer is recommended.
    """

    display_name = "Fun 10k Heartless"
    default = 0


class SkipGuardiansAndMickeyWalk(DefaultOnToggle):
    """
    When entering the Re:Mind Scala ad Caelum route, advance the story flag
    past the slow Guardians of Light and Mickey walking sequence.
    """

    display_name = "Skip Guardians and Mickey Walk"


class FastUnionCross(DefaultOnToggle):
    """End the mandatory Union Cross sequence as soon as its ride begins."""

    display_name = "Fast Union Cross"


class FastDanceMinigame(DefaultOnToggle):
    """Shorten the mandatory Kingdom of Corona story dance minigame."""

    display_name = "Fast Dance Minigame"


class FastBeatOfLead(DefaultOnToggle):
    """Finish Toy Box's mandatory Beat of Lead encounter after one mecha kill."""

    display_name = "Fast Beat of Lead"


class MaxLevelShip(DefaultOnToggle):
    """Set the Caribbean ship to its maximum level."""

    display_name = "Max Level Ship"


class SkipWorldIntroCutscenes(Toggle):
    """
    Start supported worlds after their opening cutscenes.
    Disabled starts world intros normally, retaining required Olympus/End of World setup.
    This is separate from the experimental automatic cutscene skipper.
    """

    display_name = "Skip World Intro Cutscenes"


class AutoSkipCutscenes(Toggle):
    """
    Enables the experimental automatic cutscene skipper. Doesn't work on 99% of cutscenes.
    """

    display_name = "Auto Skip Cutscenes (Experimental)"


class ColorRandomization(Choice):
    """
    Recolours Sora, his world forms, Donald, Goofy and every guest party
    member, along with the weapons they carry - keyblades, Donald's staves
    and Goofy's shields. Links are recoloured too (Stitch and Meow Wow only
    in their glow and accents), and so are prefab Gummi Ships; a ship you
    built or edited keeps the colours you gave it.

    Coordinated keeps each character recognisable: hues are spread so
    separate garments differ, the zones within one garment stay related,
    and faces and skin are left alone.

    Wild draws every material zone independently and repaints the surfaces
    Coordinated protects, faces included. Some seeds will produce genuinely
    clashing characters - that is the mode working as intended.

    This sets the starting value only. It can be changed at any time from
    AP Options in the in-game settings menu, and that choice is saved.
    """

    display_name = "Palettesanity"
    option_off = 0
    option_coordinated = 1
    option_wild = 2
    default = option_off

    @classmethod
    def get_option_name(cls, value: int) -> str:
        return {
            cls.option_off: "Off",
            cls.option_coordinated: "Coordinated",
            cls.option_wild: "Wild",
        }[value]


class MusicShuffle(Choice):
    """
    Shuffles the field and battle music of every world, including the Garden
    of Assemblage, along with six groups of music that belong to no world:
    attractions and Link summons; minigames (Flantastic Flans, Flash Tracer,
    curling, the Classic Kingdom menu, cooking, Winnie the Pooh); data
    battles and Battlegates; story scripted fights; the Gummi Ship and the
    world map; boss themes (the final fight's Attraction Flow segment keeps
    its music).

    Shuffle Worlds: each world takes another world's field and battle music,
    keeping them together.

    Shuffle Battle Themes: field music and battle music are shuffled
    separately, so a world can pair one world's field music with another's
    battle music.

    In both modes, each group's music is shuffled within the group.

    Chaos: all of this music is mixed in a single pool. With Include Custom
    Music on, WAV and OGG files in the player's music folder join it. Each
    track is used at most once and a world never gets its own music, so a
    battle theme can play while exploring or during a Link; the groups no
    longer matter. Drawn custom tracks only replace field and battle music.

    Every world always gets music from a different world. Cutscene music is
    unchanged, and so are the festival dance, which is played to its music,
    the Gummi Ship's take-off and result jingles, and the final fight's
    Attraction Flow segment.

    Music the player replaces with a custom file, or keeps with a "vanilla"
    entry in custom_music.json, stays out of the shuffle wherever the game
    normally plays it.

    This sets the starting value only. It can be changed at any time from
    Music Rando in the in-game settings menu.
    """

    display_name = "Music Shuffle"
    option_vanilla = 0
    option_shuffle_worlds = 1
    option_shuffle_battle_themes = 2
    option_chaos = 3
    default = option_vanilla


class IncludeCustomMusic(DefaultOnToggle):
    """
    Include WAV and OGG files from the KH3 client's Music Dir in the Chaos
    music shuffle.
    This affects Chaos only; explicit music replacements still apply in
    every mode, even with this option off.

    This sets the starting value only. It can be changed at any time from
    Music Rando in the in-game settings menu.
    """

    display_name = "Include Custom Music"


class MapOverlayEnabled(DefaultOnToggle):
    """Starting value of Map Overlay Shortcut (L3/F8)."""
    display_name = "Map Overlay Shortcut"


class SongDisplay(DefaultOnToggle):
    """Show the current song at the top center. Can be changed in-game under AP Options > Map options.
    """
    display_name = "Song Display"


class HideLockedChestIcons(Toggle):
    """Initially hide Chestsanity map icons until their required Keyblade is owned.
    Chest models remain visible in the world. Can be changed in-game, including with L2+R2 on the large map.
    """
    display_name = "Hide Locked Chest Icons"


class MapChestIcons(DefaultOnToggle):
    """Initially show chest icons on both maps."""
    display_name = "Chest Icons"


class MapEmblemIcons(DefaultOnToggle):
    """Initially show Lucky Emblem icons on both maps."""
    display_name = "Lucky Emblem Icons"


class MapIngredientIcons(DefaultOnToggle):
    """Initially show ingredient icons on both maps."""
    display_name = "Ingredient Icons"


class MinimapIcons(DefaultOnToggle):
    """Initially show AP collectible icons on the minimap."""
    display_name = "Minimap Icons"


class LargeMapIcons(DefaultOnToggle):
    """Initially show AP collectible icons on the large map."""
    display_name = "Large Map Icons"


class MapOverlayKeybinds(DefaultOnToggle):
    """Show controller keybind hints below the large map overlay's battle level.

    The locked-chest shortcut appears only when the map contains an unopened
    Chestsanity chest whose required Keyblade is missing.
    """
    display_name = "Map Overlay Keybinds"


class FormThemes(Toggle):
    """
    While Sora is in a keyblade's Formchange, that keyblade's theme plays over
    the current music (Hero's Origin: Olympus, Wheel of Fate: The
    Caribbean...), and Anti Form has its own. When the form ends, the music it
    interrupted picks up where it was, boss music included.

    Music the player replaces with a custom file is never covered by a form
    theme; a "vanilla" entry in custom_music.json only keeps music out of the
    shuffle, and form themes still play over it. A custom file named
    Form_UltimaWeapon (and so on) replaces that form's theme, and plays even
    with this option off; a Form_ vanilla entry turns it off.

    Some forms keep their keyblade and show no sign when they run out
    (Kingdom Key, Oathkeeper, Oblivion, Starlight): their theme, like Anti
    Form's, rests after 8 seconds without an action other than moving, and
    comes back at the next one.

    This sets the starting value only. It can be changed at any time from
    Music Rando in the in-game settings menu.
    """

    display_name = "Form Themes"


class HintSelectionType(Choice):
    """
    Controls the hint style written into Secret Reports.
    """

    display_name = "Hint Selection Type"
    option_none = 0
    option_vague_by_check = 1
    option_vague_by_category = 2
    option_verbose = 3
    default = option_verbose

    @classmethod
    def get_option_name(cls, value: int) -> str:
        return {
            cls.option_none: "None",
            cls.option_vague_by_check: "Vague: By Check",
            cls.option_vague_by_category: "Vague: By Category",
            cls.option_verbose: "Verbose",
        }[value]


class ImportantHintChecks(OptionSet):
    """
    Item names that should be considered important when generating KH3 Secret
    Report hints.
    """

    display_name = "Important Hint Checks"
    valid_keys = HINT_CHECK_KEYS
    default = frozenset(DEFAULT_HINT_CHECKS)



class BattleLevelMode(Choice):
    """Off keeps default battle levels. Sora Level scales battle levels with Sora's level."""

    display_name = "Battle Level Mode"
    option_off = 0
    option_sora_level = 1
    default = option_sora_level


class BattleLevelOffset(NamedRange):
    """Starting battle-level offset when Battle Level Sync is enabled.
    Choose -3 through +5, or +10. Can be changed in-game for this seed.
    """
    display_name = "Battle Level Offset"
    range_start = -3
    range_end = 5
    special_range_names = {"plus 10": 10}
    default = 0


class MusicChestCount(Range):
    """Number of additional music chests, each requiring its own MoM song clear.
    Choose 0-8 authored chest locations across the KH3 worlds. Requires Melody
    of Memory and MoMController. Songs repeat only after the eligible pool is used.
    Opening each music chest also awards an additional randomized AP item.
    """

    display_name = "Music Chest Count"
    range_start = 0
    range_end = 8
    default = 0


class MusicChestVanillaConversion(Range):
    """Number of vanilla chests to randomly convert to blue/gold music chests.
    Choose 1–128, or 0 to disable conversion.
    Each requires its own song clear before Open becomes available. Original
    chest locations and rewards remain intact, and opening each music chest
    also awards an additional randomized AP item. The count is capped by available
    chests. Eligible songs are reused in shuffled passes when necessary.
    """

    display_name = "Convert Vanilla Chests to Music Chests"
    range_start = 0
    range_end = 128
    default = 0

    @classmethod
    def from_any(cls, data):
        if isinstance(data, bool):
            raise TypeError("music_chest_vanilla_conversion requires a number from 0 to 128.")
        return super().from_any(data)


class MelodyOfMemorySongWhitelist(OptionSet):
    """Only these songs may be selected when nonempty; empty allows all songs
    except blacklisted songs. Accepts individual songs with their exact source
    in parentheses, or Theme:, World:, and Source: categories. Whitelist always
    overrides blacklist, including overlapping categories. Songs are selected
    without replacement before repeating; each chest still needs its own clear.
    """

    display_name = "Melody of Memory Song Whitelist"
    valid_keys = MOM_SELECTOR_KEYS
    song_catalog = MOM_SONG_CATALOG


class MelodyOfMemorySongBlacklist(OptionSet):
    """Exclude individual songs or Theme:, World:, and Source: categories.
    Whitelisted songs always remain eligible. An empty whitelist uses all
    non-blacklisted songs. Songs repeat when the eligible pool is too small.
    """

    display_name = "Melody of Memory Song Blacklist"
    valid_keys = MOM_SELECTOR_KEYS
    song_catalog = MOM_SONG_CATALOG


class MelodyOfMemoryDifficulty(Choice):
    """Default difficulty for selected Melody of Memory songs. Can be overridden
    during play in Game Settings > Melody of Memory.
    """

    display_name = "Melody of Memory Difficulty"
    option_beginner = 0
    option_standard = 1
    option_proud = 2
    default = option_beginner


class MelodyOfMemoryParty(Choice):
    """Starting party for Melody of Memory song launches. Can be changed in-game for this seed."""
    display_name = "Melody of Memory Party"
    option_team_classic = 0
    option_team_days = 1
    option_team_3d = 2
    option_team_bbs = 3
    default = option_team_classic


class MelodyOfMemoryPlayStyle(Choice):
    """Starting play style for Melody of Memory song launches. Can be changed in-game for this seed."""
    display_name = "Melody of Memory Play Style"
    option_normal = 0
    option_one_button = 1
    option_performer = 2
    default = option_normal


class MelodyOfMemoryStartingItem(Choice):
    """Starting item for Melody of Memory song launches. Can be changed in-game for this seed."""
    display_name = "Melody of Memory Starting Item"
    option_none = 0
    option_potion = 1
    option_mega_potion = 2
    option_summoning_star = 3
    default = option_none


class DeathLinkAmnesty(Range):
    """Local deaths forgiven before sending a DeathLink. With 3, every fourth
    local death is shared, then the allowance resets. 0 shares every death.
    Received DeathLinks still apply and do not use the allowance. Can be changed
    during play in the in-game AP Options menu.
    """

    display_name = "DeathLink Amnesty"
    range_start = 0
    range_end = 20
    default = 0


class IncludeSpace(Toggle):
    """Randomize Gummi missions, battles, and treasure spheres. Boss clears award
    three checks. Adds Gummi parts, blueprints, and special weapons to the pool.
    Grindy missions and space superbosses have separate include options.
    Excludes the native starter Highwind from the pool and zone unlocks.
    Each space zone has one blueprint that unlocks that zone:
    Starlight Way: Bomb and one completed main world.
    Misty Stream: Galleon and two completed main worlds.
    The Eclipse: Submarine and three completed main worlds.
    Any of Olympus, Twilight Town, Toy Box, Kingdom of Corona, Monstropolis,
    Arendelle, The Caribbean, and San Fransokyo counts, including Junk worlds.
    Other ship blueprints remain useful rewards and do not unlock zones.
    Endymion is progression when space superbosses are enabled, for Schwarzgeist.
    Ship blueprints and other Gummi items use normal placement.
    When disabled, space rewards remain vanilla.
    Requires a new seed.
    """
    display_name = "Include Space"


class IncludeGrindySpaceMissions(Toggle):
    """Include the 3,000, 5,000 and 9,999 Heartless milestones, Pro/Elite/Master Builder,
    and Special Weapon Authority (13 special weapons) as randomized checks.
    Requires Include Space. Gummi Ship Completionist is included only when
    both this option and Include Space Superbosses are enabled.
    Requires a new seed.
    """
    display_name = "Include Grindy Space Missions"


class IncludeSpaceSuperbosses(Toggle):
    """Include all three randomized rewards from each of Schwarzgeist and
    Omega Machina. Requires Include Space. Gummi Ship Completionist is included
    only when both this option and Include Grindy Space Missions are enabled.
    Requires a new seed.
    """
    display_name = "Include Space Superbosses"


class GateOpenerCount(Range):
    """Adds Gate Openers to the pool. Spend one from the Gummiphone to permanently
    open one locked Garden of Assemblage world door. Never opens the final portal.
    This deliberately bypasses access logic; generation never relies on these items.
    """
    display_name = "Gate Opener Count"
    range_start = 0
    range_end = 7
    default = 0


class PortalUnlocks(Choice):
    """Open worlds with their Keyblade, or with their designated party-member item.
    Party-member mode shows dimmed character portraits on locked portals.
    Open portals retain their normal world icons. Characters do not need to be equipped.
    """
    display_name = "Portal Unlocks"
    option_keyblades = 0
    option_party_members = 1
    default = 0


@dataclass
class KH3Options(PerGameCommonOptions):
    portal_unlocks: PortalUnlocks
    gate_opener_count: GateOpenerCount
    include_space: IncludeSpace
    include_grindy_space_missions: IncludeGrindySpaceMissions
    include_space_superbosses: IncludeSpaceSuperbosses
    start_inventory_from_pool: StartInventoryPool
    death_link: DeathLink
    death_link_amnesty: DeathLinkAmnesty
    battle_level_mode: BattleLevelMode
    battle_level_offset: BattleLevelOffset
    heart_piece_count: HeartPieceCount
    worlds_required: WorldsRequired
    lucky_emblems_per_check: LuckyEmblemsPerCheck
    keyblade_levels: KeybladeLevels
    stylesanity: Stylesanity
    chestsanity: Chestsanity
    junk_san_fransokyo_chests: JunkSanFransokyoChests
    junk_the_caribbean_chests: JunkTheCaribbeanChests
    ultima_weapon_mode: UltimaWeaponMode
    ultima_weapon_shard_count: UltimaWeaponShardCount
    trap_percentage: TrapPercentage
    olympus_pool: OlympusPool
    twilight_town_pool: TwilightTownPool
    toy_box_pool: ToyBoxPool
    kingdom_of_corona_pool: KingdomOfCoronaPool
    monstropolis_pool: MonstropolisPool
    hundred_acre_wood_pool: HundredAcreWoodPool
    arendelle_pool: ArendellePool
    san_fransokyo_pool: SanFransokyoPool
    the_caribbean_pool: TheCaribbeanPool
    keyblade_graveyard_pool: KeybladeGraveyardPool
    remind_pool: ReMindPool
    dark_world_pool: DarkWorldPool
    unreality_pool: UnrealityPool
    equipment_abilities_pool: EquipmentAbilitiesPool
    data_battle_rewards_pool: DataBattleRewardsPool
    synthesis_pool: SynthesisPool
    photo_missions_pool: PhotoMissionsPool
    keyblade_upgrade_rewards_pool: KeybladeUpgradeRewardsPool
    fullcourse_abilities_pool: FullcourseAbilitiesPool
    lucky_emblems_pool: LuckyEmblemsPool
    flantastic_flans_pool: FlantasticFlansPool
    verum_rex_pool: VerumRexPool
    festival_dance_pool: FestivalDancePool
    frozen_slider_pool: FrozenSliderPool
    flash_tracer_course_a_pool: FlashTracerCourseAPool
    flash_tracer_course_b_pool: FlashTracerCourseBPool
    festival_dance_max_rank: FestivalDanceMaxRank
    verum_rex_max_rank: VerumRexMaxRank
    frozen_slider_max_rank: FrozenSliderMaxRank
    flash_tracer_course_a_max_rank: FlashTracerCourseAMaxRank
    flash_tracer_course_b_max_rank: FlashTracerCourseBMaxRank
    classic_kingdom_checks: ClassicKingdomChecks
    ingredients: Ingredients
    adversary_checks: AdversaryChecks
    ally_characters: AllyCharacters
    kh02_enabled: KH02Enabled
    kh02_challenges: KH02Challenges
    olympus_coliseum_pool: OlympusColiseumPool
    music_chest_count: MusicChestCount
    music_chest_vanilla_conversion: MusicChestVanillaConversion
    melody_of_memory_song_whitelist: MelodyOfMemorySongWhitelist
    melody_of_memory_song_blacklist: MelodyOfMemorySongBlacklist
    melody_of_memory_difficulty: MelodyOfMemoryDifficulty
    melody_of_memory_party: MelodyOfMemoryParty
    melody_of_memory_play_style: MelodyOfMemoryPlayStyle
    melody_of_memory_starting_item: MelodyOfMemoryStartingItem
    battlegates_pool: BattlegatesPool
    base_sora_stats_pool: BaseSoraStatsPool
    level_up_stats_pool: LevelUpStatsPool
    keyblade_enhance_stats_pool: KeybladeEnhanceStatsPool
    equipment_stats_pool: EquipmentStatsPool
    food_effect_stats_pool: FoodEffectStatsPool
    enemies_pool: EnemiesPool
    bosses_pool: BossesPool
    boss_blacklist: BossBlacklist
    party_members_pool: PartyMembersPool
    default_base_abilities: DefaultBaseAbilities
    auto_equip_movement_abilities: AutoEquipMovementAbilities
    start_with_wall_run: StartWithWallRun
    start_with_magic: StartWithMagic
    start_with_links: StartWithLinks
    start_with_attraction_flow: StartWithAttractionFlow
    junk_classic_kingdom_reward: JunkClassicKingdomReward
    junk_golden_herc_rewards: JunkGoldenHercRewards
    allow_all_stats: AllowAllStats
    allow_negative_stats: AllowNegativeStats
    default_data_battles: DefaultDataBattles
    default_end_game_battles: DefaultEndGameBattles
    default_yozora_battle: DefaultYozoraBattle
    enemy_chaos: EnemyChaos
    party_chaos: PartyChaos
    level_up_limit: LevelUpLimit
    lucky_emblem_limit: LuckyEmblemLimit
    moogle_synthesis_limit: MoogleSynthesisLimit
    moogle_shop_checks: MoogleShopChecks
    moogle_shop_price: MoogleShopPrice
    exp_multiplier: ExpMultiplier
    easier_mini_ufo: EasierMiniUfo
    faster_raging_vulture: FasterRagingVulture
    faster_lich_sequence: FasterLichSequence
    dark_baymax_phase_1_skip: DarkBaymaxPhase1Skip
    frozen_chase_skip: FrozenChaseSkip
    faster_crab_collection: FasterCrabCollection
    faster_sora_collection: FasterSoraCollection
    guardians_of_light_skip: GuardiansOfLightSkip
    fun_10k_heartless: Fun10kHeartless
    skip_guardians_and_mickey_walk: SkipGuardiansAndMickeyWalk
    fast_union_cross: FastUnionCross
    fast_dance_minigame: FastDanceMinigame
    fast_beat_of_lead: FastBeatOfLead
    max_level_ship: MaxLevelShip
    auto_skip_cutscenes: AutoSkipCutscenes
    skip_world_intro_cutscenes: SkipWorldIntroCutscenes
    color_randomization: ColorRandomization
    music_shuffle: MusicShuffle
    include_custom_music: IncludeCustomMusic
    form_themes: FormThemes
    map_overlay_keybinds: MapOverlayKeybinds
    map_overlay_enabled: MapOverlayEnabled
    song_display: SongDisplay
    hide_locked_chest_icons: HideLockedChestIcons
    map_chest_icons: MapChestIcons
    map_emblem_icons: MapEmblemIcons
    map_ingredient_icons: MapIngredientIcons
    minimap_icons: MinimapIcons
    large_map_icons: LargeMapIcons
    hint_selection_type: HintSelectionType
    important_hint_checks: ImportantHintChecks


def build_randomizer_pool_config(options: KH3Options) -> dict[str, int]:
    pools = {
        randomizer_name: int(getattr(options, option_name).value)
        for option_name, randomizer_name in RANDOMIZER_POOL_NAMES.items()
    }
    pools["Always On"] = VANILLA_LOCATION_POOL_VALUE
    # Level Up Limit controls AP checks; retain the backend's Sora pool mode
    # without exposing a separate setting for starting abilities or keyblades.
    pools["Sora"] = KH3LocationPoolMode.option_randomize
    # AP places each minigame independently; the backend receives exact rewards.
    pools["Minigames"] = KH3LocationPoolMode.option_randomize
    # AP's three workshop pools are authored as exact per-location overrides.
    pools["Moogle Workshop"] = VANILLA_LOCATION_POOL_VALUE
    return pools


def build_randomizer_exception_config(options: KH3Options) -> dict[str, bool]:
    exceptions = {
        randomizer_name: bool(getattr(options, option_name).value)
        for option_name, randomizer_name in RANDOMIZER_EXCEPTION_NAMES.items()
    }
    exceptions["Can Be None"] = False
    exceptions["Safe Magic Locations"] = True
    return exceptions


def build_randomizer_limiter_config(options: KH3Options) -> dict[str, int]:
    return {
        randomizer_name: int(getattr(options, option_name).value)
        for option_name, randomizer_name in RANDOMIZER_LIMITER_NAMES.items()
    }


def build_randomizer_qol_config(options: KH3Options) -> dict[str, dict[str, bool]]:
    qol: dict[str, dict[str, bool]] = {}
    for option_name, (category, randomizer_name) in RANDOMIZER_QOL_NAMES.items():
        qol.setdefault(category, {})[randomizer_name] = bool(getattr(options, option_name).value)
    # Area maps are always granted at the start of an AP seed.
    qol.setdefault("Items", {})["All Maps Unlocked"] = True
    # The backend still requires ITEM_004 to exist even though the public
    # zero-cost Forge setting has been superseded by Keyblade Levels.
    qol.setdefault("Items", {})["Easily Upgraded Keyblades"] = False
    return qol


def effective_keyblade_levels(options: KH3Options) -> int:
    """Return the selected Keyblade Levels mode."""
    return int(options.keyblade_levels.value)


def build_randomizer_runtime_config(options: KH3Options) -> dict[str, bool]:
    return {
        "auto_skip_cutscenes": bool(options.auto_skip_cutscenes.value),
        "skip_guardians_and_mickey_walk": bool(options.skip_guardians_and_mickey_walk.value),
        "fast_union_cross": bool(options.fast_union_cross.value),
        "fast_dance_minigame": bool(options.fast_dance_minigame.value),
        "fast_beat_of_lead": bool(options.fast_beat_of_lead.value),
        "fun_10k_heartless": bool(options.fun_10k_heartless.value),
    }


def build_randomizer_hint_config(options: KH3Options) -> dict[str, object]:
    checks = set(options.important_hint_checks.value)
    checks.difference_update(GOAL_MANAGED_HINT_CHECKS)
    checks.update(goal_key_item_counts(int(options.heart_piece_count.value)))
    return {
        "type": HintSelectionType.get_option_name(int(options.hint_selection_type.value)),
        "checks": sorted(checks),
    }


def build_randomizer_clear_condition_config(options: KH3Options) -> dict[str, object]:
    return {
        "type": "Heart Pieces",
        "conditions": {"KEY_ITEM17\u0000": int(options.heart_piece_count.value)},
        "worlds_required": int(options.worlds_required.value),
    }


def build_randomizer_generation_config(options: KH3Options) -> dict[str, object]:
    return {
        "pools": build_randomizer_pool_config(options),
        "boss_blacklist": sorted(options.boss_blacklist.value),
        "exceptions": build_randomizer_exception_config(options),
        "limiters": build_randomizer_limiter_config(options),
        "lucky_emblems_per_check": int(options.lucky_emblems_per_check.value),
        "battle_level_sync": int(options.battle_level_mode.value) != BattleLevelMode.option_off,
        "skip_world_intro_cutscenes": bool(options.skip_world_intro_cutscenes.value),
        "exp_multiplier": int(options.exp_multiplier.value) / 2,
        "qol": build_randomizer_qol_config(options),
        "runtime": build_randomizer_runtime_config(options),
        "hint": build_randomizer_hint_config(options),
        "clear_condition": build_randomizer_clear_condition_config(options),
    }


option_groups = [
    OptionGroup("Generation", [
        HeartPieceCount,
        WorldsRequired,
        DeathLink,
        DeathLinkAmnesty,
        BattleLevelMode,
        BattleLevelOffset,
        KeybladeLevels,
        Stylesanity,
        Chestsanity,
        UltimaWeaponMode,
        UltimaWeaponShardCount,
        TrapPercentage,
    ]),
    OptionGroup("KH3 Randomizer Rules", [
        PortalUnlocks,
        AllyCharacters,
        GateOpenerCount,
        DefaultBaseAbilities,
        AutoEquipMovementAbilities,
        StartWithWallRun,
        StartWithMagic,
        StartWithLinks,
        StartWithAttractionFlow,
        JunkGoldenHercRewards,
        LevelUpLimit,
    ], start_collapsed=True),
    OptionGroup("KH3 Stat Pools", [
        BaseSoraStatsPool,
        LevelUpStatsPool,
        KeybladeEnhanceStatsPool,
        EquipmentStatsPool,
        FoodEffectStatsPool,
        AllowAllStats,
        AllowNegativeStats,
        ExpMultiplier,
    ]),
    OptionGroup("KH3 World Pools", [
        IncludeSpace,
        IncludeGrindySpaceMissions,
        IncludeSpaceSuperbosses,
        OlympusPool,
        TwilightTownPool,
        ToyBoxPool,
        KingdomOfCoronaPool,
        MonstropolisPool,
        HundredAcreWoodPool,
        ArendellePool,
        SanFransokyoPool,
        JunkSanFransokyoChests,
        TheCaribbeanPool,
        JunkTheCaribbeanChests,
        KeybladeGraveyardPool,
        ReMindPool,
        DarkWorldPool,
        UnrealityPool,
    ]),
    OptionGroup("KH3 Reward Pools", [
        EquipmentAbilitiesPool,
        DataBattleRewardsPool,
        FullcourseAbilitiesPool,
        Ingredients,
        AdversaryChecks,
        BattlegatesPool,
    ]),
    OptionGroup("KH3 Lucky Emblems", [
        LuckyEmblemsPool,
        LuckyEmblemsPerCheck,
        LuckyEmblemLimit,
    ]),
    OptionGroup("KH3 Minigames", [
        FlantasticFlansPool,
        VerumRexPool,
        VerumRexMaxRank,
        FestivalDancePool,
        FestivalDanceMaxRank,
        FrozenSliderPool,
        FrozenSliderMaxRank,
        FlashTracerCourseAPool,
        FlashTracerCourseAMaxRank,
        FlashTracerCourseBPool,
        FlashTracerCourseBMaxRank,
        ClassicKingdomChecks,
        JunkClassicKingdomReward,
    ]),
    OptionGroup("KH3 Moogle", [
        SynthesisPool,
        MoogleSynthesisLimit,
        PhotoMissionsPool,
        KeybladeUpgradeRewardsPool,
        MoogleShopChecks,
        MoogleShopPrice,
    ]),
    OptionGroup("Third Party Mods", [
        KH02Enabled,
        KH02Challenges,
        OlympusColiseumPool,
        MusicChestCount,
        MusicChestVanillaConversion,
        MelodyOfMemorySongWhitelist,
        MelodyOfMemorySongBlacklist,
        MelodyOfMemoryDifficulty,
        MelodyOfMemoryParty,
        MelodyOfMemoryPlayStyle,
        MelodyOfMemoryStartingItem,
    ]),
    OptionGroup("KH3 Enemy and Party Pools (Experimental)", [
        EnemiesPool,
        EnemyChaos,
        BossesPool,
        BossBlacklist,
        DefaultDataBattles,
        DefaultEndGameBattles,
        DefaultYozoraBattle,
        PartyMembersPool,
        PartyChaos,
    ]),
    OptionGroup("KH3 Cosmetics", [
        ColorRandomization,
        MusicShuffle,
        IncludeCustomMusic,
        FormThemes,
    ]),
    OptionGroup("KH3 Quality of Life", [
        EasierMiniUfo,
        FasterRagingVulture,
        FasterLichSequence,
        DarkBaymaxPhase1Skip,
        FrozenChaseSkip,
        FasterCrabCollection,
        FasterSoraCollection,
        GuardiansOfLightSkip,
        Fun10kHeartless,
        SkipGuardiansAndMickeyWalk,
        FastUnionCross,
        FastBeatOfLead,
        FastDanceMinigame,
        MaxLevelShip,
        AutoSkipCutscenes,
        SkipWorldIntroCutscenes,
    ], start_collapsed=True),
    OptionGroup("Map Options", [
        MapOverlayEnabled, MapOverlayKeybinds, HideLockedChestIcons, SongDisplay,
        MapChestIcons, MapEmblemIcons, MapIngredientIcons, MinimapIcons, LargeMapIcons,
    ]),
    OptionGroup("KH3 Hints", [
        HintSelectionType,
        ImportantHintChecks,
    ], start_collapsed=True),
]
