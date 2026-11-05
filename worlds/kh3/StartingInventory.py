"""KH3 slot-count overflow; Archipelago owns fill and progression balancing."""
import logging
from collections import Counter, defaultdict

from BaseClasses import LocationProgressType
from Fill import FillError


def register_post_fill_classifications(multiworld):
    """Keep declared foreign item promotions out of incompatible KH3 checks.
    """
    from copy import copy
    from worlds.generic.Rules import add_item_rule

    promoted = {}
    for world in multiworld.worlds.values():
        for item, classification in getattr(world, 'classifications_to_override_post_fill', ()):
            shadow = copy(item)
            shadow.classification |= classification
            if shadow.classification != item.classification:
                promoted[id(item)] = shadow
    if not promoted:
        return
    kh3_players = {world.player for world in multiworld.get_game_worlds('Kingdom Hearts III')}
    for location in multiworld.get_locations():
        if location.player not in kh3_players or location.locked:
            continue
        original = location.item_rule
        excluded = location.progress_type == LocationProgressType.EXCLUDED
        add_item_rule(location, lambda item, original=original, promoted=promoted, excluded=excluded:
                      id(item) not in promoted or (original(promoted[id(item)])
                      and (not excluded or promoted[id(item)].excludable)))


def precollect_overflow(multiworld):
    """Budget each KH3 pool against its own non-junk, unfilled network checks.

    Grant excess useful items first, then excess progression, replacing every
    granted pool item with filler. This is a deterministic count policy, not
    an item-rule matching solver. AP still validates locality and reachability.
    """
    if hasattr(multiworld, '_kh3_starting_overflow_report'):
        return multiworld._kh3_starting_overflow_report
    worlds = {world.player: world for world in multiworld.get_game_worlds('Kingdom Hearts III')}
    capacity = Counter()
    progression_capacity = Counter()
    level_capacity = Counter()
    level_keys = {(player, row['name']) for player, world in worlds.items()
                  for row in getattr(world, 'active_locations', ()) if row.get('source_table') == 'LevelUp'}
    fixed_grants = defaultdict(list)
    for location in multiworld.get_locations():
        if location.player not in worlds or location.address is None:
            continue
        if location.progress_type == LocationProgressType.EXCLUDED:
            item = location.item
            if item is not None and (item.advancement or item.useful):
                if item.player != location.player:
                    raise FillError(f'KH3 junk check {location.name} contains a fixed foreign '
                                    'non-filler item; adjust the planned placement.', multiworld=multiworld)
                replacement = worlds[location.player].create_filler()
                multiworld.push_precollected(item)
                item.location = None
                location.item = None
                location.place_locked_item(replacement)
                fixed_grants[location.player].append(item.name)
        elif location.item is None:
            capacity[location.player] += 1
            if getattr(location, 'progression_allowed', True):
                progression_capacity[location.player] += 1
            if (location.player, location.name) in level_keys:
                level_capacity[location.player] += 1

    progression = defaultdict(list)
    useful = defaultdict(list)
    early_filler = defaultdict(Counter)
    for index, item in enumerate(multiworld.itempool):
        if item.player in worlds and item.location is None:
            if item.advancement:
                progression[item.player].append(index)
            elif item.useful:
                useful[item.player].append(index)
            else:
                early_filler[item.player][item.name] += 1
    report = {}
    for player, world in worlds.items():
        prog = progression[player]
        utility = useful[player]
        early_counts = Counter(multiworld.early_items[player]) + Counter(multiworld.local_early_items[player])
        reserved = sum((early_filler[player] & early_counts).values())
        # AP places early filler before progression. Its shop funding pouch
        # can occupy a normal check even when every other junk check is gated.
        budget = max(0, capacity[player] - reserved)
        forbidden = getattr(world, 'level_up_forbidden_item_names', ())
        restricted = [index for index in utility + prog if multiworld.itempool[index].name in forbidden]
        restricted_excess = max(0, len(restricted) - max(0, budget - level_capacity[player]))
        selected = restricted[:restricted_excess]
        selected_set = set(selected)
        # Optional adversaries/0.2 and escape chests can hold useful rewards,
        # but cannot provide progression capacity. Level restrictions further
        # constrain portal/goal progression within that smaller capacity.
        prog_budget = max(0, progression_capacity[player] - reserved)
        restricted_prog = [index for index in prog if index not in selected_set
                           and multiworld.itempool[index].name in forbidden]
        restricted_prog_excess = max(0, len(restricted_prog) - max(0, prog_budget - level_capacity[player]))
        selected += restricted_prog[:restricted_prog_excess]
        selected_set = set(selected)
        remaining_prog = [index for index in prog if index not in selected_set]
        prog_excess = max(0, len(remaining_prog) - prog_budget)
        selected += remaining_prog[:prog_excess]
        selected_set = set(selected)
        remaining = [index for index in utility + prog if index not in selected_set]
        excess = max(0, len(remaining) - budget)
        selected += remaining[:excess]
        selected.sort(key=lambda index: (multiworld.itempool[index].advancement, index))
        granted = Counter()
        progression_granted = useful_granted = 0
        for index in selected:
            item = multiworld.itempool[index]
            multiworld.push_precollected(item)
            granted[item.name] += 1
            progression_granted += int(item.advancement)
            useful_granted += int(item.useful and not item.advancement)
            multiworld.itempool[index] = world.create_filler()
        report[player] = {'normal_slots': capacity[player], 'pool_progression_before': len(prog),
                          'progression_slots': progression_capacity[player],
                          'pool_useful_before': len(utility), 'pool_overflow_granted': len(selected),
                          'progression_granted': progression_granted, 'useful_granted': useful_granted,
                          'fixed_junk_rewards_granted': fixed_grants[player],
                          'granted_pool_items': dict(granted)}
        if reserved:
            report[player]['early_filler_reserved'] = reserved
        if restricted_excess:
            report[player]['level_rule_overflow_granted'] = restricted_excess
        if restricted_prog_excess or prog_excess:
            report[player]['progression_rule_overflow_granted'] = restricted_prog_excess + prog_excess
    multiworld._kh3_starting_overflow_report = report
    logging.info('KH3 starting inventory: granted %d excess pool items and %d fixed junk rewards.',
                 sum(row['pool_overflow_granted'] for row in report.values()),
                 sum(len(row['fixed_junk_rewards_granted']) for row in report.values()))
    return report


