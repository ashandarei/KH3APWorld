"""Seed-owned workshop checks. Catalog IDs are appended, never interleaved.

Native flags and recipe/weapon metadata were verified against EGS 1.0.0.0
(PE timestamp 0x60547D11). Ultima synthesis is managed by Ultima Weapon Mode;
platform-exclusive Pandora's Power is not an AP-owned Keyblade.
"""
import json

from .PackageResources import read_package_text

WORKSHOP_LOCATIONS = json.loads(read_package_text('data/workshop_locations.json', module_file=__file__))
WORKSHOP_OPTIONS = {
    'synthesis': 'synthesis_pool',
    'photo_mission': 'photo_missions_pool',
    'keyblade_upgrade': 'keyblade_upgrade_rewards_pool',
}
WORKSHOP_STATE_FILE = 'runtime_workshop.json'

# Request gates are removed by the probe. These gates describe repeatable access
# to the actual subjects, independently of their former request-unlock world.
PHOTO_MISSION_COMPLETION_WORLDS = {
    'IM_0': ('Olympus',),           # Secluded Forge fire
    'IM_1': ('Arendelle',),         # Ice Palace
    'IM_2': ('Olympus',),           # Zeus
    'IM_3': ('Monstropolis',),      # Demon Tower: also requires Battlegate 8 access
    'IM_4': ('The Caribbean',),     # Port Royal waterfall
    'IM_5': ('San Fransokyo',),     # Daytime fish-shaped windsocks
    'IM_6': ('Olympus', 'Toy Box', 'Kingdom of Corona', 'Monstropolis',
             'Arendelle', 'The Caribbean', 'San Fransokyo'), # All native teammates
    'IM_7': ('Olympus',),           # Flame Core
    'IM_8': ('Kingdom of Corona',), # Water Core
    'IM_9': ('Kingdom of Corona',), # Chief Puff
    'IM_10': ('Arendelle',),        # Olaf
    'IM_11': ('Monstropolis',),     # CDA agent
    'IM_12': ('Olympus',),          # Hercules statue
    'IM_13': ('Toy Box',),          # Beasts & Bugs display
    'IM_14': ('Toy Box',),          # Green Cactuar
    'IM_15': ('Kingdom of Corona',),# Rapunzel's tower
    'IM_16': ('100 Acre Wood',),    # Scarecrow
    'IM_17': ('Kingdom of Corona',),# Festival decorations
    'IM_18': ('San Fransokyo',),    # Evening star
    'IM_19': ('Twilight Town',),    # Tram
}
PHOTO_MISSION_ADDITIONAL_SOURCES = {'IM_3': (('Event', 'EVENT_REPORT_008b'),)}
PHOTO_WORLD_COMPLETION_EXTRA_SOURCES = {'100 Acre Wood': (('Event', 'EVENT_KEYBLADE_006'),)}


def append_workshop_locations(data: dict) -> None:
    existing = {(r['source_table'], r['source_id']) for r in data['locations']}
    data['locations'].extend(dict(r) for r in WORKSHOP_LOCATIONS
                             if (r['source_table'], r['source_id']) not in existing)


def synthesis_is_junk(location: dict, options) -> bool:
    if location.get('type') != 'synthesis':
        return False
    option = getattr(options, 'moogle_synthesis_limit', 15)
    return location['synthesis_order'] > int(getattr(option, 'value', option))
