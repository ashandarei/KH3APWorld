"""Identify rewards that cannot open protected checks for stock AP balancing."""

from collections import Counter, deque

from BaseClasses import DEFAULT_COLLECTION_RULE, Entrance, ItemClassification, Location, LocationProgressType, Region
from rule_builder.rules import (
    And,
    Has,
    HasAll,
    HasAllCounts,
    HasAny,
    HasAnyCount,
    HasFromList,
    HasFromListUnique,
    Or,
    Rule,
)

from .rule_helpers import CountReachableRules, CountRules


def rule_item_count_bounds(rule):
    """Maximum inventory needed by a known predicate; unknown counts stay unlimited."""
    if type(rule) in {And.Resolved, Or.Resolved, CountRules.Resolved, CountReachableRules.Resolved}:
        return {}
    if type(rule) is Has.Resolved:
        return {rule.item_name: rule.count}
    if type(rule) in {HasAny.Resolved, HasFromListUnique.Resolved}:
        return {}
    if type(rule) is HasAll.Resolved:
        return dict.fromkeys(rule.item_names, 1)
    if type(rule) in {HasAllCounts.Resolved, HasAnyCount.Resolved}:
        return dict(rule.item_counts)
    if type(rule) is HasFromList.Resolved:
        return dict.fromkeys(rule.item_names, rule.count)
    return dict.fromkeys(rule.item_dependencies(), None)


def balancing_inventory(multiworld, worlds):
    """Index possible logical receipts once per room, without collecting a state."""
    inventory = {player: multiworld.state.prog_items[player].copy() for player in worlds}
    seen = set()
    for item in multiworld.get_items():
        if item.player not in worlds or id(item) in seen:
            continue
        seen.add(id(item))
        name = worlds[item.player].collect_item(multiworld.state, item)
        if name:
            inventory[item.player][name] += 1
    return inventory


def cover_unique_requirements(counts, groups, state, player, inventory):
    """Choose a deterministic sufficient set for atomic unique-item alternatives.

    Every child of composite rules is still protected. Only the alternatives
    within a known unique-list predicate are interchangeable. Reuse mandatory
    and starting items first, then prefer items that cover more remaining groups.
    This selects balancing candidates; it never changes access or item inventory.
    """
    for names, _ in groups:
        for name in names:
            counts.setdefault(name, 0)
    available = {name for names, _ in groups for name in names if state.count(name, player)}
    available.update(name for name, count in counts.items() if count is None or count > 0)
    possible = {name for name, count in inventory.items() if count > 0}
    pending = [((names - available) & possible, max(0, count - len(names & available))) for names, count in groups]
    pending = [(names, count) for names, count in pending if count > 0]
    while pending:
        scores = Counter(name for names, _ in pending for name in names)
        if not scores:
            break  # An impossible predicate stays impossible; fill validates it.
        chosen = min(scores, key=lambda name: (-scores[name], name))
        counts[chosen] = max(1, counts.get(chosen, 0))
        pending = [(names - {chosen}, count - int(chosen in names))
                   for names, count in pending if count - int(chosen in names) > 0]
    return counts


def inventory_route(rule, player):
    """A canonical positive inventory conjunction, or None for other semantics."""
    if rule.player != player or rule.force_recalculate:
        return None
    if type(rule) is And.Resolved:
        children = [inventory_route(child, player) for child in rule.children]
        if any(child is None for child in children):
            return None
        counts, groups = {}, set()
        for child_counts, child_groups in children:
            for name, count in child_counts:
                counts[name] = max(counts.get(name, 0), count)
            groups.update(child_groups)
    elif type(rule) in {Has.Resolved, HasAll.Resolved, HasAllCounts.Resolved}:
        counts, groups = rule_item_count_bounds(rule), set()
    elif type(rule) in {HasAny.Resolved, HasFromListUnique.Resolved}:
        counts = {}
        groups = {(tuple(sorted(rule.item_names)), getattr(rule, "count", 1))}
    else:
        return None
    return tuple(sorted(counts.items())), tuple(sorted(groups))


def cover_inventory_routes(counts, alternatives, state, player, inventory):
    """Balance one sufficient route per known OR, reusing already required items."""
    for routes in sorted(alternatives):
        candidates = []
        for requirements, groups in routes:
            if (any(inventory[name] < count for name, count in requirements)
                    or any(sum(inventory[name] > 0 for name in names) < count for names, count in groups)):
                continue
            candidate = counts.copy()
            for name, count in requirements:
                if candidate.get(name, 0) is not None:
                    candidate[name] = max(candidate.get(name, 0), count)
            cover_unique_requirements(candidate, {(frozenset(names), count) for names, count in groups},
                                      state, player, inventory)
            cost = sum(max(0, count - state.count(name, player))
                       - max(0, counts.get(name, 0) - state.count(name, player))
                       for name, count in candidate.items() if count is not None)
            candidates.append((cost, (requirements, groups), candidate))
        if not candidates:
            return None
        counts = min(candidates, key=lambda entry: entry[:2])[2]
    return counts


