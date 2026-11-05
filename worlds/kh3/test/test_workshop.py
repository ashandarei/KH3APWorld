from collections import Counter
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from BaseClasses import Item, ItemClassification
from Fill import distribute_items_restrictive

from ..Data import load_extracted_data, normalize_locations
from ..export_ap_kh3_randomized_options import export_randomized_options_from_bridge_data
from ..Locations import BASE_LOCATION_ID, location_name_to_id
from ..Workshop import WORKSHOP_LOCATIONS, WORKSHOP_OPTIONS
from . import KH3TestBase


class WorkshopTests(TestCase):
    def test_catalog_has_unique_append_only_checks(self):
        self.assertEqual(Counter(r['type'] for r in WORKSHOP_LOCATIONS),
                         {'synthesis': 67, 'photo_mission': 20, 'keyblade_upgrade': 19})
        names = {r['name'] for r in WORKSHOP_LOCATIONS}
        self.assertEqual(len(names), 106)
        self.assertFalse(any(r['source_table'] == 'SynthesisItem' and r['source_id'] == 'IS_58'
                             for r in WORKSHOP_LOCATIONS))
        catalog = normalize_locations(load_extracted_data())
        # Workshop follows the earlier catalog; adversaries are appended after it.
        with (patch("worlds.kh3.Data.append_workshop_locations"),
              patch("worlds.kh3.Data.append_adversary_locations")):
            previous = [r for r in normalize_locations(load_extracted_data()) if r["source_id"] != "EVENT_REPORT_000"]
        self.assertEqual({r['name'] for r in catalog[len(previous):len(previous) + 106]}, names)
        for index, row in enumerate(previous):
            self.assertEqual(location_name_to_id[row['name']], BASE_LOCATION_ID + index)
        self.assertGreater(min(location_name_to_id[n] for n in names),
                           max(location_name_to_id[r['name']] for r in previous))


