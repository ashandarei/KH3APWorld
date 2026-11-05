from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import json
import re
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from BaseClasses import ItemClassification

from ..Data import local_seeded_reward_requires_bridge_delivery, resolve_item_runtime_token
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    bridge_only_item_runtime_token,
    export_randomized_options_from_bridge_data,
)
from ..Items import classify_item, item_name_to_id
from ..Locations import location_name_to_id
from ..Space import (
    ENDYMION,
    REGIONS,
    SHIP_BLUEPRINTS,
    SHIP_ZONES,
    SHIPS_BY_ZONE,
    SPACE_COSMETICS,
    SPACE_ITEM_IDS,
    SPACE_ITEMS,
    SPACE_LOCATION_IDS,
    SPACE_LOCATIONS,
    SPACE_PARTS,
    SPACE_TOKENS,
    ZONE_UNLOCK_BLUEPRINTS,
    completed_space_keys,
)
from . import KH3TestBase


class TestSpaceProtocol(TestCase):
    def test_native_ship_whitelist_matches_ap_catalog(self):
        header = native_project_root() / 'KH3APProbeCpp/kh3ap/features/world/space/space_ship_catalog.h'
        if not header.is_file():
            self.skipTest('Native source contract is checked from the repository')
        pairs = re.findall(r'\{L("[^"]+"), L("[^"]+"), (-?\d)\}', header.read_text(encoding='utf-8'))
        native = {json.loads(name): (json.loads(token), REGIONS[int(zone)] if int(zone) >= 0 else None)
                  for name, token, zone in pairs}
        self.assertEqual(native, {name: (SPACE_TOKENS[name], SHIP_ZONES.get(name)) for name in SHIP_BLUEPRINTS})

    def test_each_zone_has_its_one_selected_travel_blueprint(self):
        self.assertEqual(SHIPS_BY_ZONE, {
            'Starlight Way': frozenset({'Gummi Blueprint: Bomb'}),
            'Misty Stream': frozenset({'Gummi Blueprint: Galleon'}),
            'The Eclipse': frozenset({'Gummi Blueprint: Submarine'}),
        })
        self.assertEqual(len(ZONE_UNLOCK_BLUEPRINTS), 3)
        self.assertEqual(set(SHIP_ZONES), ZONE_UNLOCK_BLUEPRINTS)
        self.assertEqual(len(SHIP_BLUEPRINTS - ZONE_UNLOCK_BLUEPRINTS), 34)
        self.assertNotIn('Gummi Blueprint: Highwind LV 1', SHIP_BLUEPRINTS)
        self.assertNotIn('Gummi Blueprint: Highwind LV 1', SHIP_ZONES)
        self.assertNotIn('Gummi Blueprint: Highwind LV 1', item_name_to_id)

    def test_ids_do_not_collide_with_existing_items_or_locations(self):
        self.assertEqual(len(set(location_name_to_id.values())), len(location_name_to_id))
        self.assertEqual(len(set(item_name_to_id.values())), len(item_name_to_id))
        self.assertEqual(len(SPACE_LOCATION_IDS), 93)
        self.assertEqual(len(SPACE_ITEM_IDS), 127)

    def test_retired_mission_cosmetics_have_native_tokens_and_filler_classification(self):
        expected = {
            f'Gummi Pattern: {name}/P-{variant:02}': f'GUMIPATTERN_ITEM{start + variant - 1:03}'
            for name, start in (('Soccer Ball', 18), ('Crowns', 34), ('Geometric', 78), ('Dazzle', 86))
            for variant in range(1, 5)
        }
        expected.update({'Gummi Sticker: Crown/S': 'GUMISTICKER_ITEM027',
                         'Gummi Sticker: Kingdom Hearts/S': 'GUMISTICKER_ITEM028'})
        self.assertEqual(SPACE_COSMETICS, set(expected))
        for name, token in expected.items():
            with self.subTest(name=name):
                self.assertEqual(SPACE_TOKENS[name], token)
                self.assertEqual(classify_item(name), ItemClassification.filler)

    def test_boss_clear_and_commendation_are_the_same_three_checks(self):
        expected = {f'GummiSpace:gm_02_Battle_17_{i}' for i in (1, 2, 3)}
        self.assertEqual(set(completed_space_keys(missions=[13, 13])), expected)
        self.assertEqual(set(completed_space_keys(missions=[13], battles=['gm_02_Battle_17'])), expected)
        self.assertEqual(completed_space_keys(battles=['gm_01_Battle_01']), ['GummiSpace:gm_01_Battle_01_1'])
        self.assertEqual(completed_space_keys(spheres=[0, 0]), ['GummiSpace:sphere_00'])

    def test_exploration_missions_are_retired_without_shifting_other_ids(self):
        self.assertEqual(completed_space_keys(missions=range(1, 7)), [])
        for name in ('Traveler', 'Master Traveler', 'Ultimate Traveler',
                     'World Seeker', 'Master World Seeker', 'Ultimate World Seeker'):
            self.assertNotIn('Space - ' + name, location_name_to_id)
        self.assertEqual(SPACE_LOCATION_IDS['Space - Blast Off'], 0x4B4C3300)
        self.assertEqual(SPACE_LOCATION_IDS['Space - Heartless Hunter'], 0x4B4C3312)
        self.assertEqual(SPACE_LOCATION_IDS['Space - Gummi Ship Completionist'], 0x4B4C332D)


    def test_every_gummi_item_resolves_to_a_native_bridge_grant(self):
        for name, token in SPACE_TOKENS.items():
            with self.subTest(name=name):
                self.assertEqual(resolve_item_runtime_token(name), token)
                self.assertEqual(bridge_only_item_runtime_token(name), token)
                self.assertTrue(local_seeded_reward_requires_bridge_delivery('TreasureTT:TT_TEST', 'treasure', token))


    def test_export_gummi_item_in_chest_and_space_check(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            default = root / 'default.json'
            extracted = root / 'extracted.json'
            default.write_text(json.dumps({'Event': {k: {'RandomizedItem': 'ITEM_POTION\0'} for k in ALWAYS_ON_EVENT_IDS},
                                           'TreasureTT': {'TT_TEST': {'Treasure': 'ITEM_POTION\0'}}}))
            extracted.write_text(json.dumps({'items': [], 'locations': []}))
            base = dict(assigned_item_game='Kingdom Hearts III', assigned_item_name=ENDYMION,
                        assigned_item_player=1, assigned_item_player_name='KH3')
            rows = [dict(base, ap_location_id=123, ap_location_name='Test chest', type='treasure',
                         bridge_key='TreasureTT:TT_TEST', source_table='TreasureTT', source_id='TT_TEST'),
                    dict(base, ap_location_id=SPACE_LOCATION_IDS[SPACE_LOCATIONS[0]['name']],
                         ap_location_name=SPACE_LOCATIONS[0]['name'], type='space',
                         bridge_key='GummiSpace:mission_00', source_table='GummiSpace', source_id='mission_00')]
            result = export_randomized_options_from_bridge_data(default_kh3=default, default_kh3_overrides=None,
                extracted_data_path=extracted, bridge_locations=rows, slot=1, slot_name='KH3', archive_name='test.zip')
            self.assertNotIn('GummiSpace', result.randomized_options)
            self.assertEqual(result.randomized_options['TreasureTT']['TT_TEST']['Treasure'], 'AP_ITEM_123\0')
            for row in result.placement_summary['locations']:
                self.assertEqual(row['runtime_token'], SPACE_TOKENS[ENDYMION])
                self.assertTrue(row['bridge_delivery_required'])


class TestSpaceEnabled(KH3TestBase):
    options = {'include_space': 1, 'include_grindy_space_missions': 1, 'include_space_superbosses': 1}

    def test_space_requires_one_of_the_three_travel_blueprints(self):
        self.collect_all_but(SHIP_BLUEPRINTS)
        ordinary = self.get_location_by_source('GummiSpace', 'mission_00')
        self.assertFalse(ordinary.can_reach(self.multiworld.state))
        self.collect(self.world.create_item('Gummi Part: Thruster/G'))
        self.assertFalse(ordinary.can_reach(self.multiworld.state))
        for name in sorted(SHIP_BLUEPRINTS):
            with self.subTest(blueprint=name):
                item = self.world.create_item(name)
                self.assertEqual(item.advancement, name in ZONE_UNLOCK_BLUEPRINTS or name == ENDYMION)
                self.collect(item)
                self.assertEqual(ordinary.can_reach(self.multiworld.state), name in ZONE_UNLOCK_BLUEPRINTS)
                self.remove(item)
                self.assertFalse(ordinary.can_reach(self.multiworld.state))

    def test_no_starting_ap_ship_and_highwind_is_excluded(self):
        self.assertFalse(any(item.name in SHIP_BLUEPRINTS
                             for item in self.multiworld.precollected_items[self.player]))
        self.assertEqual(sum(item.name == 'Gummi Blueprint: Highwind LV 1'
                             for item in self.multiworld.itempool), 0)
        self.assertFalse(any(item.name == 'Gummi Blueprint: Highwind LV 1'
                             for item in self.multiworld.precollected_items[self.player]))

    def test_each_ship_opens_only_its_assigned_zone(self):
        self.collect_all_but(SHIP_BLUEPRINTS)
        sources = [('GummiSpace', f'sphere_{i*3:02}') for i in range(3)]
        for name in sorted(SHIP_BLUEPRINTS):
            item = self.world.create_item(name)
            self.collect(item)
            for zone, source in zip(REGIONS, sources):
                with self.subTest(blueprint=name, zone=zone):
                    expected = zone == SHIP_ZONES.get(name)
                    self.assertEqual(self.multiworld.get_region(zone, self.player).can_reach(self.multiworld.state), expected)
                    self.assertEqual(self.get_location_by_source(*source).can_reach(self.multiworld.state), expected)
                    constellation = 36 + 3*REGIONS.index(zone)
                    self.assertEqual(self.get_location_by_source('GummiSpace', f'mission_{constellation:02}')
                                     .can_reach(self.multiworld.state), expected)
                    builder = 33 + REGIONS.index(zone)
                    self.assertEqual(self.get_location_by_source('GummiSpace', f'mission_{builder:02}')
                                     .can_reach(self.multiworld.state), expected)
            self.remove(item)

    def test_all_other_ship_rewards_together_do_not_unlock_space(self):
        for name in sorted(SHIP_BLUEPRINTS - ZONE_UNLOCK_BLUEPRINTS):
            item = self.world.create_item(name)
            self.assertEqual(item.classification, ItemClassification.progression if name == ENDYMION
                             else ItemClassification.useful)
            self.collect(item)
        self.assertFalse(self.multiworld.get_region('Space', self.player).can_reach(self.multiworld.state))
        for zone in REGIONS:
            self.assertFalse(self.multiworld.get_region(zone, self.player).can_reach(self.multiworld.state))

    def test_misty_stream_ship_cannot_reach_other_builder_rewards(self):
        self.collect_all_but(SHIP_BLUEPRINTS)
        self.collect_by_name('Gummi Blueprint: Galleon')
        for name, zone, reachable in (
                ('Pro Builder', 'Starlight Way', False),
                ('Elite Builder', 'Misty Stream', True),
                ('Master Builder', 'The Eclipse', False)):
            with self.subTest(mission=name):
                location = self.multiworld.get_location('Space - ' + name, self.player)
                self.assertEqual(self.get_location_base_region(location).name, zone)
                self.assertEqual(location.can_reach(self.multiworld.state), reachable)

    def test_physical_space_checks_live_in_their_native_zone(self):
        for row in self.world.active_locations:
            if row['type'] == 'space':
                with self.subTest(location=row['name']):
                    location = self.multiworld.get_location(row['name'], self.player)
                    self.assertEqual(self.get_location_base_region(location).name, row.get('space_zone') or 'Space')

    def test_treasure_milestones_need_enough_open_zones(self):
        self.collect_all_but(SHIP_BLUEPRINTS)
        for count in range(4):
            for mid, zones_needed in ((28, 1), (29, 1), (30, 2), (31, 3), (32, 3)):
                with self.subTest(zones=count, mission=mid):
                    self.assertEqual(self.get_location_by_source('GummiSpace', f'mission_{mid:02}')
                                     .can_reach(self.multiworld.state), count >= zones_needed)
            if count < 3:
                self.collect(self.world.create_item(sorted(SHIPS_BY_ZONE[REGIONS[count]])[0]))

    def test_parts_weapons_and_cosmetics_keep_normal_placement(self):
        chest = next(self.multiworld.get_location(row['name'], self.player)
                     for row in self.world.active_locations if row['type'] == 'treasure')
        space = self.get_location_by_source('GummiSpace', 'sphere_00')
        for name in SPACE_ITEMS.keys() - SHIP_BLUEPRINTS:
            with self.subTest(item=name):
                item = self.world.create_item(name)
                self.assertTrue(chest.item_rule(item))
                self.assertTrue(space.item_rule(item))
                self.collect(item)
        self.assertFalse(any(self.multiworld.get_region(zone, self.player).can_reach(self.multiworld.state)
                             for zone in REGIONS))

    def test_ship_blueprints_accept_normal_levels_chests_and_space(self):
        for source in ('level_up', 'treasure', 'space'):
            location = next(self.multiworld.get_location(row['name'], self.player)
                            for row in self.world.active_locations if row['type'] == source)
            for name in sorted(SHIP_BLUEPRINTS):
                with self.subTest(source=source, blueprint=name):
                    self.assertTrue(location.item_rule(self.world.create_item(name)))

    def test_space_pool_and_bridge(self):
        rows = [r for r in self.world.active_locations if r['type'] == 'space']
        self.assertEqual(len(rows), 93)
        self.assertFalse(any(r.get('mission_id') in range(1, 7) for r in rows))
        counts = Counter(i.name for i in self.multiworld.itempool)
        counts.update(loc.item.name for loc in self.multiworld.get_filled_locations()
                      if loc.item.name in SPACE_ITEMS)
        self.assertEqual({name: counts[name] for name in SPACE_ITEMS},
                         {name: 0 if name in SPACE_PARTS else 1 for name in SPACE_ITEMS})
        self.assertFalse(any(i.name in SPACE_PARTS
                             for i in self.multiworld.precollected_items[self.player]))
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
        self.assertEqual({i.name for i in self.multiworld.itempool if i.name in SHIP_BLUEPRINTS and i.advancement},
                         ZONE_UNLOCK_BLUEPRINTS | {ENDYMION})
        slot = self.world.build_runtime_slot_data()
        self.assertEqual(slot['include_space'], 1)
        self.assertTrue(all(row['bridge_delivery_required'] for row in slot['bridge']['locations'] if row['type'] == 'space'))

    def test_schwarzgeist_cannot_hold_its_entrance_blueprint(self):
        self.collect_all_but(SHIP_BLUEPRINTS)
        location = self.get_location_by_source('GummiSpace', 'gm_02_Battle_17_1')
        self.assertFalse(location.can_reach(self.multiworld.state))
        self.collect_by_name('Gummi Blueprint: Galleon')
        self.assertFalse(location.can_reach(self.multiworld.state))
        self.collect_by_name(ENDYMION)
        self.assertTrue(location.can_reach(self.multiworld.state))

    def test_space_requires_one_two_three_distinct_completed_worlds(self):
        from ..ColiseumLogic import COLISEUM_WORLD_COMPLETION_SOURCES
        state = self.multiworld.get_all_state(False)
        gates = [self.multiworld.get_entrance('To ' + name, self.player)
                 for name in COLISEUM_WORLD_COMPLETION_SOURCES]
        originals = [gate.access_rule for gate in gates]
        try:
            for gate in gates:
                gate.access_rule = lambda state: False
            for count in range(4):
                self.reset_reachability(state)
                for index, zone in enumerate(REGIONS):
                    self.assertEqual(self.multiworld.get_region(zone, self.player).can_reach(state),
                                     count >= index + 1, (count, zone))
                    self.assertEqual(self.get_location_by_source('GummiSpace', f'sphere_{index*3:02}')
                                     .can_reach(state), count >= index + 1, (count, zone))
                self.assertEqual(self.get_location_by_source('GummiSpace', 'mission_00').can_reach(state),
                                 count >= 1)
                for index, zone in enumerate(REGIONS):
                    only_zone = state.copy()
                    for ship in ZONE_UNLOCK_BLUEPRINTS - SHIPS_BY_ZONE[zone]:
                        while only_zone.has(ship, self.player):
                            only_zone.remove(self.world.create_item(ship))
                    self.reset_reachability(only_zone)
                    self.assertEqual(self.get_location_by_source('GummiSpace', 'mission_00')
                                     .can_reach(only_zone), count >= index + 1, (count, zone))
                if count < 3:
                    gates[count].access_rule = lambda state: True
        finally:
            for gate, rule in zip(gates, originals):
                gate.access_rule = rule

    def test_completionist_cannot_substitute_non_space_world_keyblades(self):
        self.collect_all_but("Hero's Origin")
        # May be the randomized precollected portal key, outside itempool.
        while self.multiworld.state.has("Hero's Origin", self.player):
            self.remove(self.world.create_item("Hero's Origin"))
        location = self.get_location_by_source('GummiSpace', 'mission_45')
        self.assertFalse(location.can_reach(self.multiworld.state))
        self.collect(self.world.create_item("Hero's Origin"))
        self.assertTrue(location.can_reach(self.multiworld.state))

    def test_completionist_requires_every_space_zone(self):
        location = self.get_location_by_source('GummiSpace', 'mission_45')
        full_state = self.multiworld.get_all_state(False)
        self.assertTrue(location.can_reach(full_state))
        for zone, ships in SHIPS_BY_ZONE.items():
            with self.subTest(zone=zone):
                state = full_state.copy()
                for name in ships:
                    while state.has(name, self.player):
                        state.remove(self.world.create_item(name))
                self.assertFalse(self.multiworld.get_region(zone, self.player).can_reach(state))
                self.assertFalse(location.can_reach(state))

    def test_special_weapon_authority_requires_native_shop_unlocks(self):
        self.collect_all_but('Happy Gear')
        while self.multiworld.state.has('Happy Gear', self.player):
            self.remove(self.world.create_item('Happy Gear'))
        location = self.get_location_by_source('GummiSpace', 'mission_27')
        self.assertFalse(location.can_reach(self.multiworld.state))
        self.collect(self.world.create_item('Happy Gear'))
        self.assertTrue(location.can_reach(self.multiworld.state))


class TestSpaceOptionFilters(TestCase):
    def test_space_defaults_exclude_both_optional_groups(self):
        from test.general import setup_multiworld

        from .. import KH3World

        world = setup_multiworld(KH3World, seed=10042026, options={'include_space': 1}).worlds[1]
        self.assertFalse(world.options.include_grindy_space_missions.value)
        self.assertFalse(world.options.include_space_superbosses.value)
        self.assertEqual(world.create_item(ENDYMION).classification, ItemClassification.useful)
        self.assertEqual(sum(row['type'] == 'space' for row in world.active_locations), 79)

    def test_three_travel_blueprints_generate_reachable_seeds_with_each_optional_check_pool(self):
        from Fill import distribute_items_restrictive
        from test.general import setup_multiworld
        from worlds.AutoWorld import call_all

        from .. import KH3World

        for grindy, superbosses in ((0, 0), (1, 0), (0, 1), (1, 1)):
            for seed in (2026100508, 2026100519):
                with self.subTest(grindy=grindy, superbosses=superbosses, seed=seed):
                    multiworld = setup_multiworld(KH3World, seed=seed, options={
                        'include_space': 1,
                        'include_grindy_space_missions': grindy,
                        'include_space_superbosses': superbosses,
                    })
                    world = multiworld.worlds[1]
                    self.assertEqual(world.create_item(ENDYMION).advancement, bool(superbosses))
                    distribute_items_restrictive(multiworld)
                    call_all(multiworld, 'post_fill')
                    call_all(multiworld, 'finalize_multiworld')
                    self.assertFalse(multiworld.get_unfilled_locations())
                    for sphere in multiworld.get_spheres():
                        self.assertTrue(sphere, 'Every space and non-space check must remain reachable')
                    self.assertTrue(multiworld.can_beat_game())


class TestShipBlueprintMultiworldPlacement(TestCase):
    def test_remote_blueprints_use_normal_checks_and_preserve_foreign_rules(self):
        from BaseClasses import Item, Location, Region
        from test.general import TestWorld, gen_steps, setup_multiworld
        from worlds.AutoWorld import call_all

        from .. import KH3World

        multiworld = setup_multiworld([KH3World, KH3World, TestWorld], steps=(), seed=10261001,
                                     options=[{'include_space': 1}, {'include_space': 1}, {}])
        _, linked_ships = multiworld.add_group('Linked ships', KH3World.game, {1, 2})
        foreign_menu = Region('Menu', 3, multiworld)
        multiworld.regions.append(foreign_menu)
        foreign = Location(3, 'Foreign reward', 1, foreign_menu)
        foreign.item_rule = lambda item: item.name != 'Potion'
        foreign_menu.locations.append(foreign)
        for step in gen_steps:
            call_all(multiworld, step)
        source_world = multiworld.worlds[1]
        remote_world = multiworld.worlds[2]
        bonuses = [multiworld.get_location(row['name'], 2) for row in remote_world.active_locations
                   if (row['source_table'], row['source_id']) == ('VBonus', 'Vbonus_082')
                   or row.get('trigger_bridge_key') == 'VBonus:Vbonus_082']
        self.assertEqual(len(bonuses), 3)
        chest = next(multiworld.get_location(row['name'], 2) for row in remote_world.active_locations
                     if row['type'] == 'treasure')
        space = next(multiworld.get_location(row['name'], 2) for row in remote_world.active_locations
                     if row['type'] == 'space')
        level_up = next(multiworld.get_location(row['name'], 2) for row in remote_world.active_locations
                        if row['type'] == 'level_up')
        for name in SHIP_BLUEPRINTS:
            ship = source_world.create_item(name)
            with self.subTest(blueprint=name):
                self.assertTrue(foreign.item_rule(ship))
                self.assertTrue(chest.item_rule(ship))
                self.assertTrue(space.item_rule(ship))
                self.assertTrue(level_up.item_rule(ship))
                self.assertTrue(all(bonus.item_rule(ship) for bonus in bonuses))
        self.assertFalse(foreign.item_rule(source_world.create_item('Potion')))
        self.assertTrue(foreign.item_rule(source_world.create_item('Gummi Part: Thruster/G')))
        linked_ship = linked_ships['world'].create_item(ENDYMION)
        self.assertTrue(foreign.item_rule(linked_ship))
        self.assertTrue(chest.item_rule(linked_ship))
        self.assertTrue(space.item_rule(linked_ship))
        self.assertTrue(all(bonus.item_rule(linked_ship) for bonus in bonuses))
        # An unrelated game's item with the same display name is not a KH3 ship.
        self.assertTrue(foreign.item_rule(Item(ENDYMION, ItemClassification.progression, 2, 3)))


class TestSpaceDisabled(KH3TestBase):
    options = {'include_space': 0}

    def test_vanilla_space_has_no_ap_pool_or_checks(self):
        self.assertFalse(any(r['type'] == 'space' for r in self.world.active_locations))
        self.assertFalse(any(i.name in SPACE_ITEMS for i in self.multiworld.itempool))
        self.assertEqual(self.world.build_runtime_slot_data()['include_space'], 0)
