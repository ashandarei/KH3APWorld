from functools import cache

from BaseClasses import ItemClassification, LocationProgressType
from rule_builder.rules import (
    And,
    CanReachLocation,
    CanReachRegion,
    False_,
    Has,
    HasAll,
    HasAllCounts,
    HasAny,
    HasFromListUnique,
    Or,
    True_,
)
from worlds.generic.Rules import add_item_rule

from .Battlegates import BATTLEGATE_SOURCE_ID_REGION_LOCKS
from .CharacterUnlocks import DATA_BATTLE_COMPLETION_SOURCE_KEYS
from .ClassicKingdomLogic import HasClassicKingdomGames
from .ColiseumLogic import (
    COLISEUM_CUP_REQUIRED_WORLDS,
    COLISEUM_POSTGAME_CUP_IDS,
    COLISEUM_WORLD_COMPLETION_SOURCES,
)
from .Data import (
    CHEST_LOGIC_REQUIREMENTS,
    CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID,
    CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID,
    CONTROLLER_UNLOCK_ITEM_NAMES_BY_OPTION,
    DEFAULT_BASE_ABILITY_ITEM_NAMES,
    EVENT_LOGIC_REQUIREMENTS,
    HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT,
    INGREDIENT_LOGIC_REQUIREMENTS,
    LEVEL_UP_XP_SOURCE_KEYBLADE_ITEM_NAMES,
    LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENTS,
    LEVEL_UP_XP_SOURCE_REGION_NAMES,
    LOCATION_LOGIC_DEPENDENCIES,
    LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD,
    PORTAL_KEYBLADE_REGION_LOCKS,
    PORTAL_PARTY_ITEM_NAMES,
    PORTAL_PARTY_REGION_LOCKS,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE,
    THE_CARIBBEAN_PORTAL_LOGIC_REQUIREMENTS,
    TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES,
    TWILIGHT_TOWN_PORTAL_LOGIC_REQUIREMENTS,
    VICTORY_BONUS_LOGIC_REQUIREMENTS,
    WORLD_DIFFICULTY_TIERS,
    goal_required_item_counts,
    required_level_up_xp_source_count,
    uses_level_up_junk,
    uses_minigame_rank_junk,
    uses_replace_location_pool,
    uses_strict_junk_setting,
)
from .Ingredients import INGREDIENT_SPOTS_BY_ID, ingredient_spot_is_junk
from .Adversaries import ADVERSARIES_BY_ID, ADVERSARY_WORLDS
from .Locations import COMPLETION_LOCATION_NAME
from .Logic import MAGIC_UNLOCK_ITEM
from .Regions import HUB_REGION_NAME, KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME
from .kh02.locations import KH02_MAGIC_REQUIRED_SOURCE_IDS, KH02_WORLD
from .rule_helpers import CountReachableRules, CountRules, requirement_rule
from .Workshop import (
    PHOTO_MISSION_ADDITIONAL_SOURCES,
    PHOTO_MISSION_COMPLETION_WORLDS,
    PHOTO_WORLD_COMPLETION_EXTRA_SOURCES,
)

ALWAYS_LEVEL_UP_FORBIDDEN_ITEM_NAMES = frozenset(PORTAL_KEYBLADE_REGION_LOCKS.values())
NON_JUNK_ITEM_FLAGS = int(ItemClassification.progression | ItemClassification.useful)
# Treescape cannot be revisited from the labyrinth before the player can escape.
ARENDELLE_STARTING_CHEST_SOURCE_KEYS = frozenset({
    ("TreasureFZ", "FZ_LBOX_001"),
    ("TreasureFZ", "FZ_SBOX_001"),
    ("TreasureFZ", "FZ_SBOX_002"),
    ("TreasureFZ", "FZ_LBOX_003"),
    ("MusicChest", "AP_LBOX_008"),
})


def is_junk_item(item) -> bool:
    """Shared fill predicate; avoid rebuilding IntFlag values on every check."""
    return not (int(item.classification) & NON_JUNK_ITEM_FLAGS)


def is_level_up_forbidden_item(item, player: int, forbidden_item_names: frozenset[str]) -> bool:
    return item.player == player and item.name in forbidden_item_names


