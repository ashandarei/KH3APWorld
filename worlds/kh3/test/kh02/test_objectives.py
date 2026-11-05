"""AP catalog, generation, delivery and tracker coverage for integrated 0.2."""
from collections import Counter
from itertools import product
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from BaseClasses import Item, ItemClassification
from ...Data import SPELLWEAVER_UNLOCK_ITEM, resolve_item_runtime_token, classify_runtime_grant_method
from Fill import distribute_items_restrictive
from test.general import setup_multiworld

from ... import KH3World
from ...Bridge import build_bridge_slot_data
from ...Data import local_seeded_reward_requires_bridge_delivery
from ...kh02.locations import (ALL_KH02_LOCATIONS, KH02_CHEST_ID_BASE, KH02_LOCATION_TYPES,
                             SOURCE_CHESTS, SUPPORTED_CHALLENGES, UNAVAILABLE_CHEST_IDS,
                             INTRO_CHEST_ID, EXCLUDED_CHEST_IDS)
from ...Locations import location_name_groups, location_name_to_id
from ...SlotData import _catalog, decode_slot_data, encode_slot_data
from ...export_ap_kh3_randomized_options import BRIDGE_ONLY_LOCATION_TYPES
from ...export_ap_kh3_randomized_options import ALWAYS_ON_EVENT_IDS, export_randomized_options_from_bridge_data
from .. import KH3TestBase
from ..test_universal_tracker import TestKH3UniversalTracker as UniversalTrackerTest

ENABLED = {"kh02_enabled": 1, "kh02_challenges": 1}