def protected_check_item_counts(world, inventory=None):
    """Follow the finished access graph; unknown predicates keep normal balancing.

    Excluded/junk checks still retain all their rules and progression items. They
    simply are not reasons to pull an item forward in AP's balancing stage.
    Dependencies used by the goal or any non-excluded check remain balanced,
    including indirect, alternative and counted requirements.
    """
    multiworld, player = world.multiworld, world.player
    pending = deque(location for location in multiworld.get_locations(player)
                    if (location.address is not None and location.progress_type != LocationProgressType.EXCLUDED)
                    or (location.item is not None and location.item.advancement and location.item.code is None))
    pending.append(multiworld.completion_condition[player])
    seen, counts, groups, alternatives = set(), {}, set(), set()
    if inventory is None:
        inventory = balancing_inventory(multiworld, {player: world})[player]
    default_rule = getattr(DEFAULT_COLLECTION_RULE, "__func__", DEFAULT_COLLECTION_RULE)
    while pending:
        spot = pending.popleft()
        if spot in seen:
            continue
        seen.add(spot)
        if isinstance(spot, Region):
            pending.extend(spot.entrances)
            continue
        if isinstance(spot, Rule.Resolved):
            rule = spot
        elif isinstance(spot, (Location, Entrance)):
            pending.append(spot.parent_region)
            rule = spot.access_rule
        else:
            return None
        if rule is DEFAULT_COLLECTION_RULE or rule is default_rule:
            continue
        if (not isinstance(rule, Rule.Resolved) or rule.player != player
                or rule.force_recalculate):
            return None
        if type(rule) is Or.Resolved:
            routes = tuple(inventory_route(child, player) for child in rule.children)
            if routes and all(route is not None for route in routes):
                alternatives.add(tuple(sorted(set(routes))))
                for name in rule.item_dependencies():
                    counts.setdefault(name, 0)
                continue
        pending.extend(getattr(rule, "children", ()))
        if hasattr(rule, "child"):
            pending.append(rule.child)
        if type(rule) in {HasAny.Resolved, HasFromListUnique.Resolved}:
            groups.add((frozenset(rule.item_names), getattr(rule, "count", 1)))
        for name, count in rule_item_count_bounds(rule).items():
            old = counts.get(name, 0)
            counts[name] = None if old is None or count is None else max(old, count)
        pending.extend(multiworld.get_region(name, player) for name in rule.region_dependencies())
        pending.extend(multiworld.get_location(name, player) for name in rule.location_dependencies())
        pending.extend(multiworld.get_entrance(name, player) for name in rule.entrance_dependencies())
    cover_unique_requirements(counts, groups, multiworld.state, player, inventory)
    return cover_inventory_routes(counts, alternatives, multiworld.state, player, inventory)


def protected_check_item_names(world):
    counts = protected_check_item_counts(world)
    return None if counts is None else frozenset(counts)


def mark_optional_balancing_items(multiworld):
    """Use AP's skip flag for optional items and copies above protected counts."""
    worlds = {world.player: world for world in multiworld.get_game_worlds("Kingdom Hearts III")}
    for item in getattr(multiworld, "_kh3_optional_balancing_items", ()):
        item.classification &= ~ItemClassification.skip_balancing
    marked = multiworld._kh3_optional_balancing_items = []
    multiworld._kh3_optional_balancing_item_counts = {}
    # Linked recipients use group ownership; keep AP's full existing policy.
    if any(world.options.item_links.value for world in multiworld.worlds.values()):
        return
    inventory = balancing_inventory(multiworld, worlds)
    relevant = {player: protected_check_item_counts(world, inventory[player]) for player, world in worlds.items()}
    kept = {player: Counter() for player in worlds}
    counts = {player: 0 for player in worlds}
    for item in multiworld.itempool:
        needed = relevant.get(item.player)
        if needed is None or not item.advancement:
            continue
        bound = needed.get(item.name, 0)
        required = None if bound is None else max(0, bound - multiworld.state.count(item.name, item.player))
        if not item.skip_in_prog_balancing and (required is None or kept[item.player][item.name] < required):
            kept[item.player][item.name] += 1
            continue
        if required is not None:
            if not item.skip_in_prog_balancing:
                marked.append(item)
            item.classification |= ItemClassification.skip_balancing
            counts[item.player] += 1
    multiworld._kh3_optional_balancing_item_counts = counts