def set_rules(world) -> None:
    player = world.player
    party_portals = world.options.portal_unlocks.value == 1
    rows_by_source_key = {
        (row["source_table"], row["source_id"]): row
        for row in world.active_locations + world.logic_reference_locations
    }
    active_source_keys = {(row["source_table"], row["source_id"]) for row in world.active_locations}
    starting_controller_unlocks = frozenset(
        item_name
        for option_name, item_name in CONTROLLER_UNLOCK_ITEM_NAMES_BY_OPTION.items()
        if bool(getattr(world.options, option_name).value)
    )
    starting_logic_item_names = starting_controller_unlocks
    if bool(world.options.default_base_abilities.value):
        starting_logic_item_names = starting_logic_item_names | DEFAULT_BASE_ABILITY_ITEM_NAMES

    final_door_required_item_counts = goal_required_item_counts(int(world.options.heart_piece_count.value))
    for starting_item_name in starting_controller_unlocks:
        final_door_required_item_counts.pop(starting_item_name, None)
    level_up_forbidden_item_names = ALWAYS_LEVEL_UP_FORBIDDEN_ITEM_NAMES | frozenset(
        final_door_required_item_counts
    )
    if party_portals:
        level_up_forbidden_item_names |= PORTAL_PARTY_ITEM_NAMES
    world.level_up_forbidden_item_names = level_up_forbidden_item_names
    existing_region_names = {region.name for region in world.multiworld.get_regions(player)}

    def can_reach_source(source_key, *, chest_area=False):
        row = rows_by_source_key[source_key]
        if chest_area and row.get("type") == "treasure":
            # Ingredient anchors describe the route to a chest's area. Opening
            # that chest is a separate action, so do not inherit its seed key.
            return CanReachRegion(row["world"]) & location_rule(source_key, include_chest_lock=False)
        if source_key in active_source_keys:
            # Query the finished location rule at evaluation time.
            return CanReachLocation(row["name"])
        # Disabled rewards still gate later story checks. Inline their logic;
        # they must not become AP locations, events, or exported checks.
        return CanReachRegion(row["world"]) & location_rule(source_key)

    def can_complete_coliseum_world(region_name):
        return CanReachRegion(region_name) & And(*(
            can_reach_source(source_key)
            for source_key in COLISEUM_WORLD_COMPLETION_SOURCES[region_name]
        ))

    xp_source_rules = tuple(
        CanReachRegion(region_name) & requirement_rule(
            LEVEL_UP_XP_SOURCE_LOGIC_REQUIREMENTS.get(region_name, ((),)), starting_logic_item_names,
        )
        for region_name in sorted(LEVEL_UP_XP_SOURCE_REGION_NAMES)
        if region_name in existing_region_names
    )
    lucky_emblem_rules = tuple(
        CanReachRegion(region_name) & requirement_rule(routes or ((),), starting_logic_item_names)
        for region_name, spot_requirements in sorted(LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD.items())
        if region_name in existing_region_names
        for routes in spot_requirements
    )

    @cache
    def location_rule(source_key, *, include_chest_lock=True):
        row = rows_by_source_key[source_key]
        rule = True_()
        if (source_key[0] == "KH02" and source_key[1] in KH02_MAGIC_REQUIRED_SOURCE_IDS
                and MAGIC_UNLOCK_ITEM not in starting_logic_item_names):
            rule &= Has(MAGIC_UNLOCK_ITEM)
        if row.get("type") == "character_unlock" and "trigger_bridge_key" in row:
            rule &= can_reach_source(tuple(row["trigger_bridge_key"].split(":", 1)))
            if row.get("required_item_names"):
                rule &= HasAll(*row["required_item_names"])
        if row.get("type") == "character_unlock" and row["source_id"] == "Riku":
            rule &= And(*(can_reach_source(key) for key in DATA_BATTLE_COMPLETION_SOURCE_KEYS))
        if row["source_table"] == "Adversary":
            # Optional non-progression rewards use coarse source-world access.
            # Do not infer story/encounter availability from authored coordinates.
            rule &= Or(*(CanReachRegion(name) for name in ADVERSARY_WORLDS[row["source_id"]]))
            if ADVERSARIES_BY_ID[row["source_id"]]["name"] == "Sorcerer":
                rule &= can_reach_source(("Event", "EVENT_KEYBLADE_002"))
        if row["source_table"] == "IngredientSpot":
            rule &= CanReachRegion(INGREDIENT_SPOTS_BY_ID[row["source_id"]]["world"])
        if row.get("type") == "photo_mission":
            mission = row["source_id"]
            for region in PHOTO_MISSION_COMPLETION_WORLDS[mission]:
                if region in PHOTO_WORLD_COMPLETION_EXTRA_SOURCES:
                    rule &= CanReachRegion(region) & And(*(
                        can_reach_source(key) for key in PHOTO_WORLD_COMPLETION_EXTRA_SOURCES[region]))
                else:
                    rule &= can_complete_coliseum_world(region)
            rule &= And(*(can_reach_source(key) for key in PHOTO_MISSION_ADDITIONAL_SOURCES.get(mission, ())))
        elif row.get("type") == "synthesis":
            rule &= And(*(can_complete_coliseum_world(name) for name in COLISEUM_WORLD_COMPLETION_SOURCES))
            if row["synthesis_order"] > 15:
                rule &= CanReachLocation(COMPLETION_LOCATION_NAME)
        if row.get("type") == "keyblade_upgrade":
            keyblade = row["keyblade_name"]
            mode = int(world.options.keyblade_levels.value)
            level_item = (PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAME_BY_KEYBLADE[keyblade]
                          if mode == 2 else "Progressive Keyblade Level")
            rule &= Has(keyblade)
            if mode != 0:
                rule &= Has(level_item, row["keyblade_level"])
        if row.get("type") == "music_chest_bonus":
            rule &= CanReachLocation(row["music_chest_parent_location_name"])
        if include_chest_lock and row.get("keyblade_chest_keyblade") and not row.get("keyblade_chest_native"):
            rule &= Has(row["keyblade_chest_keyblade"])
        if row["source_table"] == "Event":
            battlegate_region = BATTLEGATE_SOURCE_ID_REGION_LOCKS.get(row["source_id"])
            if battlegate_region is not None:
                rule &= CanReachRegion(battlegate_region)

        if row["source_table"] == "OlympusColiseumCup":
            cup_id = row["source_id"]
            rule &= And(True_(), *(can_complete_coliseum_world(name)
                                   for name in COLISEUM_CUP_REQUIRED_WORLDS[cup_id]))
            if cup_id in COLISEUM_POSTGAME_CUP_IDS:
                rule &= CanReachLocation(COMPLETION_LOCATION_NAME)

        if row.get("classic_kingdom_check_kind") or row.get("source_id") == CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID:
            required_unlock_item = CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.get(str(row.get("source_id") or ""))
            if row.get("source_id") == CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID:
                predicate = (HasClassicKingdomGames if world.options.junk_classic_kingdom_reward.value
                             and not world.options.classic_kingdom_checks.value else HasAll)
                rule &= predicate(*CLASSIC_KINGDOM_UNLOCK_ITEM_NAMES_BY_GAME_ID.values())
            elif required_unlock_item is not None:
                rule &= Has(required_unlock_item)

        if row["source_table"] == "LevelUp":
            try:
                level = int(str(row["source_id"]))
            except ValueError:
                level = 0
            rule &= (CountReachableRules(*xp_source_rules, count=required_level_up_xp_source_count(level),
                                label="reachable XP sources") if xp_source_rules else False_())

        if row["source_table"] == "LuckyMark":
            try:
                required_emblem_count = int(str(row["source_id"]))
            except ValueError:
                required_emblem_count = 0
            rule &= (CountReachableRules(*lucky_emblem_rules, count=required_emblem_count,
                                label="reachable Lucky Emblems") if required_emblem_count > 0 else False_())

        routes = (CHEST_LOGIC_REQUIREMENTS.get(source_key)
                  or VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key)
                  or EVENT_LOGIC_REQUIREMENTS.get(source_key)
                  or INGREDIENT_LOGIC_REQUIREMENTS.get(source_key))
        if routes is not None:
            rule &= requirement_rule(routes, starting_logic_item_names)

        dependency = LOCATION_LOGIC_DEPENDENCIES.get(source_key)
        if dependency is not None:
            missing = tuple(key for key in dependency.source_keys if key not in rows_by_source_key)
            if missing:
                raise ValueError(
                    f"KH3 location dependency for {source_key!r} references inactive locations: {missing!r}"
                )
            prerequisites = tuple(
                can_reach_source(key, chest_area=row["source_table"] == "IngredientSpot")
                for key in dependency.source_keys
            )
            rule &= And(*prerequisites) if dependency.require_all else Or(*prerequisites)
        return rule

    if world.options.include_space.value:
        world.space_world_completion_rules = tuple(
            can_complete_coliseum_world(name) for name in COLISEUM_WORLD_COMPLETION_SOURCES
        )

    hub = world.multiworld.get_region(HUB_REGION_NAME, player)
    for entrance in hub.exits:
        if not entrance.name.startswith("To "):
            continue
        region_name = entrance.name[3:]
        required_keyblade = PORTAL_KEYBLADE_REGION_LOCKS.get(region_name)
        rule = True_()
        if region_name == "Final Door":
            rule = HasAllCounts(final_door_required_item_counts) if final_door_required_item_counts else True_()
            required_worlds = int(world.options.worlds_required.value)
            if required_worlds:
                completion_sources = {
                    **COLISEUM_WORLD_COMPLETION_SOURCES,
                    "Dark World": (("VBonus", "Vbonus_067"),),
                }
                rule &= CountReachableRules(*(
                    CanReachRegion(region) & And(True_(), *(can_reach_source(key) for key in sources))
                    for region, sources in completion_sources.items()
                    if region in existing_region_names
                ), count=required_worlds, label="Completed worlds")
        elif region_name == KH02_WORLD:
            # Aqua supplies the chapter's combat and movement abilities.
            rule = Has("Oblivion")
        elif region_name == KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME:
            rule = Or(*(CanReachRegion(name) for name in ("Keyblade Graveyard", "Re Mind")
                        if name in existing_region_names))
        elif party_portals and region_name in PORTAL_PARTY_REGION_LOCKS:
            rule = HasAny(*PORTAL_PARTY_REGION_LOCKS[region_name])
        elif required_keyblade is not None:
            rule = Has(required_keyblade)
        elif region_name == "Level Ups":
            rule = (Or(*(HasAny(*PORTAL_PARTY_REGION_LOCKS[name])
                         for name in sorted(LEVEL_UP_XP_SOURCE_REGION_NAMES)))
                    if party_portals else HasAny(*LEVEL_UP_XP_SOURCE_KEYBLADE_ITEM_NAMES))
        # Story worlds rely on their check-level combat requirements.
        # Only Data Battles retains the three tier-one world access gate.
        if region_name == "Dark World" and world.options.kh02_enabled.value:
            # The Dark Aqua route follows the KH0.2 chapter through this portal.
            rule &= Has("Oblivion")
        if region_name == "Data Battles":
            rule &= (CountRules(*(HasAny(*PORTAL_PARTY_REGION_LOCKS[name])
                                  for name in sorted(WORLD_DIFFICULTY_TIERS[1])
                                  if name in PORTAL_PARTY_REGION_LOCKS),
                                count=HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT, label="Open tier-one worlds")
                     if party_portals else HasFromListUnique(
                         *TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES, count=HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT))
        if region_name == "Twilight Town":
            rule &= requirement_rule(TWILIGHT_TOWN_PORTAL_LOGIC_REQUIREMENTS, starting_logic_item_names)
        if region_name == "Keyblade Graveyard":
            rule &= Has("Unlock: Attraction Flow")
        if region_name == "The Caribbean":
            rule &= requirement_rule(THE_CARIBBEAN_PORTAL_LOGIC_REQUIREMENTS, starting_logic_item_names)
        if region_name == "Olympus Coliseum":
            rule &= can_complete_coliseum_world("Olympus")
        # Rule Builder registers indirect region dependencies for these entrances.
        world.set_rule(entrance, rule)

    for row in world.active_locations:
        source_key = (row["source_table"], row["source_id"])
        location = world.multiworld.get_location(row["name"], player)
        world.set_rule(location, location_rule(source_key))

        # Item placement restrictions are separate from access rules.
        parent_source_key = (tuple(row["source_id"].split(":", 1))
                             if row.get("type") == "music_chest_bonus" else source_key)
        if parent_source_key in ARENDELLE_STARTING_CHEST_SOURCE_KEYS:
            add_item_rule(location, lambda item: not item.advancement)
            location.progression_allowed = False
        location.filler_only = (uses_replace_location_pool(row, world.options)
                or uses_strict_junk_setting(row, world.options)
                or (row.get("type") == "keyblade_upgrade" and int(world.options.keyblade_levels.value) == 0)
                or uses_minigame_rank_junk(row, world.options)
                or ingredient_spot_is_junk(row)
                or uses_level_up_junk(row, world.options))
        if "fixed_character_reward" in row:
            location.filler_only = False
        if location.filler_only:
            location.progress_type = LocationProgressType.EXCLUDED
            add_item_rule(location, is_junk_item)
        if row["source_table"] == "LevelUp":
            add_item_rule(
                location,
                lambda item, player=player, forbidden_item_names=level_up_forbidden_item_names:
                    not is_level_up_forbidden_item(item, player, forbidden_item_names),
            )
