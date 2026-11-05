from itertools import permutations

from BaseClasses import CollectionState, ItemClassification

from ..CharacterUnlocks import (
    CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME,
    DATA_BATTLE_COMPLETION_SOURCE_KEYS,
    ROXAS_REQUIRED_KEYBLADES,
)
from ..Data import classify_runtime_grant_method, resolve_item_runtime_token, resolve_runtime_token_display_name
from ..SlotData import decode_slot_data, encode_slot_data
from ..Logic import CountedRequirement
from ..rule_helpers import requirement_rule
from . import KH3TestBase


class TestCharacterUnlocks(KH3TestBase):
    def test_fixed_local_completion_rewards(self):
        expected = {
            "Re Mind - Unlock Kairi": ("Playable Character: Kairi", "VBonus:VBonus_DLC_015"),
            "Keyblade Graveyard - Unlock Roxas": ("Playable Character: Roxas", "VBonus:Vbonus_075"),
        }
        for name, (item, trigger) in expected.items():
            location = self.multiworld.get_location(name, self.player)
            self.assertTrue(location.locked)
            self.assertEqual((location.item.name, location.item.player), (item, self.player))
            self.assertEqual(location.item.classification, ItemClassification.progression)
            self.assertTrue(location.item_rule(location.item))
            self.assertFalse(any(reward.name == item for reward in self.multiworld.itempool))
            row = next(row for row in self.world.active_locations if row["name"] == name)
            self.assertEqual(row["trigger_bridge_key"], trigger)

    def test_native_tokens_and_slot_data_round_trip(self):
        self.assertIn("Playable Character: Riku", self.world.item_name_to_id)
        for name, token in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME.items():
            self.assertEqual(resolve_item_runtime_token(name), token)
            self.assertEqual(resolve_runtime_token_display_name(token), name)
            self.assertEqual(classify_runtime_grant_method(token), "character_unlock")
        runtime = self.world.build_runtime_slot_data()
        self.assertEqual(decode_slot_data(encode_slot_data(runtime)), runtime)
        bridge = runtime["bridge"]
        for row in bridge["locations"]:
            if row["type"] == "character_unlock" and "trigger_bridge_key" in row:
                self.assertTrue(row["bridge_delivery_required"])
                expected = dict(trigger_bridge_key=row["trigger_bridge_key"],
                                ap_location_id=row["ap_location_id"],
                                ap_location_name=row["ap_location_name"])
                if "required_item_ids" in row:
                    expected["required_item_ids"] = row["required_item_ids"]
                self.assertIn(expected, bridge["conditional_checks"])

    def test_roxas_requires_completion_and_both_keyblades_in_any_order(self):
        reward = self.multiworld.get_location("Keyblade Graveyard - Unlock Roxas", self.player)
        parent = self.get_location_by_source("VBonus", "Vbonus_075")
        original = parent.access_rule
        for order in permutations(("finish", *ROXAS_REQUIRED_KEYBLADES)):
            with self.subTest(order=order):
                state = self.multiworld.get_all_state(False)
                for name in ROXAS_REQUIRED_KEYBLADES:
                    while state.has(name, self.player):
                        state.remove(self.world.create_item(name))
                finished = False
                parent.access_rule = lambda state: finished
                self.reset_reachability(state)
                self.assertFalse(reward.can_reach(state))
                for step, requirement in enumerate(order):
                    if requirement == "finish":
                        finished = True
                    else:
                        state.collect(self.world.create_item(requirement), True)
                    self.reset_reachability(state)
                    self.assertEqual(reward.can_reach(state), step == 2)
        parent.access_rule = original
        bridge = self.world.build_runtime_slot_data()["bridge"]
        row = next(row for row in bridge["locations"] if row["ap_location_name"] == reward.name)
        self.assertEqual(row["required_item_ids"],
                         [self.world.item_name_to_id[name] for name in ROXAS_REQUIRED_KEYBLADES])

    def test_riku_is_fixed_local_reward_for_every_data_fight(self):
        reward = self.multiworld.get_location("Data Battles - Unlock Riku", self.player)
        self.assertTrue(reward.locked)
        self.assertEqual((reward.item.name, reward.item.player), ("Playable Character: Riku", self.player))
        self.assertEqual(reward.item.classification, ItemClassification.progression)
        self.assertFalse(any(item.name == reward.item.name for item in self.multiworld.itempool))
        row = next(row for row in self.world.build_runtime_slot_data()["bridge"]["locations"]
                   if row["ap_location_name"] == reward.name)
        self.assertEqual(row["bridge_key"], "CharacterUnlock:Riku")
        self.assertTrue(row["bridge_delivery_required"])
        self.assertNotIn("trigger_bridge_key", row)
        # Any one missing encounter must prevent Riku, regardless of clear order.
        state = self.multiworld.get_all_state(False)
        self.assertTrue(reward.can_reach(state))
        for table, source in DATA_BATTLE_COMPLETION_SOURCE_KEYS:
            with self.subTest(source=source):
                parent = self.get_location_by_source(table, source)
                original = parent.access_rule
                parent.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertFalse(reward.can_reach(state))
                parent.access_rule = original
                self.reset_reachability(state)
                self.assertTrue(reward.can_reach(state))

    def test_unlock_inherits_final_encounter_rule(self):
        for name, source in (("Re Mind - Unlock Kairi", "VBonus_DLC_015"),
                             ("Keyblade Graveyard - Unlock Roxas", "Vbonus_075")):
            parent = self.get_location_by_source("VBonus", source)
            reward = self.multiworld.get_location(name, self.player)
            state = self.multiworld.get_all_state(False)
            parent.access_rule = lambda state: False
            self.reset_reachability(state)
            self.assertFalse(reward.can_reach(state))
            parent.access_rule = lambda state: True
            self.reset_reachability(state)
            self.assertTrue(reward.can_reach(state))


