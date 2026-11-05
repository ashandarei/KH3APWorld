"""Stable space identities shared by generation, tracker, and native bridge.

Boss mission commendations and their battle are one activity, with exactly
three checks. Constellations use their existing mission completion bits.
"""
from __future__ import annotations

import json

from .PackageResources import read_package_text

CATALOG = json.loads(read_package_text('data/space_catalog.json', module_file=__file__))
SPACE_LOCATION_ID_BASE = 0x4B4C3300
SPACE_ITEM_ID_BASE = 0x4B500000
REGIONS = ('Starlight Way', 'Misty Stream', 'The Eclipse')
WORLD_COMPLETIONS_BY_ZONE = dict(zip(REGIONS, (1, 2, 3)))
SPACE_ITEMS = {row['name']: row for row in CATALOG['items']}
SPACE_ITEM_IDS = {name: SPACE_ITEM_ID_BASE + row['id'] for name, row in SPACE_ITEMS.items()}
SPACE_TOKENS = {name: row['token'] for name, row in SPACE_ITEMS.items()}
SPACE_NAMES_BY_TOKEN = {token: name for name, token in SPACE_TOKENS.items()}
SPECIAL_WEAPONS = frozenset(name for name, row in SPACE_ITEMS.items() if row['category'] == 'Special Weapon')
SPACE_PARTS = frozenset(name for name, row in SPACE_ITEMS.items() if row['category'] == 'Part')
SPACE_COSMETICS = frozenset(name for name, row in SPACE_ITEMS.items() if row['category'] in ('Pattern', 'Sticker'))
SPACE_POOL_ITEMS = tuple(name for name in SPACE_ITEMS if name not in SPACE_PARTS)
ENDYMION = 'Gummi Blueprint: Endymion'
SHIP_BLUEPRINTS = frozenset(name for name, row in SPACE_ITEMS.items() if row['category'] == 'Blueprint')
SHIPS_BY_ZONE = {zone: frozenset(names) for zone, names in json.loads(
    read_package_text('data/space_ship_zones.json', module_file=__file__)).items()}
SHIP_ZONES = {name: zone for zone, names in SHIPS_BY_ZONE.items() for name in names}
ZONE_UNLOCK_BLUEPRINTS = frozenset(SHIP_ZONES)
if (tuple(SHIPS_BY_ZONE) != REGIONS or not ZONE_UNLOCK_BLUEPRINTS <= SHIP_BLUEPRINTS
        or tuple(map(len, SHIPS_BY_ZONE.values())) != (1, 1, 1)
        or sum(map(len, SHIPS_BY_ZONE.values())) != len(ZONE_UNLOCK_BLUEPRINTS)):
    raise ValueError('Each space zone must have one distinct ship blueprint that unlocks only that zone')
LANDING_WORLDS = ('Olympus', 'Twilight Town', 'Toy Box', 'Kingdom of Corona',
                  'Arendelle', 'Monstropolis', 'San Fransokyo', 'The Caribbean', 'Keyblade Graveyard')
BOSS_MISSIONS = {row['boss_mission']: row['id'] for row in CATALOG['battles'] if row['boss_mission'] is not None}
# Space nodes are already unlocked; waypoint/world discovery is not an AP activity.
# Keep native catalog entries and IDs intact for seeds generated before retirement.
RETIRED_EXPLORATION_MISSIONS = frozenset(range(1, 7))
GRINDY_MISSION_IDS = frozenset({20, 21, 22, 27, 33, 34, 35})
SUPERBOSS_MISSION_IDS = frozenset({13, 17})