class WorkshopWorldTests(KH3TestBase):
    options = {'synthesis_pool': 'randomize', 'photo_missions_pool': 'randomize',
               'keyblade_upgrade_rewards_pool': 'randomize', 'hundred_acre_wood_pool': 'randomize'}

    def test_pools_can_be_enabled_independently(self):
        for enabled_type, enabled_option in WORKSHOP_OPTIONS.items():
            for mode in ('randomize', 'junk'):
                with self.subTest(pool=enabled_option, mode=mode):
                    self.options = dict.fromkeys(WORKSHOP_OPTIONS.values(), 'vanilla')
                    self.options[enabled_option] = mode
                    self.options['moogle_synthesis_limit'] = 67
                    self.options['hundred_acre_wood_pool'] = 'randomize'
                    self.world_setup(seed=913)
                    active = [r for r in self.world.active_locations if r['type'] in WORKSHOP_OPTIONS]
                    expected = [r for r in WORKSHOP_LOCATIONS if r['type'] == enabled_type]
                    self.assertEqual({r['name'] for r in active}, {r['name'] for r in expected})
                    self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
                    strict_junk = mode == 'junk'
                    for row in active:
                        check = self.multiworld.get_location(row['name'], self.player)
                        for classification in (ItemClassification.progression, ItemClassification.useful):
                            for owner in (self.player, self.player + 1):
                                item = Item('Test reward', classification, 1, owner)
                                self.assertEqual(check.item_rule(item), not strict_junk)
                        for classification in (ItemClassification.filler, ItemClassification.trap):
                            self.assertTrue(check.item_rule(Item('Test junk', classification, 2, self.player)))

    def test_junk_workshop_checks_cannot_receive_overflow_during_fill(self):
        self.options = {'synthesis_pool': 'junk', 'photo_missions_pool': 'junk',
                        'moogle_synthesis_limit': 67, 'olympus_pool': 'junk',
                        'hundred_acre_wood_pool': 'randomize'}
        self.world_setup(seed=914)
        protected = {row['name'] for row in self.world.active_locations
                     if row['type'] in {'synthesis', 'photo_mission'}}
        self.assertEqual(len(protected), 82)
        # The same strict restriction applies to ordinary world junk checks.
        olympus = next(self.multiworld.get_location(row['name'], self.player)
                       for row in self.world.active_locations if row['type'] == 'treasure' and row['world'] == 'Olympus')
        self.assertTrue(olympus.filler_only)
        self.assertFalse(olympus.item_rule(self.world.create_item('Magic: Fire')))
        distribute_items_restrictive(self.multiworld)
        self.assertFalse(self.multiworld.get_unfilled_locations())
        self.assertTrue(self.multiworld.can_beat_game())
        for name in protected:
            item = self.multiworld.get_location(name, self.player).item
            self.assertFalse(item.classification & (ItemClassification.progression | ItemClassification.useful), name)

    def test_every_photo_uses_its_subject_world_completion(self):
        from ..ColiseumLogic import COLISEUM_WORLD_COMPLETION_SOURCES
        from ..Data import PORTAL_KEYBLADE_REGION_LOCKS
        from ..Workshop import (PHOTO_MISSION_ADDITIONAL_SOURCES,
                                PHOTO_MISSION_COMPLETION_WORLDS, PHOTO_WORLD_COMPLETION_EXTRA_SOURCES)
        self.assertEqual(set(PHOTO_MISSION_COMPLETION_WORLDS),
                         {r['source_id'] for r in WORKSHOP_LOCATIONS if r['type'] == 'photo_mission'})
        # Remove each world portal in turn, retaining other worlds and combat
        # items. Verify independent missions don't inherit unrelated gates.
        for missing in (None, *COLISEUM_WORLD_COMPLETION_SOURCES):
            with self.subTest(missing_world=missing):
                self.world_setup(seed=915)
                excluded = {"Heart Piece"}
                if missing:
                    excluded.add(PORTAL_KEYBLADE_REGION_LOCKS[missing])
                for item in self.multiworld.precollected_items[self.player]:
                    if item.name in excluded:
                        self.multiworld.state.remove(item)
                self.collect_all_but(excluded)
                def source_reachable(key):
                    return self.get_location_by_source(*key).can_reach(self.multiworld.state)
                for mission, regions in PHOTO_MISSION_COMPLETION_WORLDS.items():
                    expected = all(self.multiworld.get_region(region, self.player).can_reach(self.multiworld.state)
                        and all(source_reachable(key) for key in
                            PHOTO_WORLD_COMPLETION_EXTRA_SOURCES.get(region,
                                COLISEUM_WORLD_COMPLETION_SOURCES.get(region, ()))) for region in regions)
                    expected &= all(source_reachable(key) for key in PHOTO_MISSION_ADDITIONAL_SOURCES.get(mission, ()))
                    self.assertEqual(self.get_location_by_source('PhotoMission', mission)
                                     .can_reach(self.multiworld.state), expected, (missing, mission))

    def test_olympus_photos_require_world_completion_without_endgame(self):
        from ..Data import PORTAL_KEYBLADE_REGION_LOCKS
        from ..Locations import COMPLETION_LOCATION_NAME
        excluded = (set(PORTAL_KEYBLADE_REGION_LOCKS.values()) - {"Hero's Origin"}) | {
            "Heart Piece", "Hunny Spout"}
        for item in self.multiworld.precollected_items[self.player]:
            if item.name in excluded:
                self.multiworld.state.remove(item)
        self.collect_all_but(excluded)
        completion = self.get_location_by_source('VBonus', 'Vbonus_013')
        self.assertTrue(completion.can_reach(self.multiworld.state))
        self.assertFalse(self.multiworld.get_location(COMPLETION_LOCATION_NAME, self.player)
                         .can_reach(self.multiworld.state))
        for source_id in ('IM_7', 'IM_12'):
            self.assertTrue(self.get_location_by_source('PhotoMission', source_id)
                            .can_reach(self.multiworld.state))
        self.assertFalse(self.get_location_by_source('PhotoMission', 'IM_8')
                         .can_reach(self.multiworld.state))
        self.assertFalse(self.get_location_by_source('PhotoMission', 'IM_9')
                         .can_reach(self.multiworld.state))
        # A portal unlock alone must not stand in for finishing the world.
        from BaseClasses import CollectionState
        starting = CollectionState(self.multiworld)
        starting.collect(self.world.create_item("Hero's Origin"))
        self.assertFalse(completion.can_reach(starting))
        for source_id in ('IM_7', 'IM_12'):
            self.assertFalse(self.get_location_by_source('PhotoMission', source_id)
                             .can_reach(starting))

    def test_corona_photos_require_world_completion_without_olympus_or_endgame(self):
        from BaseClasses import CollectionState
        from ..Locations import COMPLETION_LOCATION_NAME
        excluded = {"Hero's Origin", "Heart Piece"}
        for item in self.multiworld.precollected_items[self.player]:
            if item.name in excluded:
                self.multiworld.state.remove(item)
        self.collect_all_but(excluded)
        completion = self.get_location_by_source('VBonus', 'Vbonus_030')
        self.assertTrue(completion.can_reach(self.multiworld.state))
        self.assertFalse(self.get_location_by_source('VBonus', 'Vbonus_013')
                         .can_reach(self.multiworld.state))
        self.assertFalse(self.multiworld.get_location(COMPLETION_LOCATION_NAME, self.player)
                         .can_reach(self.multiworld.state))
        for source_id in ('IM_8', 'IM_9'):
            self.assertTrue(self.get_location_by_source('PhotoMission', source_id)
                            .can_reach(self.multiworld.state))
        starting = CollectionState(self.multiworld)
        starting.collect(self.world.create_item('Ever After'))
        self.assertFalse(completion.can_reach(starting))
        for source_id in ('IM_8', 'IM_9'):
            self.assertFalse(self.get_location_by_source('PhotoMission', source_id).can_reach(starting))

    def test_randomized_photo_pool_fills_without_progression_cycles(self):
        distribute_items_restrictive(self.multiworld)
        self.assertFalse(self.multiworld.get_unfilled_locations())
        self.assertTrue(self.multiworld.can_beat_game())
        self.assertTrue(self.multiworld.fulfills_accessibility())

    def test_junked_worlds_omit_their_photo_checks_and_any_required_team_check(self):
        expected_by_option = {
            'olympus_pool': {'IM_0', 'IM_2', 'IM_7', 'IM_12', 'IM_6'},
            'kingdom_of_corona_pool': {'IM_8', 'IM_9', 'IM_15', 'IM_17', 'IM_6'},
            'toy_box_pool': {'IM_13', 'IM_14', 'IM_6'},
            'monstropolis_pool': {'IM_3', 'IM_11', 'IM_6'},
            'twilight_town_pool': {'IM_19'},
            'arendelle_pool': {'IM_1', 'IM_10', 'IM_6'},
            'san_fransokyo_pool': {'IM_5', 'IM_18', 'IM_6'},
            'the_caribbean_pool': {'IM_4', 'IM_6'},
            'hundred_acre_wood_pool': {'IM_16'},
        }
        all_photos = {r['source_id'] for r in WORKSHOP_LOCATIONS if r['type'] == 'photo_mission'}
        for mode in ('randomize', 'junk'):
            for option, omitted in expected_by_option.items():
                with self.subTest(photo_mode=mode, junk_world=option):
                    self.options = {'photo_missions_pool': mode, 'hundred_acre_wood_pool': 'randomize',
                                    option: 'junk'}
                    self.world_setup(seed=916)
                    active = {r['source_id'] for r in self.world.active_locations if r['type'] == 'photo_mission'}
                    self.assertEqual(active, all_photos - omitted)
                    exported = {r['source_id'] for r in self.world.build_runtime_slot_data()['bridge']['locations']
                                if r['type'] == 'photo_mission'}
                    self.assertEqual(exported, active)
                    self.assertEqual(len(self.multiworld.itempool),len(self.multiworld.get_unfilled_locations()))

    def test_all_junk_worlds_omit_every_photo_check(self):
        from ..Data import WORLD_LOCATION_POOL_OPTIONS
        self.options = {option: 'junk' for option in WORLD_LOCATION_POOL_OPTIONS.values()}
        self.options['photo_missions_pool'] = 'randomize'
        self.world_setup(seed=917)
        self.assertFalse(any(r['type'] == 'photo_mission' for r in self.world.active_locations))
        self.assertFalse(any(r['type'] == 'photo_mission'
                             for r in self.world.build_runtime_slot_data()['bridge']['locations']))
        distribute_items_restrictive(self.multiworld)
        self.assertFalse(self.multiworld.get_unfilled_locations())
        self.assertTrue(self.multiworld.can_beat_game())

    def test_workshop_bridge_delivery(self):
        rows = [r for r in self.world.build_runtime_slot_data()['bridge']['locations']
                if r['type'] in WORKSHOP_OPTIONS]
        self.assertEqual(len(rows), 106)
        self.assertTrue(all(r['bridge_delivery_required'] for r in rows))

    def test_exact_export_replaces_only_enabled_workshop_rewards(self):
        rows = [r for r in self.world.build_runtime_slot_data()['bridge']['locations'] if r['type'] in WORKSHOP_OPTIONS]
        for index, row in enumerate(rows):
            row.update(assigned_item_name='Potion' if index % 2 == 0 else 'Foreign Item',
                       assigned_item_game='Kingdom Hearts III' if index % 2 == 0 else 'Other',
                       assigned_item_player=1 if index % 2 == 0 else 2,
                       assigned_item_player_name='Tester' if index % 2 == 0 else 'Other')
        root = Path(__file__).resolve().parents[1]
        result = export_randomized_options_from_bridge_data(
            default_kh3=root/'randomizer/wwwroot/DefaultKH3.json',
            default_kh3_overrides=root/'randomizer/wwwroot/DefaultKH3.ArchipelagoOverrides.json',
            extracted_data_path=root/'data/extracted_locations.json', bridge_locations=rows,
            slot=1, slot_name='Tester', archive_name='workshop.zip')
        tokens = set()
        for row in rows:
            if row['type'] == 'synthesis':
                token = result.randomized_options['SynthesisItem'][row['source_id']]['Reward'].rstrip('\0')
                self.assertEqual(token, row['vanilla_item'])
                tokens.add(row['source_id'])
            elif row['type'] == 'keyblade_upgrade':
                self.assertEqual(result.randomized_options['WeaponEnhance'][row['source_id']]['Ability'].rstrip('\0'),
                                 'ETresAbilityKind::NONE')
        self.assertEqual(len(tokens), 67)
        self.assertTrue(all(r['bridge_delivery_required'] for r in result.placement_summary['locations']))
        self.assertEqual(result.randomized_options['SynthesisItem']['IS_61']['Reward'].rstrip('\0'), 'PRT_ITEM45')

    def test_upgrade_requires_owned_weapon_and_levels(self):
        check = self.get_location_by_source('WeaponEnhance', 'IW_3')
        self.assertFalse(check.filler_only)
        from BaseClasses import CollectionState
        state = CollectionState(self.multiworld)
        state.prog_items[self.player].clear()
        self.assertFalse(check.can_reach(state))
        state.collect(self.world.create_item('Kingdom Key'), prevent_sweep=True)
        for _ in range(3):
            state.collect(self.world.create_item('Progressive Keyblade Level'), prevent_sweep=True)
        self.assertFalse(check.can_reach(state))
        state.collect(self.world.create_item('Progressive Keyblade Level'), prevent_sweep=True)
        self.assertTrue(check.can_reach(state))

    def test_synthesis_limit_rejects_progression_and_useful(self):
        check = self.get_location_by_source('SynthesisItem', 'IS_15')
        self.assertFalse(check.item_rule(self.world.create_item('Kingdom Key')))
        self.assertTrue(check.item_rule(self.world.create_item('Potion')))


