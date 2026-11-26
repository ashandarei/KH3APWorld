"""KH3 placement rules applied after Archipelago processes plando and item links."""
from collections import Counter

from BaseClasses import LocationProgressType
from Fill import FillError
from Options import OptionError


def validate_junk_capacity(multiworld, fill_locations, filler_count):
    excluded = [loc for loc in fill_locations if loc.progress_type == LocationProgressType.EXCLUDED]
    shortage = len(excluded) - filler_count
    if shortage <= 0:
        return

    settings = Counter(
        (loc.player, loc.junk_setting or "exclude_locations")
        for loc in excluded if loc.game == "Kingdom Hearts III"
    )
    if not settings:
        return
    contributors = ", ".join(
        f"{multiworld.get_player_name(player)} {setting} ({count})"
        for (player, setting), count in settings.most_common(5)
    )
    raise OptionError(
        f"Too many checks are restricted to junk. This room has {len(excluded)} unfilled junk-only "
        f"checks and {filler_count} filler or trap items available. Free up at least {shortage} checks "
        f"by switching Junk pools to Randomize or adjusting exclusion settings. "
        f"Largest KH3 contributors: {contributors}."
    )


def validate_fixed_junk_rewards(multiworld):
    """Reject incompatible fixed rewards before Archipelago fills junk checks."""
    for world in multiworld.get_game_worlds("Kingdom Hearts III"):
        for location in multiworld.get_filled_locations(world.player):
            if (location.address is not None and location.progress_type == LocationProgressType.EXCLUDED
                    and not location.item.excludable):
                raise FillError(
                    f"KH3 junk check {location} has a fixed reward that requires a normal check "
                    f"({location.item}). Change the junk, exclusion or fixed reward setting.",
                    multiworld=multiworld,
                )


def validate_solo_start(multiworld, fill_locations, progression_items):
    if multiworld.players != 1 or not progression_items:
        return

    state = multiworld.state.copy()
    locations = [loc for loc in fill_locations if loc.progress_type != LocationProgressType.EXCLUDED]
    remaining = progression_items
    while remaining:
        # Include reachable fixed rewards, plando and AP's early placements.
        state.sweep_for_advancements()
        if multiworld.has_beaten_game(state):
            return

        reachable = [loc for loc in locations if loc.can_reach(state)]
        # Let each reachable check supply every progression item it accepts.
        # A dead end even with this allowance is impossible in an actual fill.
        placeable = []
        blocked = []
        for item in remaining:
            if any(loc.can_fill(state, item, check_access=False) for loc in reachable):
                placeable.append(item)
            else:
                blocked.append(item)
        if not blocked:
            return
        if not placeable:
            break
        for item in placeable:
            state.collect(item, True)
        remaining = blocked

    raise OptionError(
        f"{multiworld.get_player_name(1)} cannot reach further progression from the starting "
        "checks with these settings. Set a starting world's pool to Randomize "
        "(for example, olympus_pool: randomize), remove exclusions from its opening checks, "
        "or add starting inventory that opens another check."
    )


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
    available = {player: multiworld.get_unfilled_locations(player) for player in players}
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
