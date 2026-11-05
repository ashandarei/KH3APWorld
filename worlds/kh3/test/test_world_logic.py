from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from BaseClasses import CollectionState, Item, ItemClassification

from .. import RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS
from ..Battlegates import BATTLEGATE_COMPLETION_SOURCES_BY_WORLD, BATTLEGATE_SOURCE_ID_REGION_LOCKS
from ..Bridge import build_bridge_slot_data
from ..Data import (
    CHEST_LOGIC_REQUIREMENTS,
    DEFAULT_BASE_ABILITY_ITEM_NAMES,
    EVENT_LOGIC_REQUIREMENTS,
    HEART_PIECE_GOAL_COUNT,
    HEART_PIECE_ITEM_NAME,
    HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT,
    KEYBLADE_STYLE_ITEM_NAMES,
    LARGE_MUNNY_POUCH_ITEM_NAME,
    LARGE_MUNNY_POUCH_RUNTIME_TOKEN,
    LEVEL_UP_XP_SOURCE_REGION_NAMES,
    LOCATION_LOGIC_DEPENDENCIES,
    LUCKY_EMBLEM_COUNTS_BY_WORLD,
    LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD,
    LUCKY_EMBLEM_TOTAL_COUNT,
    MUNNY_POUCH_RUNTIME_TOKENS,
    PORTAL_KEYBLADE_ITEM_NAMES,
    PORTAL_KEYBLADE_REGION_LOCKS,
    SCALED_STORY_END_LEVEL,
    STARTING_PORTAL_KEYBLADE_ITEM_NAMES,
    STYLE_CHANGE_UNLOCK_ITEM,
    THE_CARIBBEAN_PORTAL_LOGIC_REQUIREMENTS,
    TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES,
    VICTORY_BONUS_LOGIC_REQUIREMENTS,
    WORLD_DIFFICULTY_TIER_BY_REGION,
    WORLD_DIFFICULTY_TIERS,
    build_item_name_to_id,
    filter_locations_for_options,
    goal_required_item_names,
    load_extracted_data,
    normalize_locations,
    required_level_up_xp_source_count,
)
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    LUCKY_MARK_LIMIT_FILLER_TOKENS,
    SUPPRESSED_ALWAYS_ON_EVENT_TOKEN,
    configure_lucky_mark_batch_rows,
    export_randomized_options_from_bridge_data,
    read_lucky_emblem_limit_for_export,
    select_lucky_mark_limit_filler_token,
    suppress_always_on_event_rewards,
    suppress_level_up_path_rewards,
    suppress_lucky_mark_rewards_above_limit,
)
from ..Items import classify_item
from ..Locations import COMPLETION_LOCATION_NAME
from ..MelodyOfMemory import music_chest_bonus_name
from ..Logic import (
    CountedRequirement,
    normalize_requirement_token,
    parse_requirement_summary,
    require_all_locations,
    require_any_location,
    unknown_location_dependency_source_keys,
    unknown_requirement_item_names,
    unknown_requirement_source_keys,
    validate_location_dependency_cycles,
)
from ..LogicData import ALL_LOGIC_RULES, LINK_HEARTBINDER_ITEM_NAMES
from ..Options import KH3LocationPoolMode
from ..Regions import HUB_REGION_NAME, KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME
from . import KH3TestBase
from .test_helpers import KH3WorldIntegrityMixin