class WorkshopMaxLevelTests(KH3TestBase):
    options = {'keyblade_upgrade_rewards_pool': 'randomize', 'keyblade_levels': 'start_at_max'}

    def test_max_level_rewards_accept_only_filler_and_traps(self):
        upgrades = [self.multiworld.get_location(row['name'], self.player)
                    for row in self.world.active_locations if row['type'] == 'keyblade_upgrade']
        self.assertEqual(len(upgrades), 19)
        for check in upgrades:
            self.assertTrue(check.filler_only)
            self.assertTrue(check.item_rule(self.world.create_item('Potion')))
            self.assertFalse(check.item_rule(self.world.create_item('Ability: Formchange Extender')))
            self.assertFalse(check.item_rule(self.world.create_item('Magic: Fire')))

    def test_max_level_still_requires_weapon(self):
        check = self.get_location_by_source('WeaponEnhance', 'IW_43')
        if self.multiworld.state.has('Ever After', self.player):
            self.remove(self.world.create_item('Ever After'))
        self.assertFalse(check.can_reach(self.multiworld.state))
        self.collect(self.world.create_item('Ever After'))
        self.assertTrue(check.can_reach(self.multiworld.state))


class WorkshopIndividualLevelTests(KH3TestBase):
    options = {'keyblade_upgrade_rewards_pool': 'randomize', 'keyblade_levels': 'keyblade_upgrade_sanity'}

    def test_levels_from_another_weapon_do_not_unlock_check(self):
        check = self.get_location_by_source('WeaponEnhance', 'IW_3')
        self.assertFalse(check.filler_only)
        self.collect_by_name('Kingdom Key')
        self.collect_by_name('Progressive Ever After Level')
        self.assertFalse(check.can_reach(self.multiworld.state))
        self.collect_by_name('Progressive Kingdom Key Level')
        self.assertTrue(check.can_reach(self.multiworld.state))