def precollect_restrictive_starts(multiworld):
    """Bootstrap an opening with no legal progression check; AP owns the fill.

    Junk opening worlds can leave every normal check gated by the pool. Find
    one reachable check with full own inventory, then remove unnecessary pool
    items from that opening route. Grant only the remaining prerequisites.
    """
    from .Data import WORLD_DIFFICULTY_TIER_BY_REGION

    worlds = {world.player: world for world in multiworld.get_game_worlds('Kingdom Hearts III')}
    candidates = defaultdict(list)
    for index, item in enumerate(multiworld.itempool):
        if item.player in worlds and item.location is None and item.advancement:
            candidates[item.player].append((index, item))
    if not candidates:
        return
    normal = [loc for loc in multiworld.get_unfilled_locations()
              if loc.address is not None and loc.progress_type != LocationProgressType.EXCLUDED]
    ground_keys = {(player, row['name']) for player, world in worlds.items()
                   for row in getattr(world, 'active_locations', ())
                   if row.get('type') in ('treasure', 'vbonus', 'vbonus_extra')}
    ground = [loc for loc in normal if loc.player not in worlds or (loc.player, loc.name) in ground_keys]
    base = multiworld.state.copy()
    base.sweep_for_advancements()
    initial_reachability = {}

    def has_initial_check(items, needed, locations=normal):
        for loc in locations:
            if not any(loc.can_fill(base, item, check_access=False) for _, item in items):
                continue
            if loc not in initial_reachability:
                initial_reachability[loc] = loc.can_reach(base)
            if initial_reachability[loc]:
                needed -= 1
                if needed <= 0:
                    return True
        return False

    for player, items in candidates.items():
        world = worlds[player]
        needed = 1 + multiworld._kh3_starting_overflow_report[player].get('early_filler_reserved', 0)
        forbidden = getattr(world, 'level_up_forbidden_item_names', ())
        restricted = [pair for pair in items if pair[1].name in forbidden]
        # A few open minigame rewards are a fragile opening for portal items.
        # Prefer opening a normal chest/encounter route when one exists.
        openings = ground if restricted and any(loc.can_fill(base, item, check_access=False)
                    for loc in ground for _, item in restricted) else normal
        # A level check can accept magic while rejecting every portal/goal
        # item. Test that constrained group before the general opening.
        opening_items = restricted if restricted and not has_initial_check(restricted, needed, openings) else items
        if has_initial_check(opening_items, needed, openings):
            continue
        rows = {row['name']: row for row in world.active_locations}
        targets = sorted((loc for loc in openings if loc.player == player), key=lambda loc: (
            WORLD_DIFFICULTY_TIER_BY_REGION.get(rows.get(loc.name, {}).get('world'), 99),
            rows.get(loc.name, {}).get('type') not in ('treasure', 'vbonus', 'vbonus_extra'), loc.name))

        def state_with(selected):
            state = base.copy()
            for _, item in selected:
                state.collect(item, True)
            state.sweep_for_advancements()
            return state

        full = state_with(items)
        target = next((loc for loc in targets if not loc.can_reach(base) and loc.can_reach(full)
                       and any(loc.can_fill(full, item, check_access=False) for _, item in opening_items)), None)
        if target is None:
            continue
        selected = items.copy()
        for candidate in items:
            trial = [pair for pair in selected if pair[0] != candidate[0]]
            if target.can_reach(state_with(trial)):
                selected = trial
        if not selected:
            continue
        report = multiworld._kh3_starting_overflow_report[player]
        for index, item in selected:
            multiworld.push_precollected(item)
            multiworld.itempool[index] = world.create_filler()
            report['pool_overflow_granted'] += 1
            report['progression_granted'] += 1
            report['granted_pool_items'][item.name] = report['granted_pool_items'].get(item.name, 0) + 1
        report['restrictive_start_granted'] = [item.name for _, item in selected]
        base = multiworld.state.copy()
        base.sweep_for_advancements()
        initial_reachability.clear()