class TestKH3WorldIntegrity(KH3WorldIntegrityMixin, KH3TestBase):
    def test_opening_darkside_rewards_need_no_olympus_keyblade(self) -> None:
        self.world_setup()
        state = self._state_without_precollected_items()
        opening = [row for row in self.world.active_locations
                   if (row["source_table"], row["source_id"]) == ("VBonus", "Vbonus_082")
                   or row.get("trigger_bridge_key") == "VBonus:Vbonus_082"]
        self.assertEqual(len(opening), 3)
        for row in opening:
            with self.subTest(location=row["name"]):
                self.assertTrue(self.multiworld.get_location(row["name"], self.player).can_reach(state))
        cliff_row = next(row for row in self.world.active_locations
                         if (row["source_table"], row["source_id"]) == ("VBonus", "Vbonus_001"))
        cliff = self.multiworld.get_location(cliff_row["name"], self.player)
        self.assertFalse(cliff.can_reach(state))
        state.collect(self.world.create_item("Hero's Origin"))
        self.assertTrue(cliff.can_reach(state))

    def _collect_higher_tier_gate(self, state: CollectionState) -> None:
        for tier_one_keyblade in ("Hero's Origin", "Happy Gear", "Crystal Snow"):
            state.collect(self.world.create_item(tier_one_keyblade))

    def test_all_battlegates_are_included_in_both_pool_modes(self) -> None:
        expected = {f"EVENT_REPORT_{number:03}{suffix}"
                    for number in range(1, 14) for suffix in ("a", "b")}
        expected.add("EVENT_REPORT_014")
        expected.add("EVENT_REPORT_000")
        for mode in (KH3LocationPoolMode.option_randomize, KH3LocationPoolMode.option_junk):
            with self.subTest(mode=mode):
                self.options = {"battlegates_pool": mode}
                self.world_setup()
                rows = [row for row in self.world.active_locations
                        if row["world"] in {"Battlegate Reports", "Battlegate Rewards"}]
                self.assertEqual({row["source_id"] for row in rows}, expected)
                self.assertEqual(Counter(row["world"] for row in rows),
                                 {"Battlegate Reports": 13, "Battlegate Rewards": 15})
                exported = self.world.build_runtime_slot_data()["bridge"]["locations"]
                self.assertEqual({row["source_id"] for row in exported
                                  if row["world"] in {"Battlegate Reports", "Battlegate Rewards"}}, expected)
                self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_battlegates_require_their_physical_world(self) -> None:
        self.world_setup()

        source_id_to_location = {
            location_data["source_id"]: self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
            if location_data["source_id"] in BATTLEGATE_SOURCE_ID_REGION_LOCKS
        }

        for source_id, region_name in BATTLEGATE_SOURCE_ID_REGION_LOCKS.items():
            with self.subTest(source_id=source_id, region_name=region_name):
                location = source_id_to_location[source_id]
                required_keyblade = PORTAL_KEYBLADE_REGION_LOCKS[region_name]
                state = self.multiworld.get_all_state(False)
                self.assertTrue(location.can_reach(state))
                while state.has(required_keyblade, self.player):
                    state.remove(self.world.create_item(required_keyblade))
                self.assertFalse(self.multiworld.get_region(region_name, self.player).can_reach(state))
                self.assertFalse(location.can_reach(state))
                state.collect(self.world.create_item(required_keyblade))
                self.assertTrue(location.can_reach(state))

    def test_battlegates_require_only_their_own_world_story_completion(self) -> None:
        self.world_setup()
        state = self.multiworld.get_all_state(False)
        for world_name, completion_source in BATTLEGATE_COMPLETION_SOURCES_BY_WORLD.items():
            completion = self.get_location_by_source(*completion_source)
            with self.subTest(world=world_name), patch.object(completion, "access_rule", return_value=False):
                self.reset_reachability(state)
                self.assertTrue(self.multiworld.get_region(world_name, self.player).can_reach(state))
                for source_id, gate_world in BATTLEGATE_SOURCE_ID_REGION_LOCKS.items():
                    gate = self.get_location_by_source("Event", source_id)
                    self.assertEqual(gate.can_reach(state), gate_world != world_name, source_id)
            self.reset_reachability(state)

    def test_battlegates_keep_combat_requirements_after_world_completion(self) -> None:
        self.world_setup()
        state = self.multiworld.get_all_state(False)
        while state.has("Magic: Cure", self.player):
            state.remove(self.world.create_item("Magic: Cure"))
        for source_id, world_name in BATTLEGATE_SOURCE_ID_REGION_LOCKS.items():
            completion = self.get_location_by_source(*BATTLEGATE_COMPLETION_SOURCES_BY_WORLD[world_name])
            with self.subTest(source=source_id), patch.object(completion, "access_rule", return_value=True):
                self.reset_reachability(state)
                self.assertFalse(self.get_location_by_source("Event", source_id).can_reach(state))
            self.reset_reachability(state)

    def test_battlegate_four_requires_toy_box_story_completion(self) -> None:
        self.world_setup()
        locations = [
            self.get_location_by_source("Event", source_id)
            for source_id in ("EVENT_REPORT_004a", "EVENT_REPORT_004b")
        ]
        state = self._state_without_precollected_items()
        for item_name in (
            "Favorite Deputy", "Ability: Block", "Ability: Air Slide",
            "Ability: Dodge Roll", "Magic: Cure", "Unlock: Magic",
            "Ability: High Jump", "Ability: Doubleflight",
        ):
            state.collect(self.world.create_item(item_name))
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(location.can_reach(state) for location in locations))

        completion = self.get_location_by_source("Event", "EVENT_KEYBLADE_003")
        original_rule = completion.access_rule
        try:
            completion.access_rule = lambda state: False
            self.reset_reachability(state)
            self.assertTrue(all(not location.can_reach(state) for location in locations))
        finally:
            completion.access_rule = original_rule
            self.reset_reachability(state)

    def test_lucky_emblems_are_controlled_by_pool_mode(self) -> None:
        self.world_setup()

        self.assertTrue(any(location["source_table"] == "LuckyMark" for location in self.world.active_locations))
        slot_data = self.world.build_runtime_slot_data()
        self.assertTrue(
            any(location["source_table"] == "LuckyMark" for location in slot_data["bridge"]["locations"])
        )

        options = self.multiworld.worlds[self.player].options
        options.lucky_emblems_pool.value = KH3LocationPoolMode.option_randomize
        all_locations = normalize_locations(load_extracted_data())
        filtered = filter_locations_for_options(all_locations, options)

        self.assertTrue(any(location["source_table"] == "LuckyMark" for location in filtered))
        bridge_data = build_bridge_slot_data(filtered, options.heart_piece_count.value)
        self.assertTrue(
            any(location["source_table"] == "LuckyMark" for location in bridge_data["locations"])
        )

    def test_level_up_bridge_metadata_omits_retired_path_rewards(self) -> None:
        level_location = next(
            location
            for location in normalize_locations(load_extracted_data())
            if location["type"] == "level_up"
        ).copy()
        level_location["vanilla_items"] = {
            "TypeA": "Ability: Combo Plus",
            "TypeB": "Ability: Fire Boost",
            "TypeC": "Ability: Air Combo Plus",
        }
        level_location["vanilla_item_names"] = dict(level_location["vanilla_items"])

        bridge_data = build_bridge_slot_data([level_location], 7)
        bridge_location = bridge_data["locations"][0]

        self.assertTrue(bridge_location["bridge_delivery_required"])
        self.assertNotIn("vanilla_items", bridge_location)
        self.assertNotIn("vanilla_item_names", bridge_location)

    def test_lucky_emblem_limit_excludes_higher_milestones_from_ap(self) -> None:
        self.options = {
            "lucky_emblems_pool": KH3LocationPoolMode.option_randomize,
            "lucky_emblem_limit": 20,
        }
        self.world_setup()

        active_milestones = {
            int(location["source_id"])
            for location in self.world.active_locations
            if location["source_table"] == "LuckyMark"
        }
        bridge_milestones = {
            int(location["source_id"])
            for location in self.world.build_runtime_slot_data()["bridge"]["locations"]
            if location["source_table"] == "LuckyMark"
        }

        self.assertEqual(active_milestones, {1, 3, 5, 10, 15, 20})
        self.assertEqual(bridge_milestones, active_milestones)
        self.assertNotIn("Lucky Emblems - Milestone 30", {
            location.name
            for location in self.multiworld.get_locations(self.player)
        })

    def test_one_lucky_emblem_per_check_creates_ninety_milestones(self) -> None:
        self.options = {
            "lucky_emblems_pool": KH3LocationPoolMode.option_randomize,
            "lucky_emblem_limit": 90,
            "lucky_emblems_per_check": 1,
        }
        self.world_setup()

        active_milestones = {
            int(location["source_id"])
            for location in self.world.active_locations
            if location["source_table"] == "LuckyMark"
        }
        bridge_milestones = {
            int(location["source_id"])
            for location in self.world.build_runtime_slot_data()["bridge"]["locations"]
            if location["source_table"] == "LuckyMark"
        }

        self.assertEqual(active_milestones, set(range(1, 91)))
        self.assertEqual(bridge_milestones, active_milestones)

    def test_three_lucky_emblems_per_check_creates_thirty_milestones(self) -> None:
        self.options = {
            "lucky_emblems_pool": KH3LocationPoolMode.option_randomize,
            "lucky_emblem_limit": 90,
            "lucky_emblems_per_check": 3,
        }
        self.world_setup()

        active_milestones = {
            int(location["source_id"])
            for location in self.world.active_locations
            if location["source_table"] == "LuckyMark"
        }

        self.assertEqual(active_milestones, set(range(3, 91, 3)))
        self.assertEqual(len(active_milestones), 30)

    def test_lucky_emblem_counts_match_vanilla_world_totals(self) -> None:
        self.assertEqual(
            LUCKY_EMBLEM_COUNTS_BY_WORLD,
            {
                "Olympus": 12,
                "Twilight Town": 9,
                "Toy Box": 11,
                "Kingdom of Corona": 9,
                "Monstropolis": 11,
                "100 Acre Wood": 3,
                "Arendelle": 11,
                "The Caribbean": 13,
                "San Fransokyo": 11,
                "Keyblade Graveyard": 0,
                "Re Mind": 0,
                "Data Battles": 0,
                "Unreality": 0,
            },
        )
        self.assertEqual(LUCKY_EMBLEM_TOTAL_COUNT, 90)
        self.assertEqual(
            sum(len(requirements) for requirements in LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD.values()),
            90,
        )
        self.assertEqual(len(LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD["Olympus"]), 12)
        self.assertTrue(LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD["Olympus"][0])
        self.assertEqual(
            LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD["100 Acre Wood"],
            ((), (), ()),
        )

    def test_magic_logic_routes_require_magic_unlock(self) -> None:
        requirement_maps = {
            "chests": CHEST_LOGIC_REQUIREMENTS,
            "victory bonuses": VICTORY_BONUS_LOGIC_REQUIREMENTS,
            "events": EVENT_LOGIC_REQUIREMENTS,
        }

        for label, source_requirements in requirement_maps.items():
            for source_key, requirement_routes in source_requirements.items():
                for route in requirement_routes:
                    has_magic = any(
                        item_name.startswith("Magic: ")
                        for item_names in route
                        for item_name in item_names
                    )
                    if not has_magic:
                        continue

                    with self.subTest(label=label, source_key=source_key):
                        self.assertTrue(any("Unlock: Magic" in item_names for item_names in route))

        for world_name, spot_requirements in LUCKY_EMBLEM_LOGIC_REQUIREMENTS_BY_WORLD.items():
            for spot_index, requirement_routes in enumerate(spot_requirements):
                for route in requirement_routes:
                    has_magic = any(
                        item_name.startswith("Magic: ")
                        for item_names in route
                        for item_name in item_names
                    )
                    if not has_magic:
                        continue

                    with self.subTest(label="lucky emblems", world=world_name, spot_index=spot_index):
                        self.assertTrue(any("Unlock: Magic" in item_names for item_names in route))

    def test_public_logic_rules_reference_known_sources_and_items(self) -> None:
        data = load_extracted_data()
        valid_source_keys = {
            (location["source_table"], location["source_id"])
            for location in normalize_locations(data)
        }
        requirement_maps = {
            "chests": CHEST_LOGIC_REQUIREMENTS,
            "victory bonuses": VICTORY_BONUS_LOGIC_REQUIREMENTS,
            "events": EVENT_LOGIC_REQUIREMENTS,
        }

        self.assertTrue(ALL_LOGIC_RULES)
        self.assertEqual(
            unknown_requirement_source_keys(requirement_maps.values(), valid_source_keys),
            set(),
        )
        self.assertEqual(
            unknown_requirement_item_names(requirement_maps.values(), build_item_name_to_id(data)),
            set(),
        )
        self.assertEqual(
            unknown_location_dependency_source_keys(LOCATION_LOGIC_DEPENDENCIES, valid_source_keys),
            set(),
        )

    def test_location_dependency_helpers_reject_invalid_and_circular_rules(self) -> None:
        first = ("TreasureMI", "MI_SBOX_014")
        second = ("VBonus", "Vbonus_040")

        with self.assertRaisesRegex(ValueError, "at least one prerequisite"):
            require_all_locations()
        with self.assertRaisesRegex(ValueError, "cannot repeat"):
            require_any_location(second, second)
        with self.assertRaisesRegex(ValueError, "Circular KH3 location dependency"):
            validate_location_dependency_cycles({
                first: require_all_locations(second),
                second: require_any_location(first),
            })

    def test_anyof_logic_requires_distinct_items(self) -> None:
        requirements = parse_requirement_summary("AnyOf2(Block/Air Slide/Dodge Roll)")

        self.assertEqual(len(requirements), 1)
        self.assertEqual(len(requirements[0]), 1)
        counted_requirement = requirements[0][0]
        self.assertIsInstance(counted_requirement, CountedRequirement)
        self.assertEqual(counted_requirement.count, 2)
        self.assertEqual(
            counted_requirement.item_names,
            frozenset({"Ability: Block", "Ability: Air Slide", "Ability: Dodge Roll"}),
        )

        with self.assertRaisesRegex(ValueError, "asks for 4 of only 3 items"):
            parse_requirement_summary("AnyOf4(Block/Air Slide/Dodge Roll)")

    def test_prefixed_ability_magic_and_unlock_aliases(self) -> None:
        aliases = {
            "Aerial Dive": "Ability: Aerial Dive",
            "Fire": "Magic: Fire",
            "Style Change": "Unlock: Style Change",
            "Airstep": "Unlock: Airstep",
        }

        for alias, item_name in aliases.items():
            with self.subTest(alias=alias):
                self.assertEqual(normalize_requirement_token(alias), item_name)
                self.assertEqual(normalize_requirement_token(alias.lower()), item_name)

        for token, item_name in {
            "ability: aerial dive": "Ability: Aerial Dive",
            "MAGIC : FIRE": "Magic: Fire",
            "unlock: style change": "Unlock: Style Change",
        }.items():
            with self.subTest(token=token):
                self.assertEqual(normalize_requirement_token(token), item_name)

    def test_unknown_requirement_alias_is_preserved(self) -> None:
        self.assertEqual(
            normalize_requirement_token("not an ability or unlock"),
            "not an ability or unlock",
        )


    def test_data_battle_event_logic_matches_per_boss_requirements(self) -> None:
        common_requirements = parse_requirement_summary(
            "Block + Air Slide + Cure + Dodge Roll + Second Chance + Withstand Combo + Glide + "
            "AnyOf1(Counter Slash/Counter Kick)"
        )
        marluxia_requirements = parse_requirement_summary(
            "Block + Air Slide + Cure + Dodge Roll + Second Chance + Withstand Combo + Glide + "
            "AnyOf1(Counter Slash/Counter Kick) + Fire"
        )
        saix_requirements = parse_requirement_summary(
            "Block + Air Slide + Cure + Dodge Roll + Second Chance + Withstand Combo + Glide + "
            "AnyOf1(Counter Slash/Counter Kick) + Blizzard"
        )

        for event_number in range(1, 14):
            source_key = ("Event", f"EVENT_DATAB_{event_number:03d}")
            expected = (
                marluxia_requirements if event_number == 7
                else saix_requirements if event_number == 8
                else common_requirements
            )
            with self.subTest(source_key=source_key):
                self.assertEqual(EVENT_LOGIC_REQUIREMENTS[source_key], expected)

        counter_requirement = common_requirements[0][-2]
        self.assertIsInstance(counter_requirement, CountedRequirement)
        self.assertEqual(counter_requirement.count, 1)
        self.assertEqual(
            counter_requirement.item_names,
            frozenset({"Ability: Counter Slash", "Ability: Counter Kick"}),
        )

    def test_lucky_emblem_milestones_require_reachable_spot_counts(self) -> None:
        self.options = {
            **self.options,
            "lucky_emblems_pool": KH3LocationPoolMode.option_randomize,
            "lucky_emblem_limit": 90,
        }
        self.world_setup()

        milestone_1 = self.multiworld.get_location("Lucky Emblems - Milestone 1", self.player)
        milestone_3 = self.multiworld.get_location("Lucky Emblems - Milestone 3", self.player)
        milestone_5 = self.multiworld.get_location("Lucky Emblems - Milestone 5", self.player)
        milestone_10 = self.multiworld.get_location("Lucky Emblems - Milestone 10", self.player)
        milestone_15 = self.multiworld.get_location("Lucky Emblems - Milestone 15", self.player)
        milestone_20 = self.multiworld.get_location("Lucky Emblems - Milestone 20", self.player)
        milestone_25 = self.multiworld.get_location("Lucky Emblems - Milestone 25", self.player)
        milestone_30 = self.multiworld.get_location("Lucky Emblems - Milestone 30", self.player)
        milestone_45 = self.multiworld.get_location("Lucky Emblems - Milestone 45", self.player)
        milestone_50 = self.multiworld.get_location("Lucky Emblems - Milestone 50", self.player)
        milestone_60 = self.multiworld.get_location("Lucky Emblems - Milestone 60", self.player)
        milestone_65 = self.multiworld.get_location("Lucky Emblems - Milestone 65", self.player)
        milestone_70 = self.multiworld.get_location("Lucky Emblems - Milestone 70", self.player)
        milestone_90 = self.multiworld.get_location("Lucky Emblems - Milestone 90", self.player)

        start = self._state_without_precollected_items()
        self.assertTrue(milestone_1.can_reach(start))
        self.assertTrue(milestone_3.can_reach(start))
        self.assertFalse(milestone_5.can_reach(start))

        toy_box = self._state_without_precollected_items()
        toy_box.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(milestone_5.can_reach(toy_box))
        self.assertTrue(milestone_10.can_reach(toy_box))
        self.assertFalse(milestone_15.can_reach(toy_box))

        tier_one = self._state_without_precollected_items()
        for item_name in ("Hero's Origin", "Shooting Star"):
            tier_one.collect(self.world.create_item(item_name))
        self.assertFalse(milestone_10.can_reach(tier_one))

        tier_one.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(milestone_15.can_reach(tier_one))
        self.assertFalse(milestone_20.can_reach(tier_one))

        through_monstropolis = self._state_without_precollected_items()
        for item_name in (
            "Hero's Origin",
            "Shooting Star",
            "Favorite Deputy",
            "Ever After",
            "Happy Gear",
            "Ability: Block",
            "Ability: Air Slide",
        ):
            through_monstropolis.collect(self.world.create_item(item_name))
        self.assertTrue(milestone_25.can_reach(through_monstropolis))
        self.assertFalse(milestone_30.can_reach(through_monstropolis))
        self.assertFalse(milestone_60.can_reach(through_monstropolis))
        self.assertFalse(milestone_65.can_reach(through_monstropolis))

        through_arendelle = self._state_without_precollected_items()
        for item_name in (
            "Hero's Origin",
            "Shooting Star",
            "Favorite Deputy",
            "Ever After",
            "Happy Gear",
            "Crystal Snow",
        ):
            through_arendelle.collect(self.world.create_item(item_name))
        self.assertFalse(milestone_30.can_reach(through_arendelle))

        for item_name in (
            "Unlock: Wall Run",
            "Ability: Block",
            "Ability: Pole Spin",
            "Ability: Air Slide",
            "Ability: High Jump",
        ):
            through_arendelle.collect(self.world.create_item(item_name))

        self.assertTrue(milestone_65.can_reach(through_arendelle))
        self.assertFalse(milestone_70.can_reach(through_arendelle))
        self.assertFalse(milestone_90.can_reach(through_arendelle))

        all_emblem_worlds = self._state_without_precollected_items()
        for item_name in (
            "Hero's Origin",
            "Shooting Star",
            "Favorite Deputy",
            "Ever After",
            "Happy Gear",
            "Crystal Snow",
            "Wheel of Fate",
            "Nano Gear",
            "Ability: Block",
            "Ability: Air Slide",
        ):
            all_emblem_worlds.collect(self.world.create_item(item_name))
        self.assertTrue(milestone_45.can_reach(all_emblem_worlds))
        self.assertFalse(milestone_50.can_reach(all_emblem_worlds))

        for item_name in (
            "Unlock: Wall Run",
            "Ability: Block",
            "Ability: Pole Spin",
            "Ability: Air Slide",
            "Ability: High Jump",
            "Ability: Glide",
        ):
            all_emblem_worlds.collect(self.world.create_item(item_name))
        self.assertTrue(milestone_90.can_reach(all_emblem_worlds))

    def test_goal_required_items_remain_available_with_junk_unreality(self) -> None:
        self.world_setup()

        self.assertTrue(any(location["world"] == "Unreality" for location in self.world.active_locations))

        generated_item_names = {
            item.name
            for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        }
        self.assertTrue(
            goal_required_item_names(self.world.options.heart_piece_count.value).issubset(generated_item_names)
        )

    def test_level_up_locations_are_extracted_and_limited(self) -> None:
        extracted_data = load_extracted_data()
        all_locations = normalize_locations(extracted_data)
        level_locations = [location for location in all_locations if location["type"] == "level_up"]

        self.assertEqual(len(level_locations), 98)
        self.assertEqual(level_locations[0]["name"], "Level Ups - Sora Level 2")
        self.assertEqual(level_locations[-1]["name"], "Level Ups - Sora Level 99")
        self.assertEqual(
            set(level_locations[0]["vanilla_items"].values()),
            {"ETresAbilityKind::NONE"},
        )

        self.world_setup()
        active_level_ids = {
            location["source_id"]
            for location in self.world.active_locations
            if location["type"] == "level_up"
        }
        self.assertIn("35", active_level_ids)
        self.assertIn("36", active_level_ids)

        options = self.multiworld.worlds[self.player].options
        options.level_up_limit.value = 4
        limited = filter_locations_for_options(all_locations, options)
        limited_level_ids = {
            location["source_id"]
            for location in limited
            if location["type"] == "level_up"
        }
        self.assertEqual(limited_level_ids, {str(level) for level in range(2, 100)})

    def test_completion_location_requires_goal_items(self) -> None:
        self.world_setup()

        completion_location = self.multiworld.get_location(COMPLETION_LOCATION_NAME, self.player)
        final_door_region = self.multiworld.get_region("Final Door", self.player)
        state = CollectionState(self.multiworld)
        self.assertFalse(final_door_region.can_reach(state))
        self.assertFalse(completion_location.can_reach(state))

        for _ in range(7):
            state.collect(self.world.create_item("Heart Piece"))

        self.assertTrue(final_door_region.can_reach(state))
        self.assertTrue(completion_location.can_reach(state))

    def test_arendelle_starting_chests_and_melody_bonuses_forbid_progression(self) -> None:
        self.options = {**self.options, "default_base_abilities": 0,
                        "music_chest_count": 8, "music_chest_vanilla_conversion": 128}
        self.world_setup()
        state = self.multiworld.get_all_state(False)
        chests = [self.get_location_by_source("TreasureFZ", source_id)
                  for source_id in ("FZ_LBOX_001", "FZ_SBOX_001", "FZ_SBOX_002", "FZ_LBOX_003")]
        chests.append(self.get_location_by_source("MusicChest", "AP_LBOX_008"))
        bonuses = [self.multiworld.get_location(music_chest_bonus_name(chest.name), self.player)
                   for chest in chests if any(row.get("music_chest_parent_location_name") == chest.name
                                             for row in self.world.active_locations)]
        self.assertTrue(bonuses)
        while state.has("Ability: Pole Spin", self.player):
            state.remove(self.world.create_item("Ability: Pole Spin"))
        self.assertTrue(self.multiworld.get_region("Arendelle", self.player).can_reach(state))
        for location in chests + bonuses:
            with self.subTest(location=location.name):
                self.assertTrue(location.can_reach(state))
                for classification in (ItemClassification.filler, ItemClassification.useful,
                                       ItemClassification.trap, ItemClassification.progression,
                                       ItemClassification.progression_skip_balancing,
                                       ItemClassification.progression | ItemClassification.useful,
                                       ItemClassification.progression | ItemClassification.trap):
                    for item_player in (self.player, self.player + 1):
                        item = Item("Test reward", classification, 1, item_player)
                        self.assertEqual(location.item_rule(item), not item.advancement)
        while state.has("Unlock: Wall Run", self.player):
            state.remove(self.world.create_item("Unlock: Wall Run"))
        self.assertFalse(chests[3].can_reach(state))
        for location in chests[:3] + [chests[-1]]:
            self.assertTrue(location.can_reach(state))

    def test_problem_chests_allow_bridge_delivered_ability_and_vbonus_rewards(self) -> None:
        self.world_setup()

        location = self.get_location_by_source("TreasureFZ", "FZ_SBOX_005")

        self.assertTrue(location.item_rule(self.world.create_item("Ability: Attraction Enhancer")))
        self.assertTrue(location.item_rule(self.world.create_item("Magic: Fire")))
        self.assertTrue(location.item_rule(self.world.create_item("Bonus: HP +5")))
        self.assertTrue(location.item_rule(self.world.create_item("Potion")))

    def test_problem_chests_allow_remote_items(self) -> None:
        self.world_setup()

        location = self.get_location_by_source("TreasureFZ", "FZ_SBOX_005")
        remote_item = self.world.create_item("Ability: Attraction Enhancer")
        remote_item.player = self.player + 1

        self.assertTrue(location.item_rule(remote_item))

    def test_regions_include_hub_and_final_door(self) -> None:
        self.world_setup()

        hub = self.multiworld.get_region(HUB_REGION_NAME, self.player)
        final_door = self.multiworld.get_region("Final Door", self.player)
        self.assertIsNotNone(hub)
        self.assertIsNotNone(final_door)

        exit_names = {entrance.name for entrance in hub.exits}
        self.assertIn("To Final Door", exit_names)
        self.assertIn("To Olympus", exit_names)

    def test_portal_keyblades_classify_as_progression(self) -> None:
        for item_name in PORTAL_KEYBLADE_ITEM_NAMES:
            self.assertEqual(classify_item(item_name), ItemClassification.progression)

    def test_portal_worlds_require_keyblade_locks(self) -> None:
        self.world_setup()
        existing_region_names = {
            region.name
            for region in self.multiworld.get_regions(self.player)
        }

        for region_name, keyblade_name in PORTAL_KEYBLADE_REGION_LOCKS.items():
            with self.subTest(region=region_name, keyblade=keyblade_name):
                if region_name not in existing_region_names:
                    continue
                region = self.multiworld.get_region(region_name, self.player)
                state = self._state_without_precollected_items()

                self.assertFalse(region.can_reach(state))
                state.collect(self.world.create_item(keyblade_name))
                if WORLD_DIFFICULTY_TIER_BY_REGION.get(region_name, 1) > 1:
                    for tier_one_keyblade in sorted(TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES)[
                        :HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT
                    ]:
                        state.collect(self.world.create_item(tier_one_keyblade))
                if region_name == "Twilight Town":
                    state.collect(self.world.create_item("Ability: Block"))
                    state.collect(self.world.create_item("Ability: Air Slide"))
                if region_name == "The Caribbean":
                    state.collect(self.world.create_item("Ability: Air Slide"))
                if region_name == "Keyblade Graveyard":
                    state.collect(self.world.create_item("Unlock: Attraction Flow"))
                self.assertTrue(region.can_reach(state))

    def test_world_difficulty_tiers_are_renumbered_after_tier_one_expansion(self) -> None:
        self.assertEqual(
            WORLD_DIFFICULTY_TIERS,
            {
                1: frozenset({
                    "Olympus", "Toy Box", "100 Acre Wood",
                    "Monstropolis", "Kingdom of Corona", "The Caribbean", "Arendelle",
                    "San Fransokyo",
                }),
                2: frozenset({"Twilight Town", "Keyblade Graveyard", "Re Mind", "Dark World"}),
                3: frozenset({"Data Battles"}),
                4: frozenset({"Unreality"}),
            },
        )

    def test_data_battles_require_three_distinct_tier_one_keyblades(self) -> None:
        self.world_setup()

        cases = [
            ("Data Battles", "Oathkeeper"),
        ]
        existing_region_names = {
            region.name
            for region in self.multiworld.get_regions(self.player)
        }

        tier_one_keyblades = sorted(TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES)
        self.assertEqual(HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT, 3)

        for region_name, own_keyblade in cases:
            with self.subTest(region=region_name):
                if region_name not in existing_region_names:
                    continue

                region = self.multiworld.get_region(region_name, self.player)
                self.assertGreater(WORLD_DIFFICULTY_TIER_BY_REGION[region_name], 1)

                own_only = self._state_without_precollected_items()
                own_only.collect(self.world.create_item(own_keyblade))
                self.assertFalse(region.can_reach(own_only))

                two_tier_one = self._state_without_precollected_items()
                two_tier_one.collect(self.world.create_item(own_keyblade))
                for tier_one_keyblade in tier_one_keyblades[:2]:
                    two_tier_one.collect(self.world.create_item(tier_one_keyblade))
                self.assertFalse(region.can_reach(two_tier_one))

                full_access = self._state_without_precollected_items()
                full_access.collect(self.world.create_item(own_keyblade))
                for tier_one_keyblade in tier_one_keyblades[:HIGHER_TIER_TIER_ONE_KEYBLADE_COUNT]:
                    full_access.collect(self.world.create_item(tier_one_keyblade))
                self.assertTrue(region.can_reach(full_access))

                duplicate_tier_one = self._state_without_precollected_items()
                duplicate_tier_one.collect(self.world.create_item(own_keyblade))
                for _ in range(3):
                    duplicate_tier_one.collect(self.world.create_item(tier_one_keyblades[0]))
                self.assertFalse(region.can_reach(duplicate_tier_one))

    def test_dark_world_and_remind_require_only_own_portal_keyblade(self) -> None:
        self.world_setup()
        for region_name, keyblade in (
            ("Re Mind", "Kingdom Key"),
            ("Dark World", "Oblivion"),
        ):
            with self.subTest(region=region_name):
                state = self._state_without_precollected_items()
                region = self.multiworld.get_region(region_name, self.player)
                self.assertFalse(region.can_reach(state))
                state.collect(self.world.create_item(keyblade))
                self.assertTrue(region.can_reach(state))

    def test_keyblade_graveyard_requires_starlight_and_attraction_flow_at_entrance(self) -> None:
        self.world_setup()
        state = self._state_without_precollected_items()
        region = self.multiworld.get_region("Keyblade Graveyard", self.player)
        attraction_only = self._state_without_precollected_items()
        attraction_only.collect(self.world.create_item("Unlock: Attraction Flow"))
        self.assertFalse(region.can_reach(attraction_only))
        state.collect(self.world.create_item("Starlight"))
        self.assertFalse(region.can_reach(state))
        state.collect(self.world.create_item("Unlock: Attraction Flow"))
        self.assertTrue(region.can_reach(state))
        fight = self.get_location_by_source("VBonus", "Vbonus_069")
        self.assertFalse(fight.can_reach(state))
        for item_name in ("Ability: Block", "Ability: Air Slide", "Ability: Dodge Roll",
                          "Magic: Cure", "Unlock: Magic"):
            state.collect(self.world.create_item(item_name))
        self.assertTrue(fight.can_reach(state))

    def test_starting_portal_keyblades_are_ungated_tier_one_candidates(self) -> None:
        self.assertEqual(
            STARTING_PORTAL_KEYBLADE_ITEM_NAMES,
            TIER_ONE_PORTAL_KEYBLADE_ITEM_NAMES.difference({"Wheel of Fate"}),
        )

        for keyblade_name in STARTING_PORTAL_KEYBLADE_ITEM_NAMES:
            region_name = next(
                region_name
                for region_name, portal_keyblade_name in PORTAL_KEYBLADE_REGION_LOCKS.items()
                if portal_keyblade_name == keyblade_name
            )
            self.assertEqual(WORLD_DIFFICULTY_TIER_BY_REGION[region_name], 1)

        self.assertNotIn("Shooting Star", STARTING_PORTAL_KEYBLADE_ITEM_NAMES)
        self.assertNotIn("Wheel of Fate", STARTING_PORTAL_KEYBLADE_ITEM_NAMES)

    def test_twilight_town_requires_own_keyblade_and_one_defense_only(self) -> None:
        self.world_setup()

        twilight_town = self.multiworld.get_region("Twilight Town", self.player)
        keyblade_only = self._state_without_precollected_items()
        keyblade_only.collect(self.world.create_item("Shooting Star"))
        self.assertFalse(twilight_town.can_reach(keyblade_only))

        for defense in ("Ability: Block", "Ability: Air Slide", "Ability: Dodge Roll"):
            with self.subTest(defense=defense):
                state = self._state_without_precollected_items()
                state.collect(self.world.create_item(defense))
                self.assertFalse(twilight_town.can_reach(state))
                state.collect(self.world.create_item("Shooting Star"))
                self.assertTrue(twilight_town.can_reach(state))

    def test_twilight_town_story_chain_inherits_prerequisite_logic(self) -> None:
        self.world_setup()
        source_keys = (
            ("VBonus", "Vbonus_014"),
            ("VBonus", "Vbonus_015"),
            ("VBonus", "Vbonus_016"),
            ("Event", "EVENT_CKGAME_001"),
            ("Event", "EVENT_KEYBLADE_002"),
        )
        locations = [self.get_location_by_source(*key) for key in source_keys]
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )
        for key in source_keys[-2:]:
            self.assertNotIn(key, EVENT_LOGIC_REQUIREMENTS)

        state = self._state_without_precollected_items()
        state.collect(self.world.create_item("Shooting Star"))
        self.assertTrue(all(not location.can_reach(state) for location in locations))

        for defense in ("Ability: Block", "Ability: Air Slide", "Ability: Dodge Roll"):
            with self.subTest(defense=defense):
                route = state.copy()
                route.collect(self.world.create_item(defense))
                self.assertTrue(all(location.can_reach(route) for location in locations))
                # No prerequisite has to be sent as a checked location. Changing
                # any earlier check's rule must still gate every successor.
                for index, prerequisite in enumerate(locations[:-1]):
                    original_rule = prerequisite.access_rule
                    try:
                        prerequisite.access_rule = lambda state: False
                        self.reset_reachability(route)
                        self.assertTrue(all(
                            not location.can_reach(route) for location in locations[index:]
                        ))
                        self.assertTrue(all(
                            location.can_reach(route) for location in locations[:index]
                        ))
                    finally:
                        prerequisite.access_rule = original_rule
                        self.reset_reachability(route)

    def test_toy_box_story_chain_preserves_additional_logic(self) -> None:
        self.world_setup()
        source_keys = (
            ("VBonus", "Vbonus_017"),
            ("VBonus", "Vbonus_018"),
            ("VBonus", "Vbonus_019"),
            ("VBonus", "Vbonus_020"),
            ("VBonus", "Vbonus_021"),
            ("VBonus", "Vbonus_022"),
            ("Event", "EVENT_HEARTBINDER_002"),
            ("VBonus", "Vbonus_023"),
            ("Event", "EVENT_KEYBLADE_003"),
        )
        locations = [self.get_location_by_source(*key) for key in source_keys]
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )

        state = self._state_without_precollected_items()
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(all(location.can_reach(state) for location in locations[:3]))
        self.assertTrue(all(not location.can_reach(state) for location in locations[3:]))

        for movement in ("Ability: High Jump", "Ability: Doubleflight"):
            single_movement = state.copy()
            single_movement.collect(self.world.create_item(movement))
            self.assertTrue(all(
                not location.can_reach(single_movement) for location in locations[3:]
            ))
        both_jumps = state.copy()
        both_jumps.collect(self.world.create_item("Ability: High Jump"))
        both_jumps.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertTrue(all(location.can_reach(both_jumps) for location in locations[:7]))
        # The inherited alternative does not replace King of Toys' Wall Run.
        self.assertTrue(all(not location.can_reach(both_jumps) for location in locations[7:]))

        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(location.can_reach(state) for location in locations))
        # Future additional logic on any predecessor must propagate even
        # through checks whose duplicate item requirements were removed.
        for index, prerequisite in enumerate(locations[:-1]):
            original_rule = prerequisite.access_rule
            try:
                prerequisite.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertTrue(all(
                    not location.can_reach(state) for location in locations[index:]
                ))
                self.assertTrue(all(
                    location.can_reach(state) for location in locations[:index]
                ))
            finally:
                prerequisite.access_rule = original_rule
                self.reset_reachability(state)

    def test_arendelle_story_chain_preserves_additional_logic(self) -> None:
        self.world_setup()
        source_keys = tuple(
            ("VBonus", f"Vbonus_{number:03}")
            for number in (41, 42, 43, 44, 45, 47, 48, 49, 50)
        ) + (("Event", "EVENT_KEYBLADE_007"),)
        locations = [self.get_location_by_source(*key) for key in source_keys]
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )

        state = self._state_without_precollected_items()
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Crystal Snow"))
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(location.can_reach(state) for location in locations[:2]))
        self.assertTrue(all(not location.can_reach(state) for location in locations[2:]))
        for movement in ("Ability: Pole Spin", "Ability: Air Slide"):
            single_movement = state.copy()
            single_movement.collect(self.world.create_item(movement))
            self.assertTrue(all(
                not location.can_reach(single_movement) for location in locations[2:]
            ))
        state.collect(self.world.create_item("Ability: Pole Spin"))
        state.collect(self.world.create_item("Ability: Air Slide"))
        self.assertTrue(all(location.can_reach(state) for location in locations))

        no_wall_run = state.copy()
        no_wall_run.remove(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(not location.can_reach(no_wall_run) for location in locations))
        # Additional logic on any predecessor must propagate through every
        # successor, including checks whose duplicate rules were removed.
        for index, prerequisite in enumerate(locations[:-1]):
            original_rule = prerequisite.access_rule
            try:
                prerequisite.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertTrue(all(
                    not location.can_reach(state) for location in locations[index:]
                ))
                self.assertTrue(all(
                    location.can_reach(state) for location in locations[:index]
                ))
            finally:
                prerequisite.access_rule = original_rule
                self.reset_reachability(state)

    def test_corona_story_chain_preserves_additional_logic(self) -> None:
        self.world_setup()
        source_keys = tuple(
            ("VBonus", f"Vbonus_{number:03}") for number in range(24, 31)
        ) + (("Event", "EVENT_KEYBLADE_004"),)
        locations = [self.get_location_by_source(*key) for key in source_keys]
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )

        state = self._state_without_precollected_items()
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Ever After"))
        self.assertTrue(all(location.can_reach(state) for location in locations[:3]))
        self.assertTrue(all(not location.can_reach(state) for location in locations[3:]))
        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(location.can_reach(state) for location in locations[:6]))
        self.assertTrue(all(not location.can_reach(state) for location in locations[6:]))
        state.collect(self.world.create_item("Ability: Block"))
        duplicate_defense = state.copy()
        duplicate_defense.collect(self.world.create_item("Ability: Block"))
        self.assertTrue(all(
            not location.can_reach(duplicate_defense) for location in locations[6:]
        ))
        state.collect(self.world.create_item("Ability: Air Slide"))
        self.assertTrue(all(location.can_reach(state) for location in locations))
        no_wall_run = state.copy()
        no_wall_run.remove(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(not location.can_reach(no_wall_run) for location in locations[3:]))

        # Future extra requirements propagate through every successor, even
        # after removing duplicated item rules from those checks.
        for index, prerequisite in enumerate(locations[:-1]):
            original_rule = prerequisite.access_rule
            try:
                prerequisite.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertTrue(all(
                    not location.can_reach(state) for location in locations[index:]
                ))
                self.assertTrue(all(
                    location.can_reach(state) for location in locations[:index]
                ))
            finally:
                prerequisite.access_rule = original_rule
                self.reset_reachability(state)

    def test_monstropolis_story_chain_preserves_additional_logic(self) -> None:
        self.world_setup()
        source_keys = tuple(
            ("VBonus", f"Vbonus_{number:03}") for number in range(32, 37)
        ) + (
            ("Event", "EVENT_008"),
            ("VBonus", "Vbonus_037"),
            ("VBonus", "Vbonus_038"),
            ("Event", "EVENT_HEARTBINDER_003"),
            ("VBonus", "Vbonus_039"),
            ("VBonus", "Vbonus_040"),
            ("Event", "EVENT_KEYBLADE_005"),
        )
        locations = [self.get_location_by_source(*key) for key in source_keys]
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )
        state = self._state_without_precollected_items()
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Happy Gear"))
        self.assertTrue(all(location.can_reach(state) for location in locations[:7]))
        self.assertTrue(all(not location.can_reach(state) for location in locations[7:]))

        for item_name in ("Ability: Superjump", "Ability: Wall Kick", "Ability: Air Slide"):
            state.collect(self.world.create_item(item_name))
        self.assertTrue(locations[7].can_reach(state))
        self.assertTrue(all(not location.can_reach(state) for location in locations[8:]))
        state.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertTrue(all(location.can_reach(state) for location in locations[:10]))
        self.assertTrue(all(not location.can_reach(state) for location in locations[10:]))
        # The boss's defense and Magic unlock requirements also gate
        # Happy Gear. Double Flight remains required on this movement route.
        for item_name in ("Ability: Block", "Unlock: Magic"):
            state.collect(self.world.create_item(item_name))
        self.assertTrue(all(location.can_reach(state) for location in locations))
        for item_name in (
            "Ability: Superjump", "Ability: Wall Kick", "Ability: Air Slide",
            "Ability: Doubleflight", "Ability: Block", "Unlock: Magic",
        ):
            with self.subTest(missing=item_name):
                missing = state.copy()
                missing.remove(self.world.create_item(item_name))
                self.assertTrue(all(not location.can_reach(missing) for location in locations[10:]))

        for index, prerequisite in enumerate(locations[:-1]):
            original_rule = prerequisite.access_rule
            try:
                prerequisite.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertTrue(all(
                    not location.can_reach(state) for location in locations[index:]
                ))
                self.assertTrue(all(
                    location.can_reach(state) for location in locations[:index]
                ))
            finally:
                prerequisite.access_rule = original_rule
                self.reset_reachability(state)

    def test_olympus_story_chain_and_optional_branches(self) -> None:
        self.world_setup()
        source_keys = tuple(
            ("VBonus", f"Vbonus_{number:03}")
            for number in (1, 2, 5, 6, 7, 8, 10, 11, 13)
        ) + (("Event", "EVENT_KEYBLADE_001"),)
        locations = [self.get_location_by_source(*key) for key in source_keys]
        forge = self.get_location_by_source("Event", "EVENT_001")
        figures = [self.get_location_by_source("Event", f"EVENT_{number:03}") for number in range(2, 7)]
        turn_in = self.get_location_by_source("Event", "EVENT_007")
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )
        state = self._state_without_precollected_items()
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Hero's Origin"))
        self.assertTrue(locations[0].can_reach(state))
        self.assertTrue(all(not location.can_reach(state) for location in locations[1:]))
        for last_jump in ("Ability: Doubleflight", "Ability: High Jump"):
            alternate = state.copy()
            for item_name in ("Ability: Superjump", "Ability: Wall Kick", "Ability: Air Slide", last_jump):
                alternate.collect(self.world.create_item(item_name))
            self.assertTrue(all(location.can_reach(alternate) for location in locations[:6]))
            self.assertTrue(all(not location.can_reach(alternate) for location in locations[6:]))
            self.assertFalse(forge.can_reach(alternate))
            self.assertFalse(turn_in.can_reach(alternate))
        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(all(location.can_reach(state) for location in locations + [forge, turn_in] + figures))
        for index, prerequisite in enumerate(locations[:-1]):
            original_rule = prerequisite.access_rule
            try:
                prerequisite.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertTrue(all(not location.can_reach(state) for location in locations[index:]))
                self.assertTrue(all(location.can_reach(state) for location in locations[:index]))
            finally:
                prerequisite.access_rule = original_rule
                self.reset_reachability(state)

        completion_rule = locations[-1].access_rule
        try:
            locations[-1].access_rule = lambda state: False
            self.reset_reachability(state)
            self.assertTrue(forge.can_reach(state))
            self.assertTrue(all(not figure.can_reach(state) for figure in figures))
            self.assertFalse(turn_in.can_reach(state))
        finally:
            locations[-1].access_rule = completion_rule
            self.reset_reachability(state)
        for figure in figures:
            original_rule = figure.access_rule
            try:
                figure.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertFalse(turn_in.can_reach(state))
                self.assertTrue(all(other.can_reach(state) for other in figures if other is not figure))
                self.assertTrue(locations[-1].can_reach(state))
            finally:
                figure.access_rule = original_rule
                self.reset_reachability(state)

    def test_caribbean_story_chain_inherits_prerequisite_logic(self) -> None:
        self.world_setup()
        source_keys = tuple(
            ("VBonus", f"Vbonus_{number:03}") for number in range(58, 67)
        ) + (("Event", "EVENT_KEYBLADE_008"),)
        locations = [self.get_location_by_source(*key) for key in source_keys]
        for previous, current in zip(source_keys, source_keys[1:]):
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[current], require_all_locations(previous),
            )
        state = self._state_without_precollected_items()
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        state.collect(self.world.create_item("Wheel of Fate"))
        self.assertTrue(all(not location.can_reach(state) for location in locations))
        for safety_item in ("Ability: Air Slide", "Ability: Dodge Roll", "Ability: Glide", "Magic: Blizzard"):
            with self.subTest(safety_item=safety_item):
                route = state.copy()
                route.collect(self.world.create_item(safety_item))
                if safety_item == "Magic: Blizzard":
                    self.assertTrue(all(not location.can_reach(route) for location in locations))
                    route.collect(self.world.create_item("Unlock: Magic"))
                self.assertTrue(all(location.can_reach(route) for location in locations))
                route.remove(self.world.create_item("Wheel of Fate"))
                self.assertTrue(all(not location.can_reach(route) for location in locations))

        state.collect(self.world.create_item("Ability: Air Slide"))
        # Additional rules on an earlier fight must gate all successors,
        # without making the earlier fights depend on later encounters.
        for index, prerequisite in enumerate(locations[:-1]):
            original_rule = prerequisite.access_rule
            try:
                prerequisite.access_rule = lambda state: False
                self.reset_reachability(state)
                self.assertTrue(all(not location.can_reach(state) for location in locations[index:]))
                self.assertTrue(all(location.can_reach(state) for location in locations[:index]))
            finally:
                prerequisite.access_rule = original_rule
                self.reset_reachability(state)

    def test_caribbean_chests_require_lightning_angler_except_approach(self) -> None:
        self.world_setup()
        approach_ids = {
            "CA_LBOX_005", "CA_SBOX_010", "CA_SBOX_021", "CA_SBOX_022",
            "CA_SBOX_011", "CA_SBOX_012", "CA_LBOX_007",
        }
        chests = {
            data["source_id"]: self.get_location_by_source("TreasureCA", data["source_id"])
            for data in self.world.active_locations if data["source_table"] == "TreasureCA"
        }
        self.assertEqual(len(chests), 56)
        self.assertTrue(approach_ids.issubset(chests))
        for source_id in chests.keys() - approach_ids:
            self.assertEqual(
                LOCATION_LOGIC_DEPENDENCIES[("TreasureCA", source_id)],
                require_all_locations(("VBonus", "Vbonus_061")),
            )
        state = self._state_without_precollected_items()
        for item_name in (
            "Wheel of Fate", "Ability: Air Slide", "Unlock: Wall Run",
            "Ability: High Jump", "Ability: Glide",
        ):
            state.collect(self.world.create_item(item_name))
        self.assertTrue(all(chest.can_reach(state) for chest in chests.values()))
        boss = self.get_location_by_source("VBonus", "Vbonus_061")
        original_rule = boss.access_rule
        try:
            boss.access_rule = lambda state: False
            self.reset_reachability(state)
            self.assertEqual(
                {source_id for source_id, chest in chests.items() if chest.can_reach(state)},
                approach_ids,
            )
            self.assertFalse(chests["CA_LBOX_010"].can_reach(state))  # Ocean Heartbinder
        finally:
            boss.access_rule = original_rule
            self.reset_reachability(state)

        # Boss access does not replace a chest's existing movement rule.
        state.remove(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(boss.can_reach(state))
        self.assertFalse(chests["CA_LBOX_008"].can_reach(state))
        self.assertTrue(all(chests[source_id].can_reach(state) for source_id in approach_ids))

    def test_caribbean_requires_keyblade_and_one_safety_tool(self) -> None:
        self.world_setup()

        caribbean = self.multiworld.get_region("The Caribbean", self.player)
        self.assertEqual(
            THE_CARIBBEAN_PORTAL_LOGIC_REQUIREMENTS,
            parse_requirement_summary("Air Slide OR Dodge Roll OR Glide OR Blizzard"),
        )

        keyblade_only = self._state_without_precollected_items()
        keyblade_only.collect(self.world.create_item("Wheel of Fate"))
        self.assertFalse(caribbean.can_reach(keyblade_only))

        for safety_item in (
            "Ability: Air Slide",
            "Ability: Dodge Roll",
            "Ability: Glide",
        ):
            with self.subTest(safety_item=safety_item):
                state = self._state_without_precollected_items()
                state.collect(self.world.create_item("Wheel of Fate"))
                state.collect(self.world.create_item(safety_item))
                self.assertTrue(caribbean.can_reach(state))

        blizzard_without_magic_unlock = self._state_without_precollected_items()
        blizzard_without_magic_unlock.collect(self.world.create_item("Wheel of Fate"))
        blizzard_without_magic_unlock.collect(self.world.create_item("Magic: Blizzard"))
        self.assertFalse(caribbean.can_reach(blizzard_without_magic_unlock))

        blizzard_with_magic_unlock = self._state_without_precollected_items()
        blizzard_with_magic_unlock.collect(self.world.create_item("Wheel of Fate"))
        blizzard_with_magic_unlock.collect(self.world.create_item("Magic: Blizzard"))
        blizzard_with_magic_unlock.collect(self.world.create_item("Unlock: Magic"))
        self.assertTrue(caribbean.can_reach(blizzard_with_magic_unlock))

        safety_tool_without_keyblade = self._state_without_precollected_items()
        safety_tool_without_keyblade.collect(self.world.create_item("Ability: Air Slide"))
        self.assertFalse(caribbean.can_reach(safety_tool_without_keyblade))

    def test_level_ups_require_reachable_xp_source_world(self) -> None:
        self.world_setup()

        level_ups = self.multiworld.get_region("Level Ups", self.player)
        self.assertFalse(level_ups.can_reach(self._state_without_precollected_items()))

        dark_world_only = self._state_without_precollected_items()
        dark_world_only.collect(self.world.create_item("Oblivion"))
        self.assertFalse(level_ups.can_reach(dark_world_only))

        data_battles_only = self._state_without_precollected_items()
        data_battles_only.collect(self.world.create_item("Oathkeeper"))
        self.assertFalse(level_ups.can_reach(data_battles_only))

        olympus_access = self._state_without_precollected_items()
        olympus_access.collect(self.world.create_item("Hero's Origin"))
        self.assertFalse(level_ups.can_reach(olympus_access))

        keyblade_graveyard_access = self._state_without_precollected_items()
        keyblade_graveyard_access.collect(self.world.create_item("Starlight"))
        self.assertFalse(level_ups.can_reach(keyblade_graveyard_access))

        toy_box_access = self._state_without_precollected_items()
        toy_box_access.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(level_ups.can_reach(toy_box_access))

        for region_name in ("Olympus", "Dark World", "Data Battles", "Unreality", "Keyblade Graveyard"):
            self.assertNotIn(region_name, LEVEL_UP_XP_SOURCE_REGION_NAMES)

    def test_level_up_locations_scale_with_reachable_xp_source_world_count(self) -> None:
        self.world_setup()

        level_locations = {
            level: self.multiworld.get_location(f"Level Ups - Sora Level {level}", self.player)
            for level in (3, 4, 5, 6, 8, 9, 22, 25)
        }

        one_world = self._state_without_precollected_items()
        one_world.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(level_locations[3].can_reach(one_world))
        self.assertFalse(level_locations[4].can_reach(one_world))

        two_worlds = self._state_without_precollected_items()
        for item_name in ("Favorite Deputy", "Ever After"):
            two_worlds.collect(self.world.create_item(item_name))
        self.assertTrue(level_locations[5].can_reach(two_worlds))
        self.assertFalse(level_locations[6].can_reach(two_worlds))

        three_worlds = self._state_without_precollected_items()
        for item_name in ("Favorite Deputy", "Ever After", "Happy Gear"):
            three_worlds.collect(self.world.create_item(item_name))
        self.assertTrue(level_locations[8].can_reach(three_worlds))
        self.assertFalse(level_locations[9].can_reach(three_worlds))

        all_story_worlds = self._state_without_precollected_items()
        for region_name in LEVEL_UP_XP_SOURCE_REGION_NAMES:
            all_story_worlds.collect(
                self.world.create_item(PORTAL_KEYBLADE_REGION_LOCKS[region_name])
            )
        for item_name in ("Unlock: Wall Run", "Ability: Block", "Ability: Air Slide"):
            all_story_worlds.collect(self.world.create_item(item_name))
        self.assertTrue(level_locations[22].can_reach(all_story_worlds))
        self.assertTrue(level_locations[25].can_reach(all_story_worlds))

    def test_level_up_source_count_curve_reaches_level_22_after_all_story_worlds(self) -> None:
        self.assertEqual(SCALED_STORY_END_LEVEL, 22)
        expected_required_counts = {
            2: 1,
            3: 1,
            4: 2,
            5: 2,
            6: 3,
            8: 3,
            9: 4,
            12: 4,
            13: 5,
            15: 5,
            16: 6,
            19: 6,
            20: 7,
            22: 7,
            25: 7,
            50: 7,
        }
        for level, expected_count in expected_required_counts.items():
            with self.subTest(level=level):
                self.assertEqual(required_level_up_xp_source_count(level), expected_count)

    def test_level_ups_reject_keyblades_and_goal_items(self) -> None:
        self.world_setup()

        level_up_location = next(
            location
            for location in self.multiworld.get_locations(self.player)
            if location.name.startswith("Level Ups - ")
        )

        self.assertFalse(level_up_location.item_rule(self.world.create_item("Hero's Origin")))
        self.assertFalse(level_up_location.item_rule(self.world.create_item("Heart Piece")))
        self.assertTrue(level_up_location.item_rule(self.world.create_item(STYLE_CHANGE_UNLOCK_ITEM)))
        self.assertTrue(level_up_location.item_rule(self.world.create_item("Magic: Fire")))
        self.assertTrue(level_up_location.item_rule(self.world.create_item("Potion")))

    def test_level_ups_allow_foreign_keyblade_named_items(self) -> None:
        self.world_setup()

        level_up_location = next(
            location
            for location in self.multiworld.get_locations(self.player)
            if location.name.startswith("Level Ups - ")
        )
        foreign_item = self.world.create_item("Hero's Origin")
        foreign_item.player = self.player + 1

        self.assertTrue(level_up_location.item_rule(foreign_item))

    def test_csv_chest_logic_accepts_alternate_routes(self) -> None:
        self.world_setup()

        source_key = ("TreasureHE", "HE_SBOX_011")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, CHEST_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        world_only = self._state_without_precollected_items()
        world_only.collect(self.world.create_item("Hero's Origin"))
        self.assertFalse(location.can_reach(world_only))

        wall_run = self._state_without_precollected_items()
        wall_run.collect(self.world.create_item("Hero's Origin"))
        wall_run.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(location.can_reach(wall_run))

        alternate_route = self._state_without_precollected_items()
        alternate_route.collect(self.world.create_item("Hero's Origin"))
        alternate_route.collect(self.world.create_item("Ability: Superjump"))
        alternate_route.collect(self.world.create_item("Ability: Wall Kick"))
        alternate_route.collect(self.world.create_item("Ability: Air Slide"))
        alternate_route.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertTrue(location.can_reach(alternate_route))

    def test_csv_chest_logic_respects_wall_run_only_olympus_chests(self) -> None:
        self.world_setup()

        source_key = ("TreasureHE", "HE_SBOX_017")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, CHEST_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        alternate_route = self._state_without_precollected_items()
        alternate_route.collect(self.world.create_item("Hero's Origin"))
        alternate_route.collect(self.world.create_item("Ability: Superjump"))
        alternate_route.collect(self.world.create_item("Ability: Wall Kick"))
        alternate_route.collect(self.world.create_item("Ability: Air Slide"))
        alternate_route.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertFalse(location.can_reach(alternate_route))

        wall_run = self._state_without_precollected_items()
        wall_run.collect(self.world.create_item("Hero's Origin"))
        wall_run.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(location.can_reach(wall_run))

    def test_csv_chest_logic_applies_monstropolis_routes(self) -> None:
        self.world_setup()

        source_key = ("TreasureMI", "MI_SBOX_013")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, CHEST_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        world_only = self._state_without_precollected_items()
        world_only.collect(self.world.create_item("Hero's Origin"))
        world_only.collect(self.world.create_item("Happy Gear"))
        self.assertFalse(location.can_reach(world_only))

        wall_run = self._state_without_precollected_items()
        wall_run.collect(self.world.create_item("Hero's Origin"))
        wall_run.collect(self.world.create_item("Happy Gear"))
        wall_run.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(location.can_reach(wall_run))

        alternate_route = self._state_without_precollected_items()
        alternate_route.collect(self.world.create_item("Hero's Origin"))
        alternate_route.collect(self.world.create_item("Happy Gear"))
        alternate_route.collect(self.world.create_item("Ability: Superjump"))
        alternate_route.collect(self.world.create_item("Ability: Wall Kick"))
        alternate_route.collect(self.world.create_item("Ability: Air Slide"))
        alternate_route.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertTrue(location.can_reach(alternate_route))

    def test_monstropolis_accessway_rest_area_chest_only_requires_world_access(self) -> None:
        self.world_setup()

        source_key = ("TreasureMI", "MI_SBOX_015")
        location = self.get_location_by_source(*source_key)
        self.assertNotIn(source_key, CHEST_LOGIC_REQUIREMENTS)

        no_world_access = self._state_without_precollected_items()
        self.assertFalse(location.can_reach(no_world_access))

        monstropolis_access = self._state_without_precollected_items()
        monstropolis_access.collect(self.world.create_item("Happy Gear"))
        self.assertTrue(location.can_reach(monstropolis_access))

    def test_csv_chest_logic_treats_doubleflight_as_high_jump_stub(self) -> None:
        self.world_setup()

        source_key = ("TreasureCA", "CA_SBOX_017")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, CHEST_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        state = self._state_without_precollected_items()
        state.collect(self.world.create_item("Wheel of Fate"))
        state.collect(self.world.create_item("Happy Gear"))
        state.collect(self.world.create_item("Ability: Air Slide"))
        state.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertTrue(location.can_reach(state))

    def test_csv_chest_logic_preserves_explicit_high_jump_and_doubleflight_routes(self) -> None:
        self.world_setup()

        source_key = ("TreasureTS", "TS_SBOX_011")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, CHEST_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        for movement_item in ("Ability: High Jump", "Ability: Doubleflight"):
            with self.subTest(movement_item=movement_item):
                partial = self._state_without_precollected_items()
                partial.collect(self.world.create_item("Favorite Deputy"))
                partial.collect(self.world.create_item(movement_item))
                self.assertFalse(location.can_reach(partial))

        both = self._state_without_precollected_items()
        both.collect(self.world.create_item("Favorite Deputy"))
        both.collect(self.world.create_item("Ability: High Jump"))
        both.collect(self.world.create_item("Ability: Doubleflight"))
        self.assertTrue(location.can_reach(both))

        wall_run = self._state_without_precollected_items()
        wall_run.collect(self.world.create_item("Favorite Deputy"))
        wall_run.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(location.can_reach(wall_run))

    def test_toy_box_drink_factory_and_dissidia_chests_only_require_world_access(self) -> None:
        self.world_setup()

        no_world_access = self._state_without_precollected_items()
        toy_box_access = self._state_without_precollected_items()
        toy_box_access.collect(self.world.create_item("Favorite Deputy"))

        for source_id in ("TS_SBOX_023", "TS_SBOX_006"):
            with self.subTest(source_id=source_id):
                source_key = ("TreasureTS", source_id)
                location = self.get_location_by_source(*source_key)
                self.assertNotIn(source_key, CHEST_LOGIC_REQUIREMENTS)
                self.assertFalse(location.can_reach(no_world_access))
                self.assertTrue(location.can_reach(toy_box_access))

    def test_csv_chest_logic_still_requires_world_access(self) -> None:
        self.world_setup()

        source_key = ("TreasureCA", "CA_LBOX_011")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        movement_only = self._state_without_precollected_items()
        movement_only.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertFalse(location.can_reach(movement_only))

        world_and_tier_only = self._state_without_precollected_items()
        world_and_tier_only.collect(self.world.create_item("Wheel of Fate"))
        world_and_tier_only.collect(self.world.create_item("Happy Gear"))
        self.assertFalse(location.can_reach(world_and_tier_only))

        full_access = self._state_without_precollected_items()
        full_access.collect(self.world.create_item("Wheel of Fate"))
        full_access.collect(self.world.create_item("Happy Gear"))
        full_access.collect(self.world.create_item("Ability: Air Slide"))
        full_access.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(location.can_reach(full_access))

    def test_badlands_chests_depend_on_ten_thousand_heartless_fight(self) -> None:
        self.world_setup()

        source_keys = (
            ("TreasureKG", "KG_LBOX_001"),
            ("TreasureKG", "KG_SBOX_004"),
        )
        fight_source_keys = (
            ("VBonus", "Vbonus_069"),
            ("VBonus", "Vbonus_076"),
        )

        combat_items = (
            "Ability: Block",
            "Ability: Air Slide",
            "Magic: Cure",
            "Ability: Dodge Roll",
            "Unlock: Attraction Flow",
            "Unlock: Magic",
        )
        for source_key in source_keys:
            with self.subTest(source_key=source_key):
                self.assertNotIn(source_key, CHEST_LOGIC_REQUIREMENTS)
                dependency = LOCATION_LOGIC_DEPENDENCIES[source_key]
                self.assertFalse(dependency.require_all)
                self.assertEqual(dependency.source_keys, fight_source_keys)
                location = self.get_location_by_source(*source_key)

                attraction_only = self._state_without_precollected_items()
                self._collect_higher_tier_gate(attraction_only)
                attraction_only.collect(self.world.create_item("Starlight"))
                attraction_only.collect(self.world.create_item("Unlock: Attraction Flow"))
                self.assertFalse(location.can_reach(attraction_only))

                no_world_access = self._state_without_precollected_items()
                self._collect_higher_tier_gate(no_world_access)
                for item_name in combat_items:
                    no_world_access.collect(self.world.create_item(item_name))
                self.assertFalse(location.can_reach(no_world_access))

                full_access = self._state_without_precollected_items()
                self._collect_higher_tier_gate(full_access)
                full_access.collect(self.world.create_item("Starlight"))
                for item_name in combat_items:
                    full_access.collect(self.world.create_item(item_name))
                self.assertTrue(location.can_reach(full_access))

    def test_downstream_keyblade_graveyard_fights_depend_on_ten_thousand_heartless(self) -> None:
        fight_source_keys = (
            ("VBonus", "Vbonus_069"),
            ("VBonus", "Vbonus_076"),
        )
        downstream_fight_source_ids = (
            "Vbonus_068",
            "Vbonus_070",
            "Vbonus_071",
            "Vbonus_072",
            "Vbonus_073",
            "Vbonus_074",
            "Vbonus_075",
        )

        for source_id in downstream_fight_source_ids:
            with self.subTest(source_id=source_id):
                dependency = LOCATION_LOGIC_DEPENDENCIES[("VBonus", source_id)]
                self.assertFalse(dependency.require_all)
                self.assertEqual(dependency.source_keys, fight_source_keys)

    def test_reported_chest_and_victory_bonus_movement_requirements(self) -> None:
        expected_chest_requirements = {
            ("TreasureTS", "TS_SBOX_022"): parse_requirement_summary(
                "Wall Run OR High Jump + Double Flight"
            ),
            ("TreasureMI", "MI_SBOX_019"): parse_requirement_summary(
                "Wall Run + AnyOf2(Block/Air Slide/Dodge Roll) OR "
                "Super Jump + Wall Kick + Air Dash x1 + Double Flight + "
                "AnyOf2(Block/Air Slide/Dodge Roll)"
            ),
        }
        expected_vbonus_requirements = {
            ("VBonus", "Vbonus_030"): parse_requirement_summary(
                "AnyOf2(Block/Air Slide/Dodge Roll)"  # Wall Run inherited from Chaos Carriage
            ),
            ("VBonus", "Vbonus_038"): parse_requirement_summary(
                "Wall Run OR Super Jump + Wall Kick + Air Dash x1"
            ),
            ("VBonus", "Vbonus_040"): parse_requirement_summary(
                "AnyOf2(Block/Air Slide/Dodge Roll) + Unlock: Magic"
            ),
        }

        for source_key, expected in expected_chest_requirements.items():
            with self.subTest(source_key=source_key):
                self.assertEqual(CHEST_LOGIC_REQUIREMENTS.get(source_key), expected)
        for source_key, expected in expected_vbonus_requirements.items():
            with self.subTest(source_key=source_key):
                self.assertEqual(VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key), expected)
        self.assertNotIn(("VBonus", "Vbonus_004"), VICTORY_BONUS_LOGIC_REQUIREMENTS)

    def test_vbonus_040_requires_movement_defense_and_magic_without_links(self) -> None:
        self.world_setup()

        location = self.get_location_by_source("VBonus", "Vbonus_040")

        def build_state(*item_names: str) -> CollectionState:
            state = self._state_without_precollected_items()
            state.collect(self.world.create_item("Happy Gear"))
            for item_name in item_names:
                state.collect(self.world.create_item(item_name))
            return state

        wall_run = build_state(
            "Unlock: Wall Run",
            "Ability: Block",
            "Ability: Dodge Roll",
            "Unlock: Magic",
        )
        self.assertTrue(location.can_reach(wall_run))
        self.assertFalse(wall_run.has("Unlock: Links", self.player))
        self.assertTrue(all(not wall_run.has(name, self.player) for name in LINK_HEARTBINDER_ITEM_NAMES))

        alternate_movement = build_state(
            "Ability: Superjump",
            "Ability: Wall Kick",
            "Ability: Air Slide",
            "Ability: Doubleflight",
            "Ability: Block",
            "Unlock: Magic",
        )
        self.assertTrue(location.can_reach(alternate_movement))
        one_defense = build_state(
            "Unlock: Wall Run",
            "Ability: Block",
            "Unlock: Magic",
        )
        self.assertFalse(location.can_reach(one_defense))

        missing_defense = build_state(
            "Unlock: Wall Run", "Unlock: Magic"
        )
        self.assertFalse(location.can_reach(missing_defense))

        missing_magic_unlock = build_state(
            "Unlock: Wall Run",
            "Ability: Block",
            "Ability: Dodge Roll",
        )
        self.assertFalse(location.can_reach(missing_magic_unlock))

    def test_monstropolis_chest_21_requires_vbonus_040_to_be_logically_reachable(self) -> None:
        self.world_setup()

        chest = self.get_location_by_source("TreasureMI", "MI_SBOX_014")
        vbonus = self.get_location_by_source("VBonus", "Vbonus_040")
        dependency = LOCATION_LOGIC_DEPENDENCIES[("TreasureMI", "MI_SBOX_014")]
        self.assertTrue(dependency.require_all)
        self.assertEqual(dependency.source_keys, (("VBonus", "Vbonus_040"),))

        state = self._state_without_precollected_items()
        state.collect(self.world.create_item("Happy Gear"))
        state.collect(self.world.create_item("Unlock: Wall Run"))

        # Chest 21's own movement rule is met here, but the final Monstropolis
        # victory bonus is not yet logically reachable.
        self.assertFalse(vbonus.can_reach(state))
        self.assertFalse(chest.can_reach(state))

        for item_name in (
            "Ability: Block",
            "Ability: Dodge Roll",
            "Unlock: Magic",
        ):
            state.collect(self.world.create_item(item_name))

        self.assertTrue(vbonus.can_reach(state))
        self.assertTrue(chest.can_reach(state))

    def test_vbonus_030_requires_wall_run_and_two_defensive_abilities(self) -> None:
        self.world_setup()

        location = self.get_location_by_source("VBonus", "Vbonus_030")

        defensive_pairs = (
            ("Ability: Block", "Ability: Air Slide"),
            ("Ability: Block", "Ability: Dodge Roll"),
            ("Ability: Air Slide", "Ability: Dodge Roll"),
        )
        for defensive_items in defensive_pairs:
            with self.subTest(defensive_items=defensive_items):
                full_access = self._state_without_precollected_items()
                full_access.collect(self.world.create_item("Ever After"))
                full_access.collect(self.world.create_item("Unlock: Wall Run"))
                for defensive_item in defensive_items:
                    full_access.collect(self.world.create_item(defensive_item))
                self.assertTrue(location.can_reach(full_access))

        one_defense = self._state_without_precollected_items()
        one_defense.collect(self.world.create_item("Ever After"))
        one_defense.collect(self.world.create_item("Unlock: Wall Run"))
        one_defense.collect(self.world.create_item("Ability: Block"))
        self.assertFalse(location.can_reach(one_defense))

        defense_only = self._state_without_precollected_items()
        defense_only.collect(self.world.create_item("Ever After"))
        defense_only.collect(self.world.create_item("Ability: Block"))
        defense_only.collect(self.world.create_item("Ability: Air Slide"))
        self.assertFalse(location.can_reach(defense_only))

    def test_final_world_chest_requires_only_ten_thousand_heartless_fight(self) -> None:
        self.world_setup()

        location = self.get_location_by_source("TreasureEW", "EW_LBOX_001")
        self.assertNotIn(("TreasureEW", "EW_LBOX_001"), CHEST_LOGIC_REQUIREMENTS)

        old_requirements = self._state_without_precollected_items()
        old_requirements.collect(self.world.create_item("Kingdom Key"))
        old_requirements.collect(self.world.create_item("Unlock: Attraction Flow"))
        self.assertFalse(location.can_reach(old_requirements))

        full_access = self._state_without_precollected_items()
        self._collect_higher_tier_gate(full_access)
        for item_name in (
            "Starlight",
            "Ability: Block",
            "Ability: Air Slide",
            "Magic: Cure",
            "Ability: Dodge Roll",
            "Unlock: Attraction Flow",
            "Unlock: Magic",
        ):
            full_access.collect(self.world.create_item(item_name))
        self.assertFalse(full_access.has("Kingdom Key", self.player))
        self.assertTrue(location.can_reach(full_access))

        for source_id in ("Vbonus_069", "Vbonus_076"):
            self.assertTrue(self.get_location_by_source("VBonus", source_id).can_reach(full_access))

        full_access.remove(self.world.create_item("Starlight"))
        self.assertFalse(location.can_reach(full_access))

    def test_final_world_sora_rewards_depend_on_ten_thousand_heartless_fight(self) -> None:
        self.world_setup()

        rewards = tuple(
            self.get_location_by_source("VBonus", source_id)
            for source_id in ("Vbonus_083", "Vbonus_084")
        )
        fights = tuple(
            self.get_location_by_source("VBonus", source_id)
            for source_id in ("Vbonus_069", "Vbonus_076")
        )
        required_items = (
            "Starlight",
            "Ability: Block",
            "Ability: Air Slide",
            "Magic: Cure",
            "Ability: Dodge Roll",
            "Unlock: Attraction Flow",
            "Unlock: Magic",
        )
        full_access = self._state_without_precollected_items()
        self._collect_higher_tier_gate(full_access)
        for item_name in required_items:
            full_access.collect(self.world.create_item(item_name))
        for location in fights + rewards:
            self.assertTrue(location.can_reach(full_access), location.name)

        for missing_item in required_items:
            with self.subTest(missing_item=missing_item):
                state = full_access.copy()
                state.remove(self.world.create_item(missing_item))
                for location in fights + rewards:
                    self.assertFalse(location.can_reach(state), location.name)

    def test_shared_skein_chests_require_combat_attraction_and_either_world(self) -> None:
        self.world_setup()

        source_keys = tuple(
            ("TreasureKG", source_id)
            for source_id in ("KG_LBOX_002", "KG_SBOX_001", "KG_SBOX_002", "KG_SBOX_003")
        )
        expected_requirements = parse_requirement_summary(
            "Block + Air Slide + Cure + Dodge Roll + Attraction Flow"
        )
        local_requirement_items = (
            "Ability: Block",
            "Ability: Air Slide",
            "Magic: Cure",
            "Ability: Dodge Roll",
            "Unlock: Attraction Flow",
            "Unlock: Magic",
        )

        for source_key in source_keys:
            with self.subTest(source_key=source_key):
                self.assertEqual(CHEST_LOGIC_REQUIREMENTS.get(source_key), expected_requirements)
                location = self.get_location_by_source(*source_key)
                self.assertEqual(
                    self.get_location_base_region(location).name,
                    KEYBLADE_GRAVEYARD_REMIND_SHARED_REGION_NAME,
                )

                no_world = self._state_without_precollected_items()
                self._collect_higher_tier_gate(no_world)
                for item_name in local_requirement_items:
                    no_world.collect(self.world.create_item(item_name))
                self.assertFalse(location.can_reach(no_world))

                for world_keyblade in ("Starlight", "Kingdom Key"):
                    with self.subTest(source_key=source_key, world_keyblade=world_keyblade):
                        full_access = self._state_without_precollected_items()
                        self._collect_higher_tier_gate(full_access)
                        full_access.collect(self.world.create_item(world_keyblade))
                        for item_name in local_requirement_items:
                            full_access.collect(self.world.create_item(item_name))
                        self.assertTrue(location.can_reach(full_access))

                        missing_dodge_roll = self._state_without_precollected_items()
                        self._collect_higher_tier_gate(missing_dodge_roll)
                        missing_dodge_roll.collect(self.world.create_item(world_keyblade))
                        for item_name in local_requirement_items:
                            if item_name != "Ability: Dodge Roll":
                                missing_dodge_roll.collect(self.world.create_item(item_name))
                        self.assertFalse(location.can_reach(missing_dodge_roll))

    def test_remind_dlc_post_fight_chests_require_wall_run_and_common_combat_notes(self) -> None:
        self.world_setup()

        source_keys = (
            ("TreasureBT", "BT_LBOX_002"),
            ("TreasureBT", "BT_SBOX_001"),
            ("TreasureBT", "BT_SBOX_002"),
            ("TreasureBT", "BT_SBOX_003"),
            ("TreasureBT", "BT_SBOX_004"),
            ("TreasureBT", "BT_SBOX_005"),
            ("TreasureBT", "BT_SBOX_006"),
            ("TreasureBT", "BT_SBOX_007"),
            ("TreasureBT", "BT_LBOX_001"),
        )
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }

        expected_requirements = ((
            frozenset({"Unlock: Wall Run"}),
            frozenset({"Ability: Block"}),
            frozenset({"Ability: Air Slide"}),
            frozenset({"Magic: Cure"}),
            frozenset({"Ability: Dodge Roll"}),
            frozenset({"Unlock: Magic"}),
        ),)
        for source_key in source_keys:
            with self.subTest(source_key=source_key):
                self.assertEqual(CHEST_LOGIC_REQUIREMENTS.get(source_key), expected_requirements)
                self.assertIn(source_key, source_key_to_location)

                location = source_key_to_location[source_key]

                missing_wall_run = self._state_without_precollected_items()
                self._collect_higher_tier_gate(missing_wall_run)
                missing_wall_run.collect(self.world.create_item("Starlight"))
                missing_wall_run.collect(self.world.create_item("Kingdom Key"))
                missing_wall_run.collect(self.world.create_item("Ability: Block"))
                missing_wall_run.collect(self.world.create_item("Ability: Air Slide"))
                missing_wall_run.collect(self.world.create_item("Magic: Cure"))
                missing_wall_run.collect(self.world.create_item("Ability: Dodge Roll"))
                missing_wall_run.collect(self.world.create_item("Unlock: Magic"))
                self.assertFalse(location.can_reach(missing_wall_run))

                missing_dodge_roll = self._state_without_precollected_items()
                self._collect_higher_tier_gate(missing_dodge_roll)
                missing_dodge_roll.collect(self.world.create_item("Starlight"))
                missing_dodge_roll.collect(self.world.create_item("Kingdom Key"))
                missing_dodge_roll.collect(self.world.create_item("Unlock: Wall Run"))
                missing_dodge_roll.collect(self.world.create_item("Ability: Block"))
                missing_dodge_roll.collect(self.world.create_item("Ability: Air Slide"))
                missing_dodge_roll.collect(self.world.create_item("Magic: Cure"))
                missing_dodge_roll.collect(self.world.create_item("Unlock: Magic"))
                self.assertFalse(location.can_reach(missing_dodge_roll))

                full_access = self._state_without_precollected_items()
                self._collect_higher_tier_gate(full_access)
                full_access.collect(self.world.create_item("Starlight"))
                full_access.collect(self.world.create_item("Kingdom Key"))
                full_access.collect(self.world.create_item("Unlock: Wall Run"))
                full_access.collect(self.world.create_item("Ability: Block"))
                full_access.collect(self.world.create_item("Ability: Air Slide"))
                full_access.collect(self.world.create_item("Magic: Cure"))
                full_access.collect(self.world.create_item("Ability: Dodge Roll"))
                full_access.collect(self.world.create_item("Unlock: Magic"))
                self.assertTrue(location.can_reach(full_access))

    def test_csv_victory_bonus_logic_uses_magic_notes(self) -> None:
        self.world_setup()

        source_key = ("VBonus", "Vbonus_057")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, VICTORY_BONUS_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        pole_spin_only = self._state_without_precollected_items()
        pole_spin_only.collect(self.world.create_item("Nano Gear"))
        pole_spin_only.collect(self.world.create_item("Happy Gear"))
        pole_spin_only.collect(self.world.create_item("Ability: Pole Spin"))
        self.assertFalse(location.can_reach(pole_spin_only))

        magic_route = self._state_without_precollected_items()
        magic_route.collect(self.world.create_item("Nano Gear"))
        magic_route.collect(self.world.create_item("Happy Gear"))
        magic_route.collect(self.world.create_item("Ability: Pole Spin"))
        magic_route.collect(self.world.create_item("Magic: Fire"))
        magic_route.collect(self.world.create_item("Magic: Water"))
        magic_route.collect(self.world.create_item("Magic: Aero"))
        self.assertFalse(location.can_reach(magic_route))

        magic_route.collect(self.world.create_item("Unlock: Magic"))
        self.assertFalse(location.can_reach(magic_route))
        magic_route.collect(self.world.create_item("Magic: Thunder"))
        self.assertTrue(location.can_reach(magic_route))

    def test_dark_world_victory_bonus_requires_common_combat_notes(self) -> None:
        self.world_setup()

        source_key = ("VBonus", "Vbonus_067")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        expected_requirements = (
            (
                frozenset({"Ability: Block"}),
                frozenset({"Ability: Air Slide"}),
                frozenset({"Magic: Cure"}),
                frozenset({"Ability: Dodge Roll"}),
                frozenset({"Unlock: Magic"}),
            ),
        )
        self.assertEqual(VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key), expected_requirements)
        self.assertIn(source_key, source_key_to_location)

        location = source_key_to_location[source_key]
        missing_dodge_roll = self._state_without_precollected_items()
        self._collect_higher_tier_gate(missing_dodge_roll)
        missing_dodge_roll.collect(self.world.create_item("Oblivion"))
        missing_dodge_roll.collect(self.world.create_item("Wheel of Fate"))
        missing_dodge_roll.collect(self.world.create_item("Ability: Block"))
        missing_dodge_roll.collect(self.world.create_item("Ability: Air Slide"))
        missing_dodge_roll.collect(self.world.create_item("Magic: Cure"))
        missing_dodge_roll.collect(self.world.create_item("Unlock: Magic"))
        self.assertFalse(location.can_reach(missing_dodge_roll))

        missing_magic_unlock = self._state_without_precollected_items()
        self._collect_higher_tier_gate(missing_magic_unlock)
        missing_magic_unlock.collect(self.world.create_item("Oblivion"))
        missing_magic_unlock.collect(self.world.create_item("Wheel of Fate"))
        missing_magic_unlock.collect(self.world.create_item("Ability: Block"))
        missing_magic_unlock.collect(self.world.create_item("Ability: Air Slide"))
        missing_magic_unlock.collect(self.world.create_item("Ability: Dodge Roll"))
        missing_magic_unlock.collect(self.world.create_item("Magic: Cure"))
        self.assertFalse(location.can_reach(missing_magic_unlock))

        full_note_logic = self._state_without_precollected_items()
        self._collect_higher_tier_gate(full_note_logic)
        full_note_logic.collect(self.world.create_item("Oblivion"))
        full_note_logic.collect(self.world.create_item("Wheel of Fate"))
        full_note_logic.collect(self.world.create_item("Ability: Block"))
        full_note_logic.collect(self.world.create_item("Ability: Air Slide"))
        full_note_logic.collect(self.world.create_item("Ability: Dodge Roll"))
        full_note_logic.collect(self.world.create_item("Magic: Cure"))
        full_note_logic.collect(self.world.create_item("Unlock: Magic"))
        self.assertTrue(location.can_reach(full_note_logic))

    def test_remind_dlc_victory_bonuses_require_common_combat_notes(self) -> None:
        self.world_setup()

        expected_common_combat = (
            frozenset({"Ability: Block"}),
            frozenset({"Ability: Air Slide"}),
            frozenset({"Magic: Cure"}),
            frozenset({"Ability: Dodge Roll"}),
            frozenset({"Unlock: Magic"}),
        )
        expected_with_wall_run = ((frozenset({"Unlock: Wall Run"}), *expected_common_combat),)
        expected_without_wall_run = ((expected_common_combat),)

        for source_id in ("VBonus_DLC_015", "VBonus_DLC_010", "VBonus_DLC_008"):
            source_key = ("VBonus", source_id)
            with self.subTest(source_key=source_key):
                requirements = VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key)
                self.assertEqual(requirements, expected_with_wall_run)
                flattened = set().union(*requirements[0])
                self.assertNotIn("Unlock: Attraction Flow", flattened)
                self.assertIn("Unlock: Wall Run", flattened)

        for source_id in (
            "VBonus_DLC_001",
            "VBonus_DLC_002",
            "VBonus_DLC_003",
            "VBonus_DLC_004",
            "VBonus_DLC_005",
            "VBonus_DLC_006",
            "VBonus_DLC_007",
        ):
            source_key = ("VBonus", source_id)
            with self.subTest(source_key=source_key):
                requirements = VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key)
                self.assertEqual(requirements, expected_without_wall_run)
                flattened = set().union(*requirements[0])
                self.assertNotIn("Unlock: Attraction Flow", flattened)
                self.assertNotIn("Unlock: Wall Run", flattened)

    def test_keyblade_graveyard_victory_bonuses_require_common_combat_and_attraction_flow_notes(self) -> None:
        self.world_setup()

        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }

        expected_common_combat_and_attraction_flow = ((
            frozenset({"Ability: Block"}),
            frozenset({"Ability: Air Slide"}),
            frozenset({"Magic: Cure"}),
            frozenset({"Ability: Dodge Roll"}),
            frozenset({"Unlock: Attraction Flow"}),
            frozenset({"Unlock: Magic"}),
        ),)
        expected_common_combat_attraction_flow_and_wall_run = ((
            frozenset({"Unlock: Wall Run"}),
            *expected_common_combat_and_attraction_flow[0],
        ),)
        for source_id in (
            "Vbonus_068",
            "Vbonus_069",
            "Vbonus_070",
            "Vbonus_071",
            "Vbonus_072",
            "Vbonus_073",
            "Vbonus_074",
            "Vbonus_076",
        ):
            source_key = ("VBonus", source_id)
            with self.subTest(source_key=source_key):
                self.assertEqual(
                    VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key),
                    expected_common_combat_and_attraction_flow,
                )

        self.assertEqual(
            VICTORY_BONUS_LOGIC_REQUIREMENTS.get(("VBonus", "Vbonus_075")),
            expected_common_combat_attraction_flow_and_wall_run,
        )

        expected_attraction_flow_only = ((frozenset({"Unlock: Attraction Flow"}),),)
        for source_id in ("Vbonus_083", "Vbonus_084"):
            source_key = ("VBonus", source_id)
            with self.subTest(source_key=source_key):
                self.assertEqual(VICTORY_BONUS_LOGIC_REQUIREMENTS.get(source_key), expected_attraction_flow_only)

        regular_source_key = ("VBonus", "Vbonus_068")
        self.assertIn(regular_source_key, source_key_to_location)
        regular_location = source_key_to_location[regular_source_key]

        world_only = self._state_without_precollected_items()
        self._collect_higher_tier_gate(world_only)
        world_only.collect(self.world.create_item("Starlight"))
        world_only.collect(self.world.create_item("Wheel of Fate"))
        self.assertFalse(regular_location.can_reach(world_only))

        missing_attraction_flow = self._state_without_precollected_items()
        self._collect_higher_tier_gate(missing_attraction_flow)
        missing_attraction_flow.collect(self.world.create_item("Starlight"))
        missing_attraction_flow.collect(self.world.create_item("Wheel of Fate"))
        missing_attraction_flow.collect(self.world.create_item("Ability: Block"))
        missing_attraction_flow.collect(self.world.create_item("Ability: Air Slide"))
        missing_attraction_flow.collect(self.world.create_item("Magic: Cure"))
        missing_attraction_flow.collect(self.world.create_item("Ability: Dodge Roll"))
        missing_attraction_flow.collect(self.world.create_item("Unlock: Magic"))
        self.assertFalse(regular_location.can_reach(missing_attraction_flow))

        full_access = self._state_without_precollected_items()
        self._collect_higher_tier_gate(full_access)
        full_access.collect(self.world.create_item("Starlight"))
        full_access.collect(self.world.create_item("Wheel of Fate"))
        full_access.collect(self.world.create_item("Ability: Block"))
        full_access.collect(self.world.create_item("Ability: Air Slide"))
        full_access.collect(self.world.create_item("Magic: Cure"))
        full_access.collect(self.world.create_item("Ability: Dodge Roll"))
        full_access.collect(self.world.create_item("Unlock: Magic"))
        full_access.collect(self.world.create_item("Unlock: Attraction Flow"))
        self.assertTrue(regular_location.can_reach(full_access))

    def test_olympus_forge_inherits_movement_requirements(self) -> None:
        self.world_setup()

        source_key = ("Event", "EVENT_001")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertNotIn(source_key, EVENT_LOGIC_REQUIREMENTS)
        self.assertEqual(
            LOCATION_LOGIC_DEPENDENCIES[source_key],
            require_all_locations(("VBonus", "Vbonus_011")),
        )
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        world_only = self._state_without_precollected_items()
        world_only.collect(self.world.create_item("Hero's Origin"))
        self.assertFalse(location.can_reach(world_only))

        wall_run = self._state_without_precollected_items()
        wall_run.collect(self.world.create_item("Hero's Origin"))
        wall_run.collect(self.world.create_item("Unlock: Wall Run"))
        self.assertTrue(location.can_reach(wall_run))

    def test_san_fransokyo_rescues_require_each_element(self) -> None:
        self.world_setup()
        locations = [
            self.get_location_by_source("VBonus", source_id)
            for source_id in ("Vbonus_057", "Vbonus_055", "Vbonus_056")
        ] + [
            self.get_location_by_source("Event", source_id)
            for source_id in ("EVENT_009", "EVENT_KEYBLADE_009")
        ]
        required_items = (
            "Ability: Pole Spin", "Unlock: Magic", "Magic: Fire", "Magic: Thunder",
        )
        for alternative in ("Magic: Blizzard", "Magic: Water"):
            state = self._state_without_precollected_items()
            for item_name in ("Nano Gear", "Happy Gear", *required_items, alternative):
                state.collect(self.world.create_item(item_name))
            for location in locations:
                with self.subTest(alternative=alternative, location=location.name):
                    self.assertTrue(location.can_reach(state))

            for missing_item in (*required_items, alternative):
                missing = state.copy()
                missing.remove(self.world.create_item(missing_item))
                missing.collect(self.world.create_item("Magic: Aero"))
                for location in locations:
                    with self.subTest(alternative=alternative, missing=missing_item,
                                      location=location.name):
                        self.assertFalse(location.can_reach(missing))

    def test_csv_event_logic_uses_note_only_combat_requirements(self) -> None:
        self.world_setup()

        source_key = ("Event", "EVENT_DATAB_007")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, EVENT_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        state = self._state_without_precollected_items()
        self._collect_higher_tier_gate(state)
        for item_name in (
            "Oathkeeper",
            "Ultima Weapon",
            "Ability: Block",
            "Ability: Air Slide",
            "Ability: Glide",
            "Ability: Counter Slash",
            "Ability: Counter Kick",
            "Ability: Second Chance",
            "Ability: Withstand Combo",
            "Magic: Cure",
        ):
            state.collect(self.world.create_item(item_name))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Magic: Fire"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Unlock: Magic"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Dodge Roll"))
        self.assertTrue(location.can_reach(state))

    def test_csv_event_logic_data_battle_requires_counter_and_both_survival_abilities(self) -> None:
        self.world_setup()

        source_key = ("Event", "EVENT_DATAB_001")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, EVENT_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        state = self._state_without_precollected_items()
        self._collect_higher_tier_gate(state)
        for item_name in (
            "Oathkeeper",
            "Ultima Weapon",
            "Ability: Block",
            "Ability: Air Slide",
            "Ability: Dodge Roll",
            "Ability: Glide",
            "Magic: Cure",
            "Unlock: Magic",
        ):
            state.collect(self.world.create_item(item_name))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Counter Slash"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Second Chance"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Withstand Combo"))
        self.assertTrue(location.can_reach(state))

        state.remove(self.world.create_item("Ability: Second Chance"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Second Chance"))
        state.remove(self.world.create_item("Ability: Counter Slash"))
        state.collect(self.world.create_item("Ability: Counter Kick"))
        self.assertTrue(location.can_reach(state))

    def test_keyblade_graveyard_event_requires_attraction_flow(self) -> None:
        self.world_setup()

        source_key = ("Event", "EVENT_KEYBLADE_011")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, EVENT_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        missing_attraction_flow = self._state_without_precollected_items()
        self._collect_higher_tier_gate(missing_attraction_flow)
        missing_attraction_flow.collect(self.world.create_item("Starlight"))
        missing_attraction_flow.collect(self.world.create_item("Wheel of Fate"))
        missing_attraction_flow.collect(self.world.create_item("Ability: Block"))
        missing_attraction_flow.collect(self.world.create_item("Ability: Air Slide"))
        missing_attraction_flow.collect(self.world.create_item("Ability: Dodge Roll"))
        missing_attraction_flow.collect(self.world.create_item("Magic: Cure"))
        self.assertFalse(location.can_reach(missing_attraction_flow))

        full_note_logic = self._state_without_precollected_items()
        self._collect_higher_tier_gate(full_note_logic)
        full_note_logic.collect(self.world.create_item("Starlight"))
        full_note_logic.collect(self.world.create_item("Wheel of Fate"))
        full_note_logic.collect(self.world.create_item("Ability: Block"))
        full_note_logic.collect(self.world.create_item("Ability: Air Slide"))
        full_note_logic.collect(self.world.create_item("Ability: Dodge Roll"))
        full_note_logic.collect(self.world.create_item("Magic: Cure"))
        full_note_logic.collect(self.world.create_item("Unlock: Attraction Flow"))
        full_note_logic.collect(self.world.create_item("Unlock: Magic"))
        self.assertTrue(location.can_reach(full_note_logic))

    def test_csv_event_logic_saix_uses_shared_data_battle_requirements(self) -> None:
        self.world_setup()

        source_key = ("Event", "EVENT_DATAB_008")
        source_key_to_location = {
            (location_data["source_table"], location_data["source_id"]):
                self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
        }
        self.assertIn(source_key, EVENT_LOGIC_REQUIREMENTS)
        if source_key not in source_key_to_location:
            return

        location = source_key_to_location[source_key]
        state = self._state_without_precollected_items()
        self._collect_higher_tier_gate(state)
        for item_name in (
            "Oathkeeper",
            "Ultima Weapon",
            "Ability: Block",
            "Ability: Air Slide",
            "Ability: Dodge Roll",
            "Ability: Counter Slash",
            "Ability: Counter Kick",
            "Ability: Second Chance",
            "Ability: Withstand Combo",
            "Magic: Cure",
            "Unlock: Magic",
        ):
            state.collect(self.world.create_item(item_name))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Ability: Glide"))
        self.assertFalse(location.can_reach(state))

        state.collect(self.world.create_item("Magic: Blizzard"))
        self.assertTrue(location.can_reach(state))

    def test_level_up_checks_keep_standard_fill_without_extra_weighting(self) -> None:
        self.world_setup()

        fill_locations = self.multiworld.get_unfilled_locations(self.player)
        level_up_locations = [
            location
            for location in fill_locations
            if location.name.startswith("Level Ups - ")
        ]

        self.world.fill_hook(self.multiworld.itempool, [], [], fill_locations)

        # The progression weighting applies only to the normal reward range;
        # every higher level is already strictly junk.
        level_up_locations = [location for location in level_up_locations
                              if int(location.name.rsplit(" ", 1)[-1]) <= self.world.options.level_up_limit.value]

        progression_item = self.world.create_item("Magic: Fire")
        filler_item = self.world.create_item("Potion")
        restricted_locations = [
            location
            for location in level_up_locations
            if not location.item_rule(progression_item)
        ]

        self.assertEqual(len(restricted_locations), 0)
        for location in restricted_locations:
            self.assertTrue(location.item_rule(filler_item))

    def test_level_up_path_rewards_are_all_suppressed(self) -> None:
        default_options = {
            "LevelUp": {
                "2": {
                    "TypeA": "ETresAbilityKind::COMBO_PLUS\0",
                    "TypeB": "ETresAbilityKind::FIRE_UP\0",
                    "TypeC": "ETresAbilityKind::AIRCOMBO_PLUS\0",
                },
                "50": {
                    "TypeA": "ETresAbilityKind::CRITICAL_HALF\0",
                    "TypeB": "ETresAbilityKind::AIRCOMBO_UP\0",
                    "TypeC": "ETresAbilityKind::MAGIC_COMBO_UP\0",
                },
            }
        }

        suppressed = suppress_level_up_path_rewards(default_options)

        expected_none_row = {
            "TypeA": "ETresAbilityKind::NONE\0",
            "TypeB": "ETresAbilityKind::NONE\0",
            "TypeC": "ETresAbilityKind::NONE\0",
        }
        self.assertEqual(default_options["LevelUp"]["2"], expected_none_row)
        self.assertEqual(default_options["LevelUp"]["50"], expected_none_row)
        self.assertEqual([entry["source_id"] for entry in suppressed], ["2", "50"])
        self.assertEqual(
            suppressed[0]["previous_fields"]["TypeA"],
            "ETresAbilityKind::COMBO_PLUS\0",
        )

    def test_lucky_emblem_limit_suppresses_native_rewards_above_limit(self) -> None:
        default_options = {
            "LuckyMark": {
                "20": {"Reward": "ITEM_MAGICUP\0"},
                "30": {"Reward": "PRT_ITEM37\0"},
                "90": {"Reward": "ACC_ITEM31\0"},
            }
        }

        seed_material = "unit-test-seed"
        suppressed = suppress_lucky_mark_rewards_above_limit(
            default_options,
            20,
            seed_material=seed_material,
        )

        self.assertEqual(default_options["LuckyMark"]["20"]["Reward"], "ITEM_MAGICUP\0")
        self.assertEqual(
            default_options["LuckyMark"]["30"]["Reward"],
            f"{select_lucky_mark_limit_filler_token('30', seed_material)}\0",
        )
        self.assertEqual(
            default_options["LuckyMark"]["90"]["Reward"],
            f"{select_lucky_mark_limit_filler_token('90', seed_material)}\0",
        )
        self.assertEqual([entry["source_id"] for entry in suppressed], ["30", "90"])
        self.assertTrue(
            {entry["runtime_token"] for entry in suppressed}
            <= set(LUCKY_MARK_LIMIT_FILLER_TOKENS)
        )

    def test_lucky_emblem_limit_fillers_exclude_bridge_only_munny_pouches(self) -> None:
        self.assertTrue(LUCKY_MARK_LIMIT_FILLER_TOKENS)
        self.assertTrue(
            MUNNY_POUCH_RUNTIME_TOKENS.isdisjoint(LUCKY_MARK_LIMIT_FILLER_TOKENS)
        )

        reported_seed_material = "AP_73327341830603876864.zip"
        for milestone in ("60", "70"):
            with self.subTest(milestone=milestone):
                self.assertNotIn(
                    select_lucky_mark_limit_filler_token(milestone, reported_seed_material),
                    MUNNY_POUCH_RUNTIME_TOKENS,
                )

    def test_configurable_lucky_emblem_rows_add_synthetic_milestones(self) -> None:
        default_options = {
            "LuckyMark": {
                "1": {"Reward": "ITEM_APUP\0"},
                "3": {"Reward": "ITEM_MEGAPOTION\0"},
                "90": {"Reward": "ACC_ITEM31\0"},
            },
        }
        bridge_locations = [
            {"source_table": "LuckyMark", "source_id": str(milestone)}
            for milestone in (1, 2, 3)
        ]

        result = configure_lucky_mark_batch_rows(
            default_options,
            {"lucky_emblems_per_check": 1},
            bridge_locations,
            seed_material="unit-test-seed",
        )

        self.assertEqual(set(default_options["LuckyMark"]), {"1", "2", "3", "90"})
        self.assertEqual(result["batch_size"], 1)
        self.assertEqual(result["active_milestones"], [1, 2, 3])
        self.assertEqual(result["added_milestones"], [2])
        self.assertTrue(result["completion_row_added"])
        self.assertIn(
            default_options["LuckyMark"]["2"]["Reward"].rstrip("\0"),
            LUCKY_MARK_LIMIT_FILLER_TOKENS,
        )

    def test_configurable_lucky_emblem_rows_keep_completion_row_when_interval_exceeds_limit(self) -> None:
        default_options = {
            "LuckyMark": {
                "1": {"Reward": "ITEM_APUP\0"},
                "90": {"Reward": "ACC_ITEM31\0"},
            },
        }

        result = configure_lucky_mark_batch_rows(
            default_options,
            {
                "pools": {"Lucky Emblems": KH3LocationPoolMode.option_randomize},
                "lucky_emblems_per_check": 90,
            },
            [],
            seed_material="unit-test-seed",
        )

        self.assertEqual(set(default_options["LuckyMark"]), {"90"})
        self.assertEqual(result["active_milestones"], [])
        self.assertTrue(result["completion_row_added"])

    def test_export_supports_one_check_for_every_lucky_emblem(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "LuckyMark": {
                    str(milestone): {"Reward": "ITEM_POTION\0"}
                    for milestone in (1, 3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90)
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Potion", "id": "ITEM_POTION"},
                ],
                "locations": [],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263090000 + milestone,
                    "ap_location_name": f"Lucky Emblems - Milestone {milestone}",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Potion",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": f"LuckyMark:{milestone}",
                    "source_id": str(milestone),
                    "source_table": "LuckyMark",
                    "type": "lucky_emblem",
                }
                for milestone in range(1, 91)
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_one-emblem-checks.zip",
                randomizer_config={
                    "pools": {"Lucky Emblems": 2},
                    "limiters": {"Lucky Emblem Limit": 90},
                    "lucky_emblems_per_check": 1,
                },
            )

            self.assertEqual(
                set(result.randomized_options["LuckyMark"]),
                {str(milestone) for milestone in range(1, 91)},
            )
            self.assertTrue(all(
                row["Reward"].startswith("ITEM_AP_I02")
                for row in result.randomized_options["LuckyMark"].values()
            ))
            self.assertEqual(result.placement_summary["location_count"], 90)
            self.assertEqual(result.placement_summary["lucky_emblems_per_check"], 1)
            self.assertEqual(
                result.placement_summary["lucky_mark_batch_rows"]["active_milestones"],
                list(range(1, 91)),
            )
            self.assertFalse(
                result.placement_summary["lucky_mark_batch_rows"]["completion_row_added"]
            )

    def test_lucky_emblem_limit_requires_active_checks(self) -> None:
        randomizer_config = {
            "pools": {"Lucky Emblems": 3},
            "limiters": {"Lucky Emblem Limit": 20},
        }
        bridge_locations = [{"source_table": "LuckyMark", "source_id": "20"}]

        self.assertEqual(read_lucky_emblem_limit_for_export(randomizer_config, bridge_locations), 20)

        randomizer_config["pools"]["Lucky Emblems"] = 2
        self.assertIsNone(read_lucky_emblem_limit_for_export(randomizer_config, []))
        self.assertEqual(read_lucky_emblem_limit_for_export(randomizer_config, bridge_locations), 20)

    def test_always_on_event_rewards_are_suppressed_in_exact_export(self) -> None:
        default_options = {
            "Event": {
                event_id: {"RandomizedItem": f"{event_id}_OLD\0"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
        }

        suppressed = suppress_always_on_event_rewards(default_options)

        self.assertEqual(len(suppressed), len(ALWAYS_ON_EVENT_IDS))
        for event_id in ALWAYS_ON_EVENT_IDS:
            self.assertEqual(
                default_options["Event"][event_id]["RandomizedItem"],
                f"{SUPPRESSED_ALWAYS_ON_EVENT_TOKEN}\0",
            )
            self.assertIn({
                "event_id": event_id,
                "field_name": "RandomizedItem",
                "previous_token": f"{event_id}_OLD\0",
                "runtime_token": SUPPRESSED_ALWAYS_ON_EVENT_TOKEN,
            }, suppressed)

    def test_export_suppresses_foreign_event_and_vbonus_native_rewards(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            event_table = {
                event_id: {"RandomizedItem": "ITEM_POTION\0"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
            event_table["EVENT_008"] = {"RandomizedItem": "ACC_ITEM01\0"}
            default_path.write_text(json.dumps({
                "Event": event_table,
                "VBonus": {
                    "Vbonus_038": {
                        "Sora_Bonus1": "ETresVictoryBonusKind::ITEM_SLOT_UP1\0",
                        "Sora_Bonus2": "ETresVictoryBonusKind::NONE\0",
                        "Sora_Ability1": "ETresAbilityKind::NONE\0",
                        "Sora_Ability2": "ETresAbilityKind::NONE\0",
                    },
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [],
                "locations": [
                    {
                        "source_table": "VBonus",
                        "source_id": "Vbonus_038",
                        "vanilla_item_names": {
                            "Sora_Bonus1": "Bonus: Item Slot +1",
                            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                            "Sora_Ability1": "ETresAbilityKind::NONE",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                        },
                        "vanilla_items": {
                            "Sora_Bonus1": "ETresVictoryBonusKind::ITEM_SLOT_UP1",
                            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                            "Sora_Ability1": "ETresAbilityKind::NONE",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                        },
                    }
                ],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023116,
                    "ap_location_name": "Monstropolis - EVENT_008 - RandomizedItem",
                    "assigned_item_game": "Other Game",
                    "assigned_item_name": "Foreign Event Item",
                    "assigned_item_player": 2,
                    "assigned_item_player_name": "Other",
                    "bridge_key": "Event:EVENT_008",
                    "source_id": "EVENT_008",
                    "source_table": "Event",
                    "subtype": "RandomizedItem",
                    "type": "event",
                },
                {
                    "ap_location_id": 1263023209,
                    "ap_location_name": "Monstropolis - Victory Bonus 038",
                    "assigned_item_game": "Other Game",
                    "assigned_item_name": "Foreign VBonus Item",
                    "assigned_item_player": 2,
                    "assigned_item_player_name": "Other",
                    "bridge_key": "VBonus:Vbonus_038",
                    "source_id": "Vbonus_038",
                    "source_table": "VBonus",
                    "type": "vbonus",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["Event"]["EVENT_008"]["RandomizedItem"],
                "ETresVictoryBonusKind::NONE\0",
            )
            self.assertEqual(
                result.randomized_options["VBonus"]["Vbonus_038"]["Sora_Bonus1"],
                "ETresVictoryBonusKind::NONE\0",
            )
            self.assertEqual(
                result.foreign_item_manifest["foreign_items"][0]["placeholder_runtime_token"],
                "AP_ITEM_000",
            )
            self.assertEqual(
                result.foreign_item_manifest["foreign_items"][0]["placeholder_token"],
                "AP_ITEM_1263023116",
            )
            self.assertEqual(
                result.foreign_item_manifest["foreign_items"][0]["item_display_name"],
                "Foreign Event Item for Other",
            )
            self.assertEqual(
                result.foreign_item_manifest["foreign_items"][1]["placeholder_token"],
                "AP_ITEM_1263023209",
            )
            self.assertEqual(
                result.foreign_item_manifest["foreign_items"][1]["item_display_name"],
                "Foreign VBonus Item for Other",
            )
            self.assertEqual(
                result.placement_summary["locations"][0]["export_token"],
                "AP_ITEM_1263023116",
            )
            self.assertEqual(
                result.placement_summary["locations"][0]["item_name"],
                "Foreign Event Item",
            )
            self.assertEqual(
                result.placement_summary["locations"][0]["item_display_name"],
                "Foreign Event Item for Other",
            )
            self.assertEqual(
                result.placement_summary["locations"][0]["native_reward_token"],
                "ETresVictoryBonusKind::NONE",
            )
            self.assertEqual(
                result.placement_summary["locations"][1]["native_reward_token"],
                "ETresVictoryBonusKind::NONE",
            )
            self.assertEqual(
                result.placement_summary["locations"][0]["runtime_token"],
                "AP_ITEM_000",
            )

    def test_export_enforces_lucky_emblem_limit_for_non_ap_milestones(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "LuckyMark": {
                    "20": {"Reward": "ITEM_MAGICUP\0"},
                    "30": {"Reward": "PRT_ITEM37\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Hero's Glove", "id": "PRT_ITEM03"},
                    {"name": "Potion", "id": "ITEM_POTION"},
                ],
                "locations": [],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023264,
                    "ap_location_name": "Lucky Emblems - Milestone 20",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Hero's Glove",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "LuckyMark:20",
                    "source_id": "20",
                    "source_table": "LuckyMark",
                    "type": "lucky_emblem",
                },
            ]
            randomizer_config = {
                "pools": {"Lucky Emblems": 2},
                "limiters": {"Lucky Emblem Limit": 20},
            }

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
                randomizer_config=randomizer_config,
            )

            self.assertNotIn(
                result.randomized_options["LuckyMark"]["20"]["Reward"].rstrip("\0"),
                LUCKY_MARK_LIMIT_FILLER_TOKENS,
            )
            self.assertEqual(
                result.randomized_options["LuckyMark"]["30"]["Reward"],
                f"{select_lucky_mark_limit_filler_token('30', 'AP_test.zip')}\0",
            )
            self.assertEqual(result.placement_summary["lucky_emblem_limit"], 20)
            self.assertEqual(
                [
                    entry["source_id"]
                    for entry in result.placement_summary["suppressed_lucky_emblem_limit_rewards"]
                ],
                ["30"],
            )

    def test_export_maps_local_lucky_emblem_ability_to_native_ap_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "LuckyMark": {
                    "30": {"Reward": "PRT_ITEM37\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Ability: Pole Spin", "id": "ETresAbilityKind::POLE_SPIN"},
                ],
                "locations": [],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023266,
                    "ap_location_name": "Lucky Emblems - Milestone 30",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Ability: Pole Spin",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "LuckyMark:30",
                    "source_id": "30",
                    "source_table": "LuckyMark",
                    "type": "lucky_emblem",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["LuckyMark"]["30"]["Reward"],
                "ITEM_AP_I02200_1263023266\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], "ITEM_AP_I02200_1263023266")
            self.assertEqual(placement["native_reward_token"], "ITEM_AP_I02200_1263023266")
            self.assertEqual(placement["runtime_token"], "ETresAbilityKind::POLE_SPIN")
            self.assertEqual(placement["item_display_name"], "Ability: Pole Spin")
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertTrue(bridge_locations[0]["bridge_delivery_required"])

    def test_export_maps_local_lucky_emblem_munny_pouch_to_native_ap_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "LuckyMark": {
                    "10": {"Reward": "ITEM_APUP\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [],
                "locations": [],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023261,
                    "ap_location_name": "Lucky Emblems - Milestone 10",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": LARGE_MUNNY_POUCH_ITEM_NAME,
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "LuckyMark:10",
                    "source_id": "10",
                    "source_table": "LuckyMark",
                    "type": "lucky_emblem",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            native_ap_token = "ITEM_AP_I02200_1263023261"
            self.assertEqual(
                result.randomized_options["LuckyMark"]["10"]["Reward"],
                f"{native_ap_token}\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], native_ap_token)
            self.assertEqual(placement["native_reward_token"], native_ap_token)
            self.assertEqual(placement["runtime_token"], LARGE_MUNNY_POUCH_RUNTIME_TOKEN)
            self.assertEqual(placement["item_display_name"], LARGE_MUNNY_POUCH_ITEM_NAME)
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertTrue(bridge_locations[0]["bridge_delivery_required"])

    def test_export_maps_local_lucky_emblem_native_item_to_native_ap_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "LuckyMark": {
                    "1": {"Reward": "ITEM_APUP\0"},
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"name": "Hero's Glove", "id": "PRT_ITEM03"},
                ],
                "locations": [],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023258,
                    "ap_location_name": "Lucky Emblems - Milestone 1",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_name": "Hero's Glove",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "LuckyMark:1",
                    "source_id": "1",
                    "source_table": "LuckyMark",
                    "type": "lucky_emblem",
                },
            ]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            self.assertEqual(
                result.randomized_options["LuckyMark"]["1"]["Reward"],
                "ITEM_AP_I02200_1263023258\0",
            )
            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], "ITEM_AP_I02200_1263023258")
            self.assertEqual(placement["native_reward_token"], "ITEM_AP_I02200_1263023258")
            self.assertEqual(placement["runtime_token"], "PRT_ITEM03")
            self.assertEqual(placement["item_display_name"], "Hero's Glove")
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertTrue(bridge_locations[0]["bridge_delivery_required"])

class TestKH3WorldStartingControllerUnlockOptions(KH3TestBase):
    options = {
        "start_with_magic": 1,
    }

    def test_starting_controller_unlock_options_remove_unlock_items_from_pool(self) -> None:
        self.world_setup()

        slot_data = self.world.build_runtime_slot_data()
        item_pool_names = [item.name for item in self.multiworld.itempool]
        precollected_item_names = [
            item.name for item in self.multiworld.precollected_items[self.player]
        ]

        self.assertTrue(slot_data["start_with_magic"])
        self.assertTrue(slot_data["start_with_airstep"])
        self.assertNotIn("controller_unlocks", slot_data["kh3_randomizer"])
        self.assertIn("Unlock: Magic", precollected_item_names)
        self.assertIn("Unlock: Airstep", precollected_item_names)
        self.assertNotIn("Unlock: Magic", item_pool_names)
        self.assertNotIn("Unlock: Airstep", item_pool_names)
        self.assertIn("Unlock: Wall Run", item_pool_names)
        self.assertNotIn("Unlock: Style Change", item_pool_names)
        self.assertIn("Unlock: Attraction Flow", item_pool_names)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

class TestKH3StylesanityOn(KH3TestBase):
    options = {
        "stylesanity": "on",
    }

    def test_keyblade_style_items_replace_global_style_change(self) -> None:
        self.world_setup()

        item_pool_names = [item.name for item in self.multiworld.itempool]
        counts = Counter(item_pool_names)
        self.assertNotIn(STYLE_CHANGE_UNLOCK_ITEM, item_pool_names)
        for item_name in KEYBLADE_STYLE_ITEM_NAMES:
            with self.subTest(item_name=item_name):
                self.assertEqual(counts[item_name], 1)
                self.assertEqual(self.world.create_item(item_name).classification, ItemClassification.useful)

        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["stylesanity"], 1)
        self.assertNotIn("style_change", slot_data)
        self.assertNotIn(
            STYLE_CHANGE_UNLOCK_ITEM,
            [item.name for item in self.multiworld.precollected_items[self.player]],
        )
        self.assertNotIn(
            STYLE_CHANGE_UNLOCK_ITEM,
            slot_data["bridge"]["goal_required_item_counts"],
        )
        self.assertNotIn(
            STYLE_CHANGE_UNLOCK_ITEM,
            slot_data["kh3_randomizer"]["hint"]["checks"],
        )
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations(self.player)))

    def test_goal_no_longer_requires_global_style_change(self) -> None:
        self.world_setup()

        completion_location = self.multiworld.get_location(COMPLETION_LOCATION_NAME, self.player)
        state = CollectionState(self.multiworld)
        for _ in range(7):
            state.collect(self.world.create_item("Heart Piece"))
        self.assertTrue(completion_location.can_reach(state))


class TestKH3SevenHeartPiecesGoalLogic(KH3TestBase):
    options = {
        "heart_piece_count": 7,
    }

    def test_final_door_requires_seven_heart_pieces(self) -> None:
        self.world_setup()

        completion_location = self.multiworld.get_location(COMPLETION_LOCATION_NAME, self.player)
        final_door_region = self.multiworld.get_region("Final Door", self.player)
        state = CollectionState(self.multiworld)

        self.assertFalse(final_door_region.can_reach(state))
        self.assertFalse(completion_location.can_reach(state))

        for _ in range(HEART_PIECE_GOAL_COUNT - 1):
            state.collect(self.world.create_item(HEART_PIECE_ITEM_NAME))
        self.assertFalse(completion_location.can_reach(state))

        state.collect(self.world.create_item(HEART_PIECE_ITEM_NAME))
        self.assertTrue(final_door_region.can_reach(state))
        self.assertTrue(completion_location.can_reach(state))

    def test_level_ups_reject_selected_goal_items_only(self) -> None:
        self.world_setup()

        level_up_location = next(
            location
            for location in self.multiworld.get_locations(self.player)
            if location.name.startswith("Level Ups - ")
        )

        self.assertFalse(level_up_location.item_rule(self.world.create_item(HEART_PIECE_ITEM_NAME)))
        self.assertTrue(level_up_location.item_rule(self.world.create_item(STYLE_CHANGE_UNLOCK_ITEM)))
        self.assertTrue(level_up_location.item_rule(self.world.create_item("Potion")))


class TestKH3WorldStartingWallRunLogic(KH3TestBase):
    options = {
        "start_with_wall_run": 1,
    }

    def test_starting_wall_run_satisfies_csv_logic_without_pool_item(self) -> None:
        self.world_setup()

        item_pool_names = [item.name for item in self.multiworld.itempool]
        self.assertNotIn("Unlock: Wall Run", item_pool_names)

        target_location = next(
            self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
            if (location_data["source_table"], location_data["source_id"]) == ("TreasureHE", "HE_SBOX_017")
        )
        state = CollectionState(self.multiworld)
        state.collect(self.world.create_item("Hero's Origin"))

        self.assertTrue(target_location.can_reach(state))

    def test_starting_wall_run_does_not_make_olympus_a_grindable_xp_source(self) -> None:
        self.world_setup()

        level_3 = self.multiworld.get_location("Level Ups - Sora Level 3", self.player)
        state = CollectionState(self.multiworld)
        for item in self.multiworld.precollected_items[self.player]:
            if item.name in PORTAL_KEYBLADE_ITEM_NAMES:
                state.remove(item)
        state.collect(self.world.create_item("Hero's Origin"))

        self.assertFalse(level_3.can_reach(state))

        state.collect(self.world.create_item("Favorite Deputy"))
        self.assertTrue(level_3.can_reach(state))

class TestKH3WorldDefaultBaseAbilitiesOff(KH3TestBase):
    options = {
        "default_base_abilities": 0,
    }

    def test_default_base_ability_pool_includes_randomized_chrinit_copy(self) -> None:
        self.world_setup()

        owned_item_counts = Counter(
            item.name
            for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        )

        for item_name in DEFAULT_BASE_ABILITY_ITEM_NAMES:
            expected_count = 3 if item_name in {"Ability: Air Slide", "Ability: Superslide"} else 1
            expected_count += int(item_name in RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS)
            with self.subTest(item_name=item_name):
                self.assertEqual(owned_item_counts[item_name], expected_count)
        self.assertEqual(owned_item_counts["Ability: Zero EXP"], 0)

class TestKH3WorldDefaultBaseAbilitiesOn(KH3TestBase):
    options = {
        "default_base_abilities": 1,
    }

    def test_default_base_ability_pool_keeps_non_starting_copies(self) -> None:
        self.world_setup()

        available_item_counts = Counter(
            item.name
            for item in self.multiworld.itempool + self.multiworld.precollected_items[self.player]
        )

        for item_name in DEFAULT_BASE_ABILITY_ITEM_NAMES:
            expected_count = 2 if item_name in {"Ability: Air Slide", "Ability: Superslide"} else 0
            expected_count += int(item_name in RETIRED_GROWTH_PATH_ABILITY_POOL_ITEMS)
            with self.subTest(item_name=item_name):
                self.assertEqual(available_item_counts[item_name], expected_count)
        self.assertEqual(available_item_counts["Ability: Zero EXP"], 0)

    def test_default_base_abilities_satisfy_csv_logic_without_pool_item(self) -> None:
        self.world_setup()

        target_location = next(
            self.multiworld.get_location(location_data["name"], self.player)
            for location_data in self.world.active_locations
            if (location_data["source_table"], location_data["source_id"]) == ("VBonus", "Vbonus_057")
        )
        state = CollectionState(self.multiworld)
        state.collect(self.world.create_item("Nano Gear"))
        state.collect(self.world.create_item("Happy Gear"))
        state.collect(self.world.create_item("Magic: Fire"))
        state.collect(self.world.create_item("Magic: Water"))
        state.collect(self.world.create_item("Unlock: Magic"))
        self.assertFalse(target_location.can_reach(state))
        state.collect(self.world.create_item("Magic: Thunder"))
        self.assertTrue(target_location.can_reach(state))


class TestKH3WorldUnrealityLogic(KH3WorldIntegrityMixin, KH3TestBase):
    options = {
        "unreality_pool": KH3LocationPoolMode.option_randomize,
        "default_base_abilities": 1,
    }

    def test_unreality_is_open_without_portal_keyblades(self) -> None:
        self.world_setup()

        unreality = self.multiworld.get_region("Unreality", self.player)
        state = self._state_without_precollected_items()

        self.assertTrue(unreality.can_reach(state))

    def test_yozora_checks_require_data_battle_combat(self) -> None:
        required_items = (
            "Ability: Block",
            "Ability: Air Slide",
            "Ability: Dodge Roll",
            "Magic: Cure",
            "Unlock: Magic",
            "Ability: Second Chance",
            "Ability: Withstand Combo",
            "Ability: Glide",
        )
        for default_base_abilities in (0, 1):
            self.options = {**self.options, "default_base_abilities": default_base_abilities}
            self.world_setup()
            implicit_items = DEFAULT_BASE_ABILITY_ITEM_NAMES if default_base_abilities else frozenset()
            for source_id in ("EVENT_KEYITEM_005", "EVENT_YOZORA_001"):
                location = self.get_location_by_source("Event", source_id)
                for counter in ("Ability: Counter Slash", "Ability: Counter Kick"):
                    with self.subTest(default_base_abilities=default_base_abilities,
                                      source_id=source_id, counter=counter):
                        state = self._state_without_precollected_items()
                        for item_name in (*required_items, counter):
                            if item_name not in implicit_items:
                                state.collect(self.world.create_item(item_name))
                        self.assertTrue(location.can_reach(state))
                        for item_name in (*required_items, counter):
                            if item_name in implicit_items:
                                continue
                            with self.subTest(missing=item_name):
                                state.remove(self.world.create_item(item_name))
                                self.assertFalse(location.can_reach(state))
                                state.collect(self.world.create_item(item_name))

    def test_yozora_event_requirements_match_common_data_fights(self) -> None:
        for source_id in ("EVENT_KEYITEM_005", "EVENT_YOZORA_001"):
            with self.subTest(source_id=source_id):
                self.assertEqual(
                    EVENT_LOGIC_REQUIREMENTS[("Event", source_id)],
                    EVENT_LOGIC_REQUIREMENTS[("Event", "EVENT_DATAB_001")],
                )


class TestKH3StylesanityOff(KH3TestBase):
    options = {"stylesanity": "off"}

    def test_shared_style_unlock_is_precollected_not_shuffled(self) -> None:
        names = [item.name for item in self.multiworld.itempool]
        precollected = [item.name for item in self.multiworld.precollected_items[self.player]]
        style_items = {*KEYBLADE_STYLE_ITEM_NAMES, STYLE_CHANGE_UNLOCK_ITEM}
        self.assertTrue(style_items.isdisjoint(names))
        self.assertTrue(set(KEYBLADE_STYLE_ITEM_NAMES).isdisjoint(precollected))
        self.assertEqual(precollected.count(STYLE_CHANGE_UNLOCK_ITEM), 1)
        self.assertIn(STYLE_CHANGE_UNLOCK_ITEM, self.world.kh3_starting_inventory_items)
        self.assertEqual(self.world.create_item(STYLE_CHANGE_UNLOCK_ITEM).classification, ItemClassification.useful)
        self.assertFalse(self.multiworld.state.has(STYLE_CHANGE_UNLOCK_ITEM, self.player))
        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["stylesanity"], 0)
        self.assertEqual(slot_data["universal_tracker"]["options"]["stylesanity"], 0)
        self.assertNotIn("style_change", slot_data)
        self.assertEqual(len(names), len(self.multiworld.get_unfilled_locations(self.player)))


class TestKH3DefaultStylesanity(TestKH3StylesanityOn):
    options = {}

    def test_default_is_per_keyblade(self) -> None:
        self.assertEqual(self.world.build_runtime_slot_data()["stylesanity"], 1)


class TestKH3StylePoolReplacement(KH3TestBase):
    def test_extra_styles_preserve_keyblades_with_generated_upgrades(self) -> None:
        # Force the non-filler replacement path and make it consume all eligible
        # useful items, rather than depending on random selection in a full seed.
        names = ["Starlight", "Progressive Starlight Level"] + ["Ability: HP Prize Plus"] * len(KEYBLADE_STYLE_ITEM_NAMES)
        pool = [self.world.create_item(name) for name in names]
        self.world._add_keyblade_style_item_pool_items(pool)
        counts = Counter(item.name for item in pool)
        self.assertEqual(counts["Starlight"], 1)
        self.assertEqual(counts["Progressive Starlight Level"], 1)
        self.assertEqual(counts["Ability: HP Prize Plus"], 0)
        self.assertTrue(all(counts[name] == 1 for name in KEYBLADE_STYLE_ITEM_NAMES))
        self.assertEqual(len(pool), len(names))
