from collections import defaultdict

from BaseClasses import Entrance, ItemClassification, MultiWorld, Region

from .ColiseumLogic import COLISEUM_WORLD_COMPLETION_SOURCES
from .Data import LEVEL_UP_XP_SOURCE_REGION_NAMES, LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD
from .Ingredients import INGREDIENT_WORLD_SOURCES
from .Adversaries import ADVERSARY_WORLDS
from .Locations import (
    COMPLETION_LOCATION_NAME,
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
    ROCK_TITAN_VBONUS_SOURCE_KEY,
    KH3Location,
    location_name_to_id,
)
from .Space import REGIONS as SPACE_REGIONS

HUB_REGION_NAME = "Garden of Assemblage"
KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME = "Keyblade Graveyard / Re Mind Shared Chests"
KEYBLADE_GRAVEYARD_REMIND_SHARED_SOURCE_KEYS = frozenset({
    ("TreasureKG", "KG_LBOX_002"),
    ("TreasureKG", "KG_SBOX_001"),
    ("TreasureKG", "KG_SBOX_002"),
    ("TreasureKG", "KG_SBOX_003"),
})


def create_regions(multiworld: MultiWorld, player: int, locations: list[dict],
                   logic_references: list[dict] = ()) -> None:
    menu = Region("Menu", player, multiworld)
    hub = Region(HUB_REGION_NAME, player, multiworld)

    entrance = Entrance(player, "Start KH3", menu)
    menu.exits.append(entrance)
    entrance.connect(hub)

    regions = [menu, hub]
    world_groups = defaultdict(list)
    for location in locations:
        source_key = (location["source_table"], location["source_id"])
        if location.get("type") == "music_chest_bonus":
            source_key = tuple(location["trigger_bridge_key"].split(":", 1))
        if source_key == ("VBonus", "Vbonus_082") or location.get("trigger_bridge_key") == "VBonus:Vbonus_082":
            # Opening Darkside precedes the hub and needs no portal keyblade.
            hub.locations.append(KH3Location(
                player, location["name"], location_name_to_id[location["name"]], hub))
            continue
        region_name = (
            KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME
            if source_key in KEYBLADE_GRAVEYARD_REMIND_SHARED_SOURCE_KEYS
            else location.get('space_zone') or location["world"]
        )
        world_groups[region_name].append(location)

    for reference in logic_references:
        world_groups.setdefault(reference["world"], [])

    if "Space" in world_groups:
        from .Space import LANDING_WORLDS
        for world_name in LANDING_WORLDS:
            world_groups.setdefault(world_name, [])

    if "Level Ups" in world_groups:
        for world_name in LEVEL_UP_XP_SOURCE_REGION_NAMES:
            world_groups.setdefault(world_name, [])

    if "Lucky Emblems" in world_groups:
        # Every emblem world participates in milestone logic, even if no active
        # reward check created its region.
        for world_name, spot_requirements in LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD.items():
            if spot_requirements:
                world_groups.setdefault(world_name, [])

    if "Olympus Coliseum" in world_groups or "Moogle Workshop" in world_groups:
        for world_name in COLISEUM_WORLD_COMPLETION_SOURCES:
            world_groups.setdefault(world_name, [])
    if any(row.get("type") == "photo_mission" for row in locations):
        world_groups.setdefault("100 Acre Wood", [])
    if "Ingredients" in world_groups:
        for alternatives in INGREDIENT_WORLD_SOURCES.values():
            for world_name in alternatives:
                world_groups.setdefault(world_name, [])
    if "Adversaries" in world_groups:
        for world_names in ADVERSARY_WORLDS.values():
            for world_name in world_names:
                world_groups.setdefault(world_name, [])

    world_regions = {name: Region(name, player, multiworld) for name in sorted(world_groups)}
    regions.extend(world_regions.values())
    for world_name, world_locations in sorted(world_groups.items()):
        region = world_regions[world_name]
        parent = world_regions['Space'] if world_name in SPACE_REGIONS else hub

        world_entrance = Entrance(player, f"To {world_name}", parent)
        parent.exits.append(world_entrance)
        world_entrance.connect(region)

        for location in sorted(world_locations, key=lambda entry: entry["name"]):
            region.locations.append(
                KH3Location(player, location["name"], location_name_to_id[location["name"]], region)
            )
            if location.get("type") == "adversary":
                # These optional checks have incomplete encounter/access logic.
                # Only filler and useful rewards are supported for every player.
                region.locations[-1].item_rule = lambda item: item.classification in (
                    ItemClassification.filler, ItemClassification.useful)
                region.locations[-1].progression_allowed = False

        if world_name == "Olympus" and any(
            (location["source_table"], location["source_id"]) == ROCK_TITAN_VBONUS_SOURCE_KEY
            for location in world_locations
        ):
            region.locations.append(
                KH3Location(
                    player,
                    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME,
                    ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_ID,
                    region,
                )
            )

    final_door = Region("Final Door", player, multiworld)
    regions.append(final_door)

    final_entrance = Entrance(player, "To Final Door", hub)
    hub.exits.append(final_entrance)
    final_entrance.connect(final_door)

    final_door.locations.append(
        KH3Location(player, COMPLETION_LOCATION_NAME, None, final_door)
    )

    multiworld.regions.extend(regions)
