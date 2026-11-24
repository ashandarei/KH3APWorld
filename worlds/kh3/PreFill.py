"""KH3 placement rules applied after Archipelago processes plando and item links."""
from collections import Counter, defaultdict

from BaseClasses import LocationProgressType
from Fill import FillError


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
