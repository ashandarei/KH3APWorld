
from worlds.kh3.test.runtime_sources import native_project_root
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ..Data import RANDOMIZED_KEYBLADE_ITEM_NAMES, load_extracted_data, normalize_locations
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    export_randomized_options_from_bridge_data,
)
from ..Ingredients import (
    INGREDIENT_FIXED_REWARDS,
    INGREDIENT_JUNK_SPOTS,
    INGREDIENT_SPAWN_RATES,
    INGREDIENT_SPOTS,
    INGREDIENT_WORLD_SOURCES,
    INGREDIENTS_BY_ID,
)
from ..Locations import BASE_LOCATION_ID, location_name_to_id
from ..Logic import require_all_locations, require_any_location
from ..LogicData import LOCATION_LOGIC_DEPENDENCIES
from ..Options import Ingredients
from . import KH3TestBase
from .test_helpers import KH3WorldIntegrityMixin


class TestIngredientChecks(KH3WorldIntegrityMixin, KH3TestBase):
    options = {"ingredients": "ingredientsanity"}

    def test_one_reachable_field_source_is_enough(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        lemon = self.get_location_by_source("Ingredient", "FST_ITEM35")
        self.assertFalse(lemon.can_reach(state))
        state.collect(self.world.create_item("Happy Gear"))
        self.assertTrue(lemon.can_reach(state))
        self.assertFalse(any(row["source_table"] == "IngredientSpot" for row in self.world.active_locations))

    def test_standard_ingredient_inherits_reviewed_hidden_spot_route(self):
        # Isolate the reviewed Chocolate source from its other valid sources.
        with patch.dict(LOCATION_LOGIC_DEPENDENCIES, {
            ("Ingredient", "FST_ITEM42"): require_any_location(("IngredientSpot", "TS_02_PF_003")),
        }):
            self.world_setup()
            chocolate = self.get_location_by_source("Ingredient", "FST_ITEM42")
            state = self._state_without_precollected_items()
            state.collect(self.world.create_item("Favorite Deputy"))
            self.assertFalse(chocolate.can_reach(state))
            state.collect(self.world.create_item("Ability: High Jump"))
            self.assertTrue(chocolate.can_reach(state))
            self.assertFalse(any(row["source_table"] == "IngredientSpot"
                                 for row in self.world.active_locations))

    def test_catalog_and_item_balance(self):
        self.world_setup(seed=181)
        checks = [x for x in self.world.active_locations if x["type"] == "ingredient"]
        self.assertEqual(len(checks), 51)
        self.assertEqual({x["source_id"] for x in checks}, set(INGREDIENT_WORLD_SOURCES))
        self.assertEqual(len(INGREDIENTS_BY_ID), 59)
        slots = self.world.build_runtime_slot_data()
        self.assertEqual(slots["ingredients"], 1)
        self.assertEqual(slots["ingredient_checks"], 1)
        self.assertTrue(all(x.get("bridge_delivery_required") for x in slots["bridge"]["locations"]
                            if x["type"] == "ingredient"))
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_existing_ids_are_unchanged(self):
        catalog = normalize_locations(load_extracted_data())
        # Only the catalog published before ingredients belongs to this
        # prefix; later level-up and minigame additions follow ingredients.
        old = catalog[:next(index for index, row in enumerate(catalog) if row["type"] == "ingredient")]
        for index, row in enumerate(old):
            self.assertEqual(location_name_to_id[row["name"]], BASE_LOCATION_ID + index)
        self.assertTrue(all(location_name_to_id[x["name"]] >= BASE_LOCATION_ID+len(old)
                            for x in catalog if x["type"] == "ingredient"))

    def test_requires_source_world_access(self):
        self.world_setup(seed=182)
        self.assertFalse(self.multiworld.get_location("Ingredients - Crab", self.player).can_reach(self.multiworld.state))
        self.collect_all_but([])
        self.assertTrue(self.multiworld.get_location("Ingredients - Crab", self.player).can_reach(self.multiworld.state))

    def test_export_keeps_native_ingredient_and_delivers_reward_via_bridge(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "default.json"
            extracted = root / "extracted.json"
            defaults.write_text(json.dumps({"Event": {
                key: {"RandomizedItem": "ITEM_POTION\0"} for key in ALWAYS_ON_EVENT_IDS
            }}))
            extracted.write_text(json.dumps({
                "items": [{"name": "Potion", "id": "ITEM_POTION"}], "locations": [],
            }))
            result = export_randomized_options_from_bridge_data(
                default_kh3=defaults, default_kh3_overrides=None,
                extracted_data_path=extracted, slot=1, slot_name="KH3", archive_name="AP_test.zip",
                bridge_locations=[dict(
                    ap_location_id=location_name_to_id["Ingredients - Bay Leaf"],
                    ap_location_name="Ingredients - Bay Leaf", assigned_item_game="Kingdom Hearts III",
                    assigned_item_name="Potion", assigned_item_player=1, assigned_item_player_name="KH3",
                    bridge_key="Ingredient:FST_ITEM28", source_id="FST_ITEM28",
                    source_table="Ingredient", type="ingredient",
                ), dict(
                    ap_location_id=location_name_to_id[INGREDIENT_SPOTS[0]["name"]],
                    ap_location_name=INGREDIENT_SPOTS[0]["name"], assigned_item_game="Kingdom Hearts III",
                    assigned_item_name="Potion", assigned_item_player=1, assigned_item_player_name="KH3",
                    bridge_key="IngredientSpot:" + INGREDIENT_SPOTS[0]["id"], source_id=INGREDIENT_SPOTS[0]["id"],
                    source_table="IngredientSpot", type="ingredient",
                )],
            )
            self.assertNotIn("Ingredient", result.randomized_options)
            placement = result.placement_summary["locations"][0]
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertEqual(placement["runtime_token"], "ITEM_POTION")
            self.assertNotIn("IngredientSpot", result.randomized_options)
            self.assertTrue(result.placement_summary["locations"][1]["bridge_delivery_required"])


class TestIngredientOptions(KH3TestBase):
    def test_requested_yaml_modes_generate_checks(self):
        for mode, value, count in (("ingredientsanity", 1, 51), ("ingredientsanity_plus", 2, 298)):
            with self.subTest(mode=mode):
                self.options = {"ingredients": mode}
                self.world_setup(seed=184)
                self.assertEqual(sum(x["type"] == "ingredient" for x in self.world.active_locations), count)
                slots = self.world.build_runtime_slot_data()
                self.assertEqual(slots["ingredients"], value)
                self.assertEqual(slots["ingredient_checks"], int(value == 1))
                self.assertEqual(sum(x["type"] == "ingredient" for x in slots["bridge"]["locations"]), count)
                self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_default_off(self):
        self.world_setup(seed=184)
        self.assertFalse(any(x["type"] == "ingredient" for x in self.world.active_locations))

    def test_yaml_choices(self):
        for text, value, display in (("off", 0, "Off"), ("Ingredientsanity", 1, "Ingredientsanity"),
                                     ("ingredientsanity_plus", 2, "Ingredientsanity+"),
                                     ("Ingredientsanity+", 2, "Ingredientsanity+")):
            parsed = Ingredients.from_any(text)
            self.assertEqual(parsed.value, value)
            self.assertEqual(parsed.current_option_name, display)
        with self.assertRaises(KeyError):
            Ingredients.from_any("junk")


class TestIngredientSpots(KH3WorldIntegrityMixin, KH3TestBase):
    options = {"ingredients": "ingredientsanity_plus"}

    def test_early_monstropolis_pickup_relies_on_chest_area(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        spot = self.get_location_by_source("IngredientSpot", "MI_01_PF_001")
        chest = self.get_location_by_source("TreasureMI", "MI_SBOX_002")
        self.assertFalse(spot.can_reach(state))
        state.collect(self.world.create_item("Happy Gear"))
        self.assertTrue(spot.can_reach(state))
        self.assertFalse(self.get_location_by_source("VBonus", "Vbonus_040").can_reach(state))
        # An opening restriction on the chest must not block the nearby pickup.
        chest.access_rule = lambda state: False
        self.reset_reachability(state)
        self.assertTrue(spot.can_reach(state))

    def test_catalog_dependencies_match_field_sources(self):
        self.assertEqual({key[1] for key in LOCATION_LOGIC_DEPENDENCIES if key[0] == "IngredientSpot"},
                         {spot["id"] for spot in INGREDIENT_SPOTS} - {"TS_02_PF_003", "TS_02_PF_006"})
        for ingredient, worlds in INGREDIENT_WORLD_SOURCES.items():
            known = {spot["id"] for spot in INGREDIENT_SPOTS}
            expected = {("IngredientSpot", key) for keys in worlds.values() for key in keys
                        if key in known and key not in INGREDIENT_JUNK_SPOTS}
            dep = LOCATION_LOGIC_DEPENDENCIES[("Ingredient", ingredient)]
            self.assertFalse(dep.require_all)
            self.assertEqual(set(dep.source_keys), expected)

    def test_reviewed_movement_routes_and_world_entry(self):
        self.world_setup()
        for spot_id, world_items, alternatives in (
            ("BX_02_PF_003", ("Nano Gear",), ("Unlock: Wall Run",)),
            ("BX_02_PF_016", ("Nano Gear",), ("Unlock: Wall Run", "Ability: Doubleflight")),
            ("CA_01_PF_013", ("Wheel of Fate", "Ability: Air Slide"),
             ("Unlock: Wall Run", "Ability: Doubleflight")),
            ("CA_02h_F_013", ("Wheel of Fate", "Ability: Air Slide"), ("Unlock: Wall Run",)),
            ("TS_02_PF_003", ("Favorite Deputy",),
             ("Ability: High Jump", "Ability: Doubleflight", "Unlock: Wall Run")),
        ):
            spot = self.get_location_by_source("IngredientSpot", spot_id)
            for movement in alternatives:
                with self.subTest(spot=spot_id, movement=movement):
                    state = self._state_without_precollected_items()
                    state.collect(self.world.create_item(movement))
                    self.assertFalse(spot.can_reach(state))
                    state = self._state_without_precollected_items()
                    for name in world_items:
                        state.collect(self.world.create_item(name))
                    self.assertFalse(spot.can_reach(state))
                    state.collect(self.world.create_item(movement))
                    self.assertTrue(spot.can_reach(state))

    def test_toy_box_ramp_and_unchanged_vent_anchors(self):
        self.world_setup()
        ramp = self.get_location_by_source("IngredientSpot", "TS_02_PF_006")
        state = self._state_without_precollected_items()
        self.assertFalse(ramp.can_reach(state))
        state.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(ramp.can_reach(state))
        self.assertFalse(self.get_location_by_source("TreasureTS", "TS_SBOX_011").can_reach(state))
        state.collect(self.world.create_item("Ability: Doubleflight"))
        for spot_id in ("TS_02_PF_004", "TS_02_PF_009"):
            spot = self.get_location_by_source("IngredientSpot", spot_id)
            self.assertFalse(spot.can_reach(state))
            both = state.copy()
            both.collect(self.world.create_item("Ability: High Jump"))
            self.assertTrue(spot.can_reach(both))
            wall_run = self._state_without_precollected_items()
            for name in ("Favorite Deputy", "Unlock: Wall Run"):
                wall_run.collect(self.world.create_item(name))
            self.assertTrue(spot.can_reach(wall_run))

    def test_revisit_pickups_depend_on_final_event_access(self):
        self.world_setup()
        state = self.multiworld.get_all_state(False)
        for event_id, spots in {
            "EVENT_KEYBLADE_001": ("HE_03_F_018", "HE_03_PF_001", "HE_03_PF_003",
                                    "HE_03_PF_014", "HE_03_PF_015", "HE_03_PF_024", "HE_03_PF_025"),
            "EVENT_KEYBLADE_004": ("RA_02_PF_017", "RA_02_PF_018", "RA_02_PF_019", "RA_02_PF_020"),
        }.items():
            event = self.get_location_by_source("Event", event_id)
            for spot_id in spots:
                with self.subTest(spot=spot_id):
                    spot = self.get_location_by_source("IngredientSpot", spot_id)
                    self.assertTrue(spot.can_reach(state))
                    with patch.object(event, "access_rule", lambda state: False):
                        self.reset_reachability(state)
                        self.assertFalse(spot.can_reach(state))
                    self.reset_reachability(state)
                    self.assertTrue(spot.can_reach(state))

    def test_isle_of_luck_uses_physical_emblem_movement_without_photo(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        for name in ("Wheel of Fate", "Ability: Air Slide"):
            state.collect(self.world.create_item(name))
        for number in range(1, 8):
            spot = self.get_location_by_source("IngredientSpot", f"CA_02c_F_{number:03}")
            self.assertFalse(spot.can_reach(state))
            high_jump = state.copy()
            high_jump.collect(self.world.create_item("Ability: High Jump"))
            self.assertTrue(spot.can_reach(high_jump))
        self.assertFalse(state.locations_checked)

    def test_twilight_town_spots_need_no_wall_run(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        for name in ("Shooting Star", "Ability: Block", "Ability: Air Slide"):
            state.collect(self.world.create_item(name))
        for data in INGREDIENT_SPOTS:
            if data["world"] == "Twilight Town":
                with self.subTest(spot=data["id"]):
                    self.assertTrue(self.get_location_by_source("IngredientSpot", data["id"]).can_reach(state))

    def test_intermittent_spots_reject_progression_and_useful_for_any_player(self):
        from BaseClasses import Item, ItemClassification

        self.world_setup()
        for spot_id in INGREDIENT_JUNK_SPOTS:
            spot = self.get_location_by_source("IngredientSpot", spot_id)
            with self.subTest(spot=spot_id):
                self.assertTrue(spot.filler_only)
                for owner in (self.player, self.player + 1):
                    self.assertTrue(spot.item_rule(Item("Junk", ItemClassification.filler, 1, owner)))
                    for classification in (ItemClassification.progression, ItemClassification.useful,
                                           ItemClassification.progression | ItemClassification.useful):
                        self.assertFalse(spot.item_rule(Item("Important", classification, 2, owner)))
        self.assertTrue(self.get_location_by_source("IngredientSpot", "BX_02_PF_001").item_rule(
            self.world.create_item("Favorite Deputy")))

    def test_event_sensitive_spots_lock_ethers_and_balance_pool(self):
        self.world_setup()
        for spot_id in ("RA_01_F_005", "RA_01_F_010", "RA_01_F_011"):
            spot = self.get_location_by_source("IngredientSpot", spot_id)
            self.assertTrue(spot.locked)
            self.assertEqual(spot.item.name, "Ether")
            self.assertEqual(spot.item.player, self.player)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_restrictive_fill_keeps_all_intermittent_rewards_junk(self):
        from Fill import distribute_items_restrictive

        self.world_setup(seed=193)
        distribute_items_restrictive(self.multiworld)
        self.assertFalse(self.multiworld.get_unfilled_locations())
        self.assertTrue(self.multiworld.can_beat_game())
        for spot_id in INGREDIENT_JUNK_SPOTS:
            spot = self.get_location_by_source("IngredientSpot", spot_id)
            self.assertFalse(spot.item.advancement or spot.item.useful)
            if spot_id in INGREDIENT_FIXED_REWARDS:
                self.assertEqual(spot.item.name, "Ether")

    def test_native_rates_cover_catalog_and_keep_reported_exceptions(self):
        self.assertEqual(set(INGREDIENT_SPAWN_RATES), {spot["id"] for spot in INGREDIENT_SPOTS})
        self.assertTrue(all(type(rate) is int and 0 <= rate <= 100 for rate in INGREDIENT_SPAWN_RATES.values()))
        for spot_id, rate in INGREDIENT_SPAWN_RATES.items():
            if rate < 100:
                self.assertIn(spot_id, INGREDIENT_JUNK_SPOTS)
        # Authored rates miss other spawn conditions reported in the review.
        self.assertEqual(INGREDIENT_SPAWN_RATES["TS_01_F_001"], 100)
        self.assertIn("TS_01_F_001", INGREDIENT_JUNK_SPOTS)
        self.assertIn("RA_01_F_010", INGREDIENT_FIXED_REWARDS)

    def test_native_catalog_matches_python_catalog(self):
        header = native_project_root() / 'KH3APProbeCpp/kh3ap/features/ingredients/ingredient_spots.h'
        native = re.findall(r'\{L"([^"]+)", L"([^"]+)"\}', header.read_text())
        expected = [(actor, spot["id"]) for spot in INGREDIENT_SPOTS for actor in spot["actors"]]
        self.assertEqual(native, expected)

    def test_spot_catalog_and_generation(self):
        self.world_setup(seed=191)
        checks = [x for x in self.world.active_locations if x["type"] == "ingredient"]
        self.assertEqual(len(checks), 298)
        self.assertEqual({x["source_table"] for x in checks}, {"IngredientSpot"})
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
        self.assertEqual(len({s["id"] for s in INGREDIENT_SPOTS}), 298)
        actors = [actor for s in INGREDIENT_SPOTS for actor in s["actors"]]
        self.assertEqual(len(set(actors)), len(actors))
        self.assertFalse(any("tutorial" in a.lower() for a in actors))
        self.assertEqual(sum(len(s["actors"]) > 1 for s in INGREDIENT_SPOTS), 24)
        slots = self.world.build_runtime_slot_data()
        self.assertEqual(slots["ingredients"], 2)
        self.assertEqual(slots["ingredient_checks"], 0)
        self.assertTrue(all(x.get("bridge_delivery_required") for x in slots["bridge"]["locations"]
                            if x["type"] == "ingredient"))


    def test_existing_ingredient_type_ids_stay_before_spots(self):
        catalog = normalize_locations(load_extracted_data())
        normal = [row for row in catalog if row["source_table"] == "Ingredient"]
        spots = [row for row in catalog if row["source_table"] == "IngredientSpot"]
        self.assertEqual(len(normal), 59)
        self.assertLess(max(location_name_to_id[x["name"]] for x in normal),
                        min(location_name_to_id[x["name"]] for x in spots))


class TestIngredientChestAreaAccess(KH3WorldIntegrityMixin, KH3TestBase):
    options = {"ingredients": "ingredientsanity_plus", "chestsanity": 1,
               "portal_unlocks": "party_members", "start_with_wall_run": 0}

    def test_rapunzel_opens_ingredients_but_not_chests_without_keyblades(self):
        state = self._state_without_precollected_items()
        early = self.get_location_by_source("IngredientSpot", "RA_01_F_001")
        upper = self.get_location_by_source("IngredientSpot", "RA_01_F_029")
        chest = self.get_location_by_source("TreasureRA", "RA_SBOX_007")
        self.assertFalse(early.can_reach(state))
        state.collect(self.world.create_item("Party Member: Rapunzel"))
        self.assertTrue(early.can_reach(state))
        self.assertFalse(upper.can_reach(state))
        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(upper.can_reach(state))
        self.assertFalse(any(state.has(name, self.player) for name in RANDOMIZED_KEYBLADE_ITEM_NAMES))
        for row in self.world.active_locations:
            if row["world"] == "Kingdom of Corona" and row["type"] == "treasure":
                self.assertFalse(self.multiworld.get_location(row["name"], self.player).can_reach(state))
        chest_row = next(row for row in self.world.active_locations if row["name"] == chest.name)
        key = self.world.create_item(chest_row["keyblade_chest_keyblade"])
        state.collect(key)
        self.assertTrue(chest.can_reach(state))
        state.remove(key)
        self.assertFalse(chest.can_reach(state))
        self.assertTrue(early.can_reach(state))

    def test_revisit_ingredients_keep_story_gate_without_keyblades(self):
        state = self._state_without_precollected_items()
        spot = self.get_location_by_source("IngredientSpot", "RA_02_PF_017")
        for name in ("Party Member: Rapunzel", "Unlock: Wall Run"):
            state.collect(self.world.create_item(name))
        self.assertFalse(spot.can_reach(state))
        self.collect_all_but(RANDOMIZED_KEYBLADE_ITEM_NAMES, state)
        self.assertTrue(spot.can_reach(state))
        event = self.get_location_by_source("Event", "EVENT_KEYBLADE_004")
        with patch.object(event, "access_rule", lambda state: False):
            self.reset_reachability(state)
            self.assertFalse(spot.can_reach(state))

    def test_standard_ingredient_uses_hidden_spot_area_without_chest_key(self):
        self.options = {**self.options, "ingredients": "ingredientsanity"}
        # Isolate a field source to exercise the hidden IngredientSpot path.
        with patch.dict(LOCATION_LOGIC_DEPENDENCIES, {
            ("Ingredient", "FST_ITEM35"): require_any_location(("IngredientSpot", "RA_01_F_001")),
        }):
            self.world_setup()
            state = self._state_without_precollected_items()
            ingredient = self.get_location_by_source("Ingredient", "FST_ITEM35")
            self.assertFalse(ingredient.can_reach(state))
            state.collect(self.world.create_item("Party Member: Rapunzel"))
            self.assertTrue(ingredient.can_reach(state))
            self.assertFalse(self.get_location_by_source("TreasureRA", "RA_SBOX_007").can_reach(state))

    def test_tracker_preserves_ingredient_area_and_chest_lock(self):
        from BaseClasses import CollectionState
        from .test_universal_tracker import TestKH3UniversalTracker

        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(
            self, self.world.build_runtime_slot_data())
        state = CollectionState(tracker)
        for item in tracker.precollected_items[self.player]:
            state.remove(item)
        state.collect(tracker.worlds[self.player].create_item("Party Member: Rapunzel"))
        for table, source_id, reachable in (
            ("IngredientSpot", "RA_01_F_001", True),
            ("IngredientSpot", "RA_01_F_029", False),
            ("TreasureRA", "RA_SBOX_007", False),
        ):
            location = self.get_location_by_source(table, source_id)
            self.assertEqual(tracker.get_location(location.name, self.player).can_reach(state), reachable)


class TestIngredientDependenciesWithJunkWorld(KH3WorldIntegrityMixin, KH3TestBase):
    options = {"ingredients": "ingredientsanity_plus", "monstropolis_pool": "junk"}

    def test_junk_anchor_keeps_logic_and_ap_reward(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        spot = self.get_location_by_source("IngredientSpot", "MI_01_PF_001")
        self.assertFalse(spot.can_reach(state))
        state.collect(self.world.create_item("Happy Gear"))
        self.assertTrue(spot.can_reach(state))
        self.assertTrue(any(row["source_table"] == "TreasureMI" for row in self.world.active_locations))
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_nested_dependencies_keep_combat_and_pool_items(self):
        with patch.dict(LOCATION_LOGIC_DEPENDENCIES, {
            ("IngredientSpot", "MI_01_PF_001"): require_all_locations(("TreasureMI", "MI_SBOX_014"))
        }):
            self.world_setup()
            spot = self.get_location_by_source("IngredientSpot", "MI_01_PF_001")
            state = self._state_without_precollected_items()
            for name in ("Happy Gear", "Unlock: Wall Run"):
                state.collect(self.world.create_item(name))
            self.assertFalse(spot.can_reach(state))
            for name in ("Ability: Block", "Ability: Dodge Roll", "Unlock: Magic"):
                state.collect(self.world.create_item(name))
            self.assertTrue(spot.can_reach(state))
            self.assertNotIn("Dream Heartbinder", self.world._required_logic_item_names())


class TestIngredientTypesWithJunkWorld(TestIngredientChecks):
    options = {"ingredients": "ingredientsanity", "monstropolis_pool": "junk"}