def _locations():
    missions = {row['id']: row for row in CATALOG['missions']}
    for mid, row in missions.items():
        if mid in BOSS_MISSIONS or mid in RETIRED_EXPLORATION_MISSIONS:
            continue
        zone = None
        if 33 <= mid <= 35:
            # Builder commendations count the 15 physical fragments in one
            # specific zone, rather than ship designs made in the editor.
            zone = REGIONS[mid - 33]
        elif 36 <= mid <= 44:
            zone = REGIONS[(mid - 36)//3]
        yield dict(name='Space - ' + row['name'], source_id=f'mission_{mid:02}',
                   offset=mid, mission_id=mid, threshold=row['threshold'],
                   space_zone=zone)
    for row in CATALOG['battles']:
        _, area, _, battle = row['id'].split('_')
        boss = row['boss_mission']
        name = (missions[boss]['name'].removesuffix(' Slayer') if boss is not None
                else f'{REGIONS[int(area)-1]} - Battle {int(battle):02} - {row["name"]}')
        for reward in range(1, 4 if boss is not None else 2):
            yield dict(name=f'Space - {name}' + (f' - Reward {reward}' if boss is not None else ''),
                       source_id=f'{row["id"]}_{reward}', battle_id=row['id'], boss_mission=boss,
                       space_zone=REGIONS[int(area)-1],
                       offset=0x100 + int(area) * 0x100 + int(battle) * 4 + reward)
    for row in CATALOG['spheres']:
        sid = row['id']
        yield dict(name=f'Space - {REGIONS[sid//3]} - Treasure Sphere {sid%3+1}',
                   source_id=f'sphere_{sid:02}', offset=0x800 + sid, sphere_id=sid,
                   space_zone=REGIONS[sid//3])


SPACE_LOCATIONS = tuple(dict(row, world='Space', source_table='GummiSpace', type='space',
                             primary_item_name='', vanilla_item_name='') for row in _locations())
SPACE_LOCATION_IDS = {row['name']: SPACE_LOCATION_ID_BASE + row['offset'] for row in SPACE_LOCATIONS}
SPACE_BY_SOURCE = {row['source_id']: row for row in SPACE_LOCATIONS}
KNOWN_SPACE_SOURCES = frozenset(SPACE_BY_SOURCE) | {
    f'mission_{mid:02}' for mid in RETIRED_EXPLORATION_MISSIONS
}


def space_locations(enabled: bool, *, include_grindy_space_missions: bool = False,
                    include_space_superbosses: bool = False) -> list[dict]:
    if not enabled:
        return []
    return [dict(row) for row in SPACE_LOCATIONS
            if (include_grindy_space_missions or row.get('mission_id') not in GRINDY_MISSION_IDS)
            and (include_space_superbosses or row.get('boss_mission') not in SUPERBOSS_MISSION_IDS)
            and (row.get('mission_id') != 45 or
                 (include_grindy_space_missions and include_space_superbosses))]


def completed_space_keys(missions=(), battles=(), spheres=()) -> list[str]:
    """Expand authoritative native activity IDs; retries never add new IDs."""
    mission_ids, battle_ids, sphere_ids = set(missions), set(battles), set(spheres)
    battle_ids.update(BOSS_MISSIONS[mid] for mid in mission_ids if mid in BOSS_MISSIONS)
    return [f'GummiSpace:{row["source_id"]}' for row in SPACE_LOCATIONS
            if row.get('mission_id') in mission_ids or row.get('battle_id') in battle_ids
            or row.get('sphere_id') in sphere_ids]


def set_space_rules(world):
    from rule_builder.rules import And, CanReachLocation, CanReachRegion, Has, HasAny, Or, True_

    from .rule_helpers import CountRules
    if not world.options.include_space.value:
        return
    player = world.player
    # Only the three travel blueprints open space. The remaining ships and the
    # native starter Highwind are usable designs, without granting zone access.
    completions = world.space_world_completion_rules
    zone_rules = {
        zone: HasAny(*sorted(ships)) & CountRules(*completions,
            count=WORLD_COMPLETIONS_BY_ZONE[zone], label='completed main worlds')
        for zone, ships in SHIPS_BY_ZONE.items()
    }
    # Global commendations require an actually accessible zone too. A Galleon
    # with only one clear cannot substitute for a usable Bomb blueprint.
    world.set_rule(world.multiworld.get_entrance('To Space', player), Or(*zone_rules.values()))
    for zone, ships in SHIPS_BY_ZONE.items():
        world.set_rule(world.multiworld.get_entrance(f'To {zone}', player),
                       zone_rules[zone])
    monstropolis_final = next(row['name'] for row in world.active_locations
                             if (row['source_table'], row['source_id']) == ('VBonus', 'Vbonus_040'))

    # Trinity Fire and Decoy Duckies stay in the native Gummi shop. Their
    # stock unlocks at Twilight Town arrival and Monstropolis completion.
    special_weapons = (
        *(Has(item) for item in sorted(SPECIAL_WEAPONS)),
        CanReachRegion("Twilight Town"),
        CanReachLocation(monstropolis_final),
    )

    for row in world.active_locations:
        if row.get('type') != 'space':
            continue
        location = world.multiworld.get_location(row['name'], player)
        mid = row.get('mission_id')
        rule = True_()
        # Endymion meets Schwarzgeist's 200-speed entrance requirement. Other
        # ship equipment remains useful; ordinary encounters permit custom builds.
        if row.get('boss_mission') == 13 or mid == 45:
            rule &= Has(ENDYMION)
        if mid in range(23, 28) or mid == 45:
            needed = len(SPECIAL_WEAPONS) + 2 if mid == 45 else row['threshold']
            rule &= CountRules(*special_weapons, count=needed, label="available Gummi special weapons")
        if mid == 45:
            rule &= And(*(CanReachRegion(name) for name in LANDING_WORLDS))
            rule &= And(*(CanReachRegion(zone) for zone in REGIONS))
        if mid in range(28, 33):
            # Three physical treasure spheres per zone; total milestones need
            # enough independently unlocked zones to contain that many spheres.
            rule &= CountRules(*(CanReachRegion(zone) for zone in REGIONS),
                               count=(row['threshold'] + 2)//3, label='accessible space zones')
        world.set_rule(location, rule)