class TestCharacterUnlocksWithKH02(TestCharacterUnlocks):
    options = {"kh02_enabled": True, "kh02_challenges": False}

    def test_aqua_uses_final_victory_without_optional_challenges(self):
        reward = self.multiworld.get_location("0.2 - Unlock Aqua", self.player)
        self.assertTrue(reward.locked)
        self.assertEqual(reward.item.name, "Playable Character: Aqua")
        row = next(row for row in self.world.active_locations if row["name"] == reward.name)
        self.assertEqual(row["source_table"], "KH02")
        self.assertEqual(row["kh02_flag"], "BattleBonus_DW_dw_10_DevilWave")
        self.assertNotEqual(row["kh02_flag"], "BattleBonus_DW_dw_09_DevilTower")
        self.assertFalse(any(item.name == reward.item.name for item in self.multiworld.itempool))


class TestCharacterUnlocksWithJunkWorlds(KH3TestBase):
    options = {"remind_pool": "junk", "keyblade_graveyard_pool": "junk", "data_battle_rewards_pool": "junk"}

    def test_fixed_rewards_survive_junk_world_settings(self):
        for name in ("Re Mind - Unlock Kairi", "Keyblade Graveyard - Unlock Roxas", "Data Battles - Unlock Riku"):
            location = self.multiworld.get_location(name, self.player)
            self.assertTrue(location.locked)
            self.assertFalse(location.filler_only)
            self.assertTrue(location.item_rule(location.item))
            self.assertTrue(location.can_reach(self.multiworld.get_all_state(False)))