def reserve_all_local_locations(multiworld):
    """Prevent foreign early imports only when an all-local pool fills every slot."""
    players = {
        player: world.options.local_items.value
        for player, world in multiworld.worlds.items()
        if world.game == 'Kingdom Hearts III'
        and world.item_names <= world.options.local_items.value
    }
    if not players:
        return {}
    required = Counter(item.player for item in multiworld.itempool
                       if item.location is None and item.player in players
                       and item.name in players[item.player])
    available = defaultdict(list)
    for location in multiworld.get_unfilled_locations():
        if location.player in players:
            available[location.player].append(location)
    for player in players:
        if required[player] > len(available[player]):
            raise FillError(
                f'KH3 all-local fill for {multiworld.get_player_name(player)} needs '
                f'{required[player]} locations for local items, but only '
                f'{len(available[player])} unfilled locations remain. Check item links '
                'and planned placements for conflicting imports.', multiworld=multiworld)
    reserved = {}
    rule_cache = {}
    for player in players:
        if not required[player] or required[player] != len(available[player]):
            continue
        for location in available[player]:
            old_rule = location.item_rule
            key = player, id(old_rule)
            if key not in rule_cache:
                rule_cache[key] = lambda item, p=player, rule=old_rule: item.player == p and rule(item)
            location.item_rule = rule_cache[key]
        reserved[player] = required[player]
    return reserved
