"""Share KH3 access requirements through AP's ordinary region reachability."""

from collections import defaultdict

from BaseClasses import DEFAULT_COLLECTION_RULE, Entrance, Location, Region
from rule_builder.rules import And, CanReachLocation, Rule

from .Locations import ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME, ROCK_TITAN_VBONUS_SOURCE_KEY


def required_location_names(rule):
    """Find unconditional location conjuncts; leave alternatives as predicates."""
    if rule.options:
        return
    if isinstance(rule, CanReachLocation):
        yield rule.location_name
    elif isinstance(rule, And):
        for child in rule.children:
            yield from required_location_names(child)


def create_logic_regions(world, rules) -> None:
    """Move equal predicates into shared entrances, without changing checks.

    World/space regions remain the portal and travel gates. A nontrivial check
    becomes a child region, grouped with checks having the same access rule in
    that world. This also caches single encounter stages used by later story
    checks. Mandatory prerequisites in the same world also provide direct
    parent regions, letting AP schedule later stages after earlier stages.
    The full predicate remains so edits to a prerequisite's finished location
    rule still apply. Rules are resolved again after moving locations so Rule Builder
    registers their new parent regions as indirect entrance dependencies.
    """
    multiworld = world.multiworld
    rows = {row["name"]: row for row in world.active_locations}
    groups = defaultdict(list)
    inherited = []
    for location, rule in rules.items():
        if not isinstance(location, Location):
            continue
        row = rows.get(location.name)
        if row is None or row["type"] in {"vbonus_extra", "music_chest_bonus"}:
            continue
        resolved = location.access_rule
        if resolved is DEFAULT_COLLECTION_RULE or getattr(resolved, "always_true", False):
            continue
        # Only Rule Builder predicates are static enough to share. Keep any
        # custom callable on its original check.
        if isinstance(rule, Rule):
            groups[location.parent_region, resolved].append(location)

    gates = []
    moved = set()
    for (parent, _), locations in groups.items():
        region = Region(f"Access: {locations[0].name}", world.player, multiworld)
        multiworld.regions.append(region)
        gates.append((parent, region, rules[locations[0]]))
        for location in locations:
            parent.locations.remove(location)
            location.parent_region = region
            region.locations.append(location)
            moved.add(location)

    # Extra encounter rewards and Rock Titan's fixed bonus share the actual
    # encounter region, while retaining their own item/exclusion rules.
    sources = {(row["source_table"], row["source_id"]): row for row in world.active_locations}
    for row in world.extra_vbonus_locations:
        parent_row = sources[tuple(row["trigger_bridge_key"].split(":", 1))]
        inherited.append((multiworld.get_location(row["name"], world.player),
                          multiworld.get_location(parent_row["name"], world.player)))
    for row in world.active_locations:
        if row["type"] == "music_chest_bonus":
            inherited.append((multiworld.get_location(row["name"], world.player),
                              multiworld.get_location(row["music_chest_parent_location_name"], world.player)))
    if ROCK_TITAN_VBONUS_SOURCE_KEY in sources:
        parent = multiworld.get_location(sources[ROCK_TITAN_VBONUS_SOURCE_KEY]["name"], world.player)
        inherited.append((multiworld.get_location(ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME, world.player),
                          parent))
    for location, parent in inherited:
        if location.parent_region is not parent.parent_region:
            location.parent_region.locations.remove(location)
            location.parent_region = parent.parent_region
            parent.parent_region.locations.append(location)

    # Discard only the provisional registrations for this world's entrances.
    # Other games' indirect conditions remain intact.
    entrances = {spot for spot in rules if isinstance(spot, Entrance)}
    for indirects in multiworld.indirect_connections.values():
        indirects.difference_update(entrances)
    for spot, rule in rules.items():
        if spot in moved:
            spot.access_rule = DEFAULT_COLLECTION_RULE
        else:
            world.set_rule(spot, rule)
    base_parents = {region: parent for parent, region, _ in gates}
    chain_parents = {}
    for base_parent, region, rule in gates:
        parent = base_parent
        for name in required_location_names(rule):
            candidate = multiworld.get_location(name, world.player).parent_region
            if candidate is region or base_parents.get(candidate) is not base_parent:
                continue
            # Grouped rules can share a region. Never turn such sharing into a
            # cycle when choosing a prerequisite as the physical parent.
            ancestor = candidate
            while ancestor in chain_parents and ancestor is not region:
                ancestor = chain_parents[ancestor]
            if ancestor is region:
                continue
            parent = candidate
            break
        chain_parents[region] = parent
        entrance = parent.connect(region, name=f"{base_parent.name} -> {region.name}")
        world.set_rule(entrance, rule)
    # The fixed bonus inherited the former local encounter predicate directly.
    # Its shared parent now carries that predicate.
    for location, _ in inherited:
        if location.name == ROCK_TITAN_NO_WALL_RUN_BONUS_LOCATION_NAME:
            location.access_rule = DEFAULT_COLLECTION_RULE

    world.logic_region_count = len(gates)
    world.logic_region_location_count = len(moved)
    world.logic_chain_region_count = sum(parent is not base_parents[region]
                                       for region, parent in chain_parents.items())