class TestKH02Catalog(TestCase):
    def test_stable_addresses_groups_and_supported_conditions(self):
        self.assertEqual(Counter(row["type"] for row in ALL_KH02_LOCATIONS),
                         {"kh02_chest": 40, "kh02_boss": 8, "kh02_objective": 51})
        self.assertEqual(len(location_name_to_id), len(set(location_name_to_id.values())))
        self.assertEqual(location_name_groups["Kingdom Hearts 0.2"],
                         {row["name"] for row in ALL_KH02_LOCATIONS})
        for row in ALL_KH02_LOCATIONS:
            self.assertEqual(row["ap_location_id"], location_name_to_id[row["name"]])
            self.assertEqual(("kh02_flag" in row) + ("kh02_challenge" in row), 1)
        self.assertEqual(SUPPORTED_CHALLENGES, frozenset(range(1, 52)))
        self.assertTrue(any(row["source_id"] == "Treasure:DW_BOX009" for row in ALL_KH02_LOCATIONS))
        self.assertTrue(any(row["source_id"] == "Treasure:DW_LBOX04" for row in ALL_KH02_LOCATIONS))

    def test_post_clear_chests_are_shuffled_but_intro_chest_is_never_a_location(self):
        self.assertEqual(UNAVAILABLE_CHEST_IDS, frozenset())
        self.assertEqual(INTRO_CHEST_ID, "DW_BOX001")
        self.assertEqual(EXCLUDED_CHEST_IDS, {INTRO_CHEST_ID})
        chests = {row["source_id"].removeprefix("Treasure:"): row for row in ALL_KH02_LOCATIONS
                  if row["type"] == "kh02_chest"}
        self.assertTrue(UNAVAILABLE_CHEST_IDS.isdisjoint(chests))
        self.assertTrue(EXCLUDED_CHEST_IDS.isdisjoint(chests))
        self.assertNotIn(INTRO_CHEST_ID, {row[2] for row in SOURCE_CHESTS})
        self.assertTrue({"DW_BOX002", "DW_BOX005", "DW_BOX010", "DW_BOX031", "DW_BOX036"}.issubset(chests))
        self.assertTrue({"DW_BOX009", "DW_BOX026", "DW_BOX027", "DW_BOX028", "DW_BOX029", "DW_LBOX04",
                         "DW_BOX012", "DW_BOX014", "DW_BOX030", "DW_BOX032", "DW_BOX034", "DW_BOX035",
                         "DW_BOX037"}.issubset(chests))
        for number, _, identity in SOURCE_CHESTS:
            if identity in chests:
                self.assertEqual(chests[identity]["ap_location_id"], KH02_CHEST_ID_BASE + number)

    def test_wire_catalog_has_only_supported_objectives_and_bridge_delivery(self):
        catalog, _ = _catalog()
        rows = build_bridge_slot_data(list(ALL_KH02_LOCATIONS), 7)["locations"]
        for row in rows:
            self.assertTrue(row["bridge_delivery_required"])
            self.assertIn(row["type"], BRIDGE_ONLY_LOCATION_TYPES)
            self.assertTrue(local_seeded_reward_requires_bridge_delivery(
                row["bridge_key"], row["type"], "ITEM_POTION"))
            self.assertEqual(row, catalog[row["ap_location_id"]])

    def test_seed_export_keeps_kh02_rewards_out_of_native_kh3_tables(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            defaults, extracted = root / "defaults.json", root / "extracted.json"
            defaults.write_text(json.dumps({"Event": {
                key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS
            }}))
            extracted.write_text(json.dumps({"items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": []}))
            rows = build_bridge_slot_data(list(ALL_KH02_LOCATIONS), 7)["locations"]
            for row in rows:
                row.update(assigned_item_game="Kingdom Hearts III", assigned_item_name="Potion",
                           assigned_item_player=1, assigned_item_player_name="KH3")
            result = export_randomized_options_from_bridge_data(
                default_kh3=defaults, default_kh3_overrides=None, extracted_data_path=extracted,
                slot=1, slot_name="KH3", archive_name="AP_test.zip", bridge_locations=rows,
            )
            self.assertNotIn("KH02", result.randomized_options)


class TestKH02World(KH3TestBase):
    def test_spellweaver_is_precollected_when_kh02_is_enabled(self):
        token = resolve_item_runtime_token(SPELLWEAVER_UNLOCK_ITEM)
        self.assertEqual(token, "KH3AP_CONTROLLER_UNLOCK::spellweaver")
        self.assertEqual(classify_runtime_grant_method(token), "controller_unlock")
        for stylesanity in (0, 1):
            for choices in product((0, 1), repeat=len(ENABLED)):
                settings = dict(zip(ENABLED, choices))
                expected = settings["kh02_enabled"]
                with self.subTest(stylesanity=stylesanity, **settings):
                    self.options = {"stylesanity": stylesanity, **settings}
                    self.world_setup(seed=357)
                    starting = [item.name for item in self.multiworld.precollected_items[self.player]]
                    self.assertEqual(starting.count(SPELLWEAVER_UNLOCK_ITEM), expected)
                    self.assertEqual(self.world.kh3_starting_inventory_items.count(SPELLWEAVER_UNLOCK_ITEM), expected)
                    self.assertNotIn(SPELLWEAVER_UNLOCK_ITEM, [item.name for item in self.multiworld.itempool])
                    self.assertEqual(self.world.create_item(SPELLWEAVER_UNLOCK_ITEM).classification,
                                     ItemClassification.useful)
                    self.assertEqual(len(self.multiworld.itempool),
                                     len(self.multiworld.get_unfilled_locations(self.player)))

    def test_starting_playable_aqua_also_grants_spellweaver_once(self):
        cases = (
            ({"Playable Character: Aqua": 1}, {"kh02_enabled": 0}, 1),
            ({"Playable Character: Aqua": 1}, {"kh02_enabled": 1}, 1),
            ({"Playable Character: Aqua": 0}, {"kh02_enabled": 0}, 0),
            ({"Party Member: Aqua": 1}, {"kh02_enabled": 0}, 0),
        )
        for option in ("start_inventory", "start_inventory_from_pool"):
            for stylesanity in (0, 1):
                for inventory, kh02, expected in cases:
                    with self.subTest(option=option, stylesanity=stylesanity, inventory=inventory, kh02=kh02):
                        self.options = {option: inventory, "stylesanity": stylesanity, **kh02}
                        self.world_setup(seed=358)
                        starting = [item.name for item in self.multiworld.precollected_items[self.player]]
                        self.assertEqual(starting.count(SPELLWEAVER_UNLOCK_ITEM), expected)
                        self.assertEqual(self.world.kh3_starting_inventory_items.count(SPELLWEAVER_UNLOCK_ITEM),
                                         expected)
                        self.assertNotIn(SPELLWEAVER_UNLOCK_ITEM, [item.name for item in self.multiworld.itempool])
                        self.assertEqual(len(self.multiworld.itempool),
                                         len(self.multiworld.get_unfilled_locations(self.player)))

    def test_enabled_with_challenges_by_default(self):
        from ...Options import KH3Options

        self.assertEqual(Counter(row["type"] for row in self.world.active_locations
                                 if row["type"] in KH02_LOCATION_TYPES),
                         {"kh02_chest": 40, "kh02_boss": 8, "kh02_objective": 51})
        runtime = self.world.build_runtime_slot_data()
        self.assertEqual({key: runtime[key] for key in ENABLED}, ENABLED)
        self.assertEqual({key for key in KH3Options.type_hints if key.startswith("kh02_")}, set(ENABLED))

    def test_master_switch_and_optional_challenges_balance_items(self):
        for enabled, challenges in product((0, 1), repeat=2):
            with self.subTest(enabled=enabled, challenges=challenges):
                self.options = {"kh02_enabled": enabled, "kh02_challenges": challenges}
                self.world_setup(seed=351)
                rows = [row for row in self.world.active_locations if row["type"] in KH02_LOCATION_TYPES]
                expected = {"kh02_chest": 40, "kh02_boss": 8} if enabled else {}
                if enabled and challenges:
                    expected["kh02_objective"] = 51
                self.assertEqual(Counter(row["type"] for row in rows), expected)
                self.assertEqual(any(region.name == "Kingdom Hearts 0.2" for region in self.multiworld.regions),
                                 bool(enabled))
                self.assertEqual(any(row["name"] == "0.2 - Unlock Aqua" for row in self.world.active_locations),
                                 bool(enabled))
                self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_tracker_and_slot_data_preserve_check_conditions(self):
        for enabled, challenges in product((0, 1), repeat=2):
            with self.subTest(enabled=enabled, challenges=challenges):
                self.options = {"kh02_enabled": enabled, "kh02_challenges": challenges}
                self.world_setup(seed=352)
                runtime = self.world.build_runtime_slot_data()
                self.assertEqual({key: runtime[key] for key in ENABLED}, self.options)
                self.assertEqual(runtime, decode_slot_data(encode_slot_data(runtime)))
                tracker = UniversalTrackerTest.build_yamlless_tracker_multiworld(self, runtime)
                actual = [row for row in self.world.active_locations if row["type"] in KH02_LOCATION_TYPES]
                restored = [row for row in tracker.worlds[1].active_locations if row["type"] in KH02_LOCATION_TYPES]
                self.assertEqual(actual, restored)
                self.assertEqual(len(actual), (48 + 51 * challenges) if enabled else 0)

    def test_kh02_checks_allow_all_item_classes_for_all_players(self):
        self.options = ENABLED
        self.world_setup(seed=353)
        for row in ALL_KH02_LOCATIONS:
            location = self.multiworld.get_location(row["name"], self.player)
            self.assertTrue(location.progression_allowed)
            for player in (self.player, self.player + 1):
                for classification in (ItemClassification.filler, ItemClassification.useful,
                                       ItemClassification.trap, ItemClassification.progression,
                                       ItemClassification.progression_skip_balancing,
                                       ItemClassification.progression | ItemClassification.useful):
                    item = Item("test", classification, 1, player)
                    self.assertTrue(location.item_rule(item))

    def test_magic_checks_require_magic_unlock_in_both_portal_modes(self):
        from BaseClasses import CollectionState
        from ...kh02.locations import KH02_MAGIC_REQUIRED_SOURCE_IDS
        for mode in (0, 1):
            for starting_magic in (0, 1):
                with self.subTest(portal_unlocks=mode, start_with_magic=starting_magic):
                    self.options = {**ENABLED, "portal_unlocks": mode, "start_with_magic": starting_magic}
                    self.world_setup(seed=355)
                    state = CollectionState(self.multiworld)
                    checks = [self.multiworld.get_location(row["name"], self.player) for row in ALL_KH02_LOCATIONS]
                    gated = [self.get_location_by_source("KH02", source)
                             for source in KH02_MAGIC_REQUIRED_SOURCE_IDS]
                    self.assertTrue(all(not location.can_reach(state) for location in checks))
                    state.collect(self.world.create_item("Oblivion"))
                    self.assertTrue(all(location.can_reach(state) for location in checks if location not in gated))
                    self.assertTrue(all(location.can_reach(state) == bool(starting_magic) for location in gated))
                    state.collect(self.world.create_item("Magic: Fire"))
                    self.assertTrue(all(location.can_reach(state) == bool(starting_magic) for location in gated))
                    unlock = self.world.create_item("Unlock: Magic")
                    state.collect(unlock)
                    self.assertTrue(all(location.can_reach(state) for location in checks))
                    if not starting_magic:
                        state.remove(unlock)
                        self.assertTrue(all(not location.can_reach(state) for location in gated))

    def test_dark_aqua_keeps_its_check_and_combat_requirements(self):
        from BaseClasses import CollectionState
        for enabled, challenges in product((0, 1), repeat=2):
            self.options = {"kh02_enabled": enabled, "kh02_challenges": challenges, "portal_unlocks": 1,
                            "default_base_abilities": 0, "start_with_magic": 0}
            self.world_setup(seed=356)
            row = next(row for row in self.world.active_locations
                       if (row["source_table"], row["source_id"]) == ("VBonus", "Vbonus_067"))
            location = self.multiworld.get_location(row["name"], self.player)
            state = CollectionState(self.multiworld)
            state.collect(self.world.create_item("Party Member: Aqua"))
            self.assertEqual(self.multiworld.get_region("Dark World", self.player).can_reach(state), not enabled)
            state.collect(self.world.create_item("Oblivion"))
            self.assertFalse(location.can_reach(state))
            for item in ("Ability: Block", "Ability: Air Slide", "Ability: Dodge Roll", "Magic: Cure", "Unlock: Magic"):
                state.collect(self.world.create_item(item))
            self.assertTrue(location.can_reach(state))


class TestKH02Generation(TestCase):
    def test_fill_checks_accessibility_and_delivery_at_trap_extremes(self):
        for mode in (0, 1):
            for trap_percentage in (0, 100):
                with self.subTest(portal_unlocks=mode, trap_percentage=trap_percentage):
                    multiworld = setup_multiworld(KH3World, seed=354, options={
                        **ENABLED, "trap_percentage": trap_percentage, "portal_unlocks": mode})
                    distribute_items_restrictive(multiworld)
                    self.assertFalse(multiworld.get_unfilled_locations())
                    self.assertTrue(multiworld.can_beat_game())
                    state = multiworld.get_all_state(False)
                    runtime = multiworld.worlds[1].build_runtime_slot_data()
                    checks = [row for row in runtime["bridge"]["locations"] if row["type"] in KH02_LOCATION_TYPES]
                    self.assertEqual(len(checks), 99)
                    for row in checks:
                        location = multiworld.get_location(row["ap_location_name"], 1)
                        self.assertTrue(location.can_reach(state))
                        self.assertTrue(row["bridge_delivery_required"])