class TestAllyMovement(KH3TestBase):
    options = {"default_base_abilities": False, "start_with_wall_run": False}

    def test_ally_unlock_opens_a_chest_requiring_both_movement_abilities(self):
        chest = self.get_location_by_source("TreasureHE", "HE_SBOX_011")
        for name in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME:
            with self.subTest(character=name):
                state = CollectionState(self.multiworld)
                for item in ("Hero's Origin", "Ability: Superjump", "Ability: Wall Kick"):
                    state.collect(self.world.create_item(item), True)
                self.assertFalse(chest.can_reach(state))
                ally = self.world.create_item(name)
                state.collect(ally, True)
                self.assertTrue(chest.can_reach(state))
                state.remove(ally)
                self.assertFalse(chest.can_reach(state))

    def test_each_ally_supplies_both_movement_abilities_and_removal_revokes_them(self):
        movement = ((frozenset({"Ability: Doubleflight"}), frozenset({"Ability: Air Slide"})),)
        rule = requirement_rule(movement)
        restored = self.world.rule_from_dict(rule.to_dict())
        for name in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME:
            with self.subTest(character=name):
                state = CollectionState(self.multiworld)
                item = self.world.create_item(name)
                for candidate in (rule.resolve(self.world), restored.resolve(self.world)):
                    self.assertFalse(candidate(state))
                    state.collect(item, True)
                    self.assertTrue(candidate(state))
                    state.remove(item)
                    self.assertFalse(candidate(state))

    def test_counted_requirements_count_capabilities_without_double_counting_allies(self):
        abilities = frozenset({"Ability: Doubleflight", "Ability: Air Slide", "Ability: Glide"})
        two = requirement_rule(((CountedRequirement(abilities, 2),),)).resolve(self.world)
        three = requirement_rule(((CountedRequirement(abilities, 3),),)).resolve(self.world)
        state = CollectionState(self.multiworld)
        for name in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME:
            state.collect(self.world.create_item(name), True)
            self.assertTrue(two(state))
            self.assertFalse(three(state))
        state.collect(self.world.create_item("Ability: Air Slide"), True)
        self.assertFalse(three(state))
        state.collect(self.world.create_item("Ability: Glide"), True)
        self.assertTrue(three(state))


class TestAllyCharactersDisabled(TestAllyMovement):
    options = {**TestAllyMovement.options, "ally_characters": False, "kh02_enabled": True}

    def test_disabled_removes_all_awards_and_tracker_preserves_setting(self):
        from .test_universal_tracker import TestKH3UniversalTracker

        self.assertFalse(any(row["type"] == "character_unlock" for row in self.world.active_locations))
        self.assertFalse(any(item.name in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME
                             for item in self.multiworld.get_items()))
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))
        runtime = self.world.build_runtime_slot_data()
        self.assertEqual(runtime["ally_characters"], 0)
        self.assertFalse(any(row["type"] == "character_unlock" for row in runtime["bridge"]["locations"]))
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, runtime)
        self.assertFalse(tracker.worlds[1].options.ally_characters)
        self.assertFalse(any(row["type"] == "character_unlock" for row in tracker.worlds[1].active_locations))

    def test_disabled_accepts_starting_character_rewards(self):
        from test.general import gen_steps, setup_multiworld
        from worlds.AutoWorld import call_all

        starting_allies = {name: 1 for name in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME}
        for option in ("start_inventory", "start_inventory_from_pool"):
            with self.subTest(option=option):
                multiworld = setup_multiworld(type(self.world), steps=("generate_early",), seed=357,
                                             options={**self.options, option: starting_allies})
                world = multiworld.worlds[self.player]
                # Main precollects both starting-inventory options after generate_early.
                for name in getattr(world.options, option).value:
                    multiworld.push_precollected(world.create_item(name))
                for step in gen_steps[1:]:
                    call_all(multiworld, step)
                state = CollectionState(multiworld)
                self.assertTrue(state.has_all(starting_allies, self.player))
                self.assertFalse(any(row["type"] == "character_unlock" for row in world.active_locations))
                self.assertFalse(any(item.name in starting_allies for item in multiworld.itempool))
                self.assertEqual(len(multiworld.itempool), len(multiworld.get_unfilled_locations(self.player)))
                for name, token in CHARACTER_UNLOCK_RUNTIME_TOKEN_BY_ITEM_NAME.items():
                    self.assertEqual(resolve_item_runtime_token(name), token)
                    self.assertEqual(classify_runtime_grant_method(token), "character_unlock")
                movement = ((frozenset({"Ability: Doubleflight"}), frozenset({"Ability: Air Slide"})),)
                self.assertTrue(requirement_rule(movement).resolve(world)(state))
