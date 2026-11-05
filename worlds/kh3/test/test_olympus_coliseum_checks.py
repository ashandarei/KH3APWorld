from __future__ import annotations

from collections import Counter

from ..ColiseumLogic import COLISEUM_CUP_REQUIRED_WORLDS, COLISEUM_POSTGAME_CUP_IDS
from ..Data import (
    OLYMPUS_COLISEUM_CUP_IDS,
    OLYMPUS_COLISEUM_CUP_NAMES_BY_ID,
    OLYMPUS_COLISEUM_CUP_SOURCE_TABLE,
    PORTAL_KEYBLADE_REGION_LOCKS,
    goal_required_item_counts,
)
from ..Options import KH3Options, OlympusColiseumPool
from . import KH3TestBase
from .test_helpers import KH3WorldIntegrityMixin


def _olympus_coliseum_locations(world) -> list[dict]:
    return [
        location
        for location in world.active_locations
        if location.get("source_table") == OLYMPUS_COLISEUM_CUP_SOURCE_TABLE
    ]


class TestOlympusColiseumChecks(KH3WorldIntegrityMixin, KH3TestBase):
    def test_unreleased_sephiroth_has_no_location_or_bridge_check(self) -> None:
        location_name = "Olympus Coliseum - Secret - Sephiroth Match Clear"
        self.assertNotIn(location_name, self.world.location_name_to_id)
        for mode in (2, 3):
            with self.subTest(mode=mode):
                self.options = {"olympus_coliseum_pool": mode}
                self.world_setup(seed=206)
                self.assertEqual(len(_olympus_coliseum_locations(self.world)), 24)
                self.assertNotIn(
                    location_name,
                    {location.name for location in self.multiworld.get_locations(self.player)},
                )
                bridge_checks = [
                    location
                    for location in self.world.build_runtime_slot_data()["bridge"]["locations"]
                    if location.get("source_table") == OLYMPUS_COLISEUM_CUP_SOURCE_TABLE
                ]
                self.assertEqual(len(bridge_checks), 24)
                self.assertNotIn(
                    "series_secret/cup_sephiroth",
                    {location["source_id"] for location in bridge_checks},
                )

    def _state_excluding(self, *names):
        state = self.multiworld.get_all_state(False)
        for name in names:
            while state.has(name, self.player):
                state.remove(self.world.create_item(name))
        return state

    def test_all_cups_require_olympus_completion(self):
        self.options = {"olympus_coliseum_pool": 2}
        self.world_setup(seed=201)
        for missing in (PORTAL_KEYBLADE_REGION_LOCKS["Olympus"], "Unlock: Wall Run"):
            state = self._state_excluding(missing)
            for cup in _olympus_coliseum_locations(self.world):
                with self.subTest(missing=missing, cup=cup["source_id"]):
                    self.assertFalse(self.multiworld.get_location(cup["name"], self.player).can_reach(state))

    def test_every_cup_requires_both_worlds_in_its_native_gate(self):
        self.options = {"olympus_coliseum_pool": 2}
        self.world_setup(seed=202)
        self.assertEqual(set(COLISEUM_CUP_REQUIRED_WORLDS), set(OLYMPUS_COLISEUM_CUP_IDS))
        for cup_id, required_worlds in COLISEUM_CUP_REQUIRED_WORLDS.items():
            cup = self.get_location_by_source(OLYMPUS_COLISEUM_CUP_SOURCE_TABLE, cup_id)
            self.assertTrue(cup.can_reach(self.multiworld.get_all_state(False)))
            for region in required_worlds:
                with self.subTest(cup=cup_id, missing_world=region):
                    self.assertFalse(cup.can_reach(self._state_excluding(PORTAL_KEYBLADE_REGION_LOCKS[region])))

    def test_only_explicit_postgame_cups_require_the_final_door_goal(self):
        for goal in (3, 30):
            self.options = {"olympus_coliseum_pool": 2, "heart_piece_count": goal}
            self.world_setup(seed=203)
            state = self._state_excluding(*goal_required_item_counts(goal))
            for cup_id in OLYMPUS_COLISEUM_CUP_IDS:
                with self.subTest(goal=goal, cup=cup_id):
                    cup = self.get_location_by_source(OLYMPUS_COLISEUM_CUP_SOURCE_TABLE, cup_id)
                    self.assertEqual(cup.can_reach(state), cup_id not in COLISEUM_POSTGAME_CUP_IDS)

    def test_world_completion_uses_combat_rules_not_only_portal_access(self):
        self.options = {"olympus_coliseum_pool": 2}
        self.world_setup(seed=204)
        state = self._state_excluding("Magic: Fire", "Magic: Thunder")
        self.assertTrue(self.multiworld.get_region("San Fransokyo", self.player).can_reach(state))
        self.assertFalse(self.get_location_by_source(
            OLYMPUS_COLISEUM_CUP_SOURCE_TABLE, "series_kh2/cup_goddessoffate").can_reach(state))

    def test_junk_story_pools_keep_coliseum_story_gates(self):
        self.options = {"olympus_coliseum_pool": 3, "olympus_pool": 3, "twilight_town_pool": 3}
        self.world_setup(seed=205)
        cup = self.get_location_by_source(OLYMPUS_COLISEUM_CUP_SOURCE_TABLE, "series_kh1/cup_phil")
        self.assertTrue(cup.can_reach(self.multiworld.get_all_state(False)))
        self.assertFalse(cup.can_reach(self._state_excluding("Unlock: Wall Run")))
        self.assertFalse(cup.can_reach(self._state_excluding(PORTAL_KEYBLADE_REGION_LOCKS["Twilight Town"])))

    def test_default_is_off_and_adds_no_external_mod_locations(self) -> None:
        self.world_setup(seed=101)

        self.assertIn("olympus_coliseum_pool", KH3Options.type_hints)
        option_type = KH3Options.type_hints["olympus_coliseum_pool"]
        self.assertIs(option_type, OlympusColiseumPool)
        self.assertEqual(option_type.display_name, "Olympus Coliseum Mod Pool")
        self.assertEqual(option_type.default, option_type.option_off)
        self.assertIn("randomize", option_type.options)
        self.assertEqual(option_type.from_text("randomize").value, option_type.option_randomize)
        self.assertEqual(option_type.from_text("replace").value, option_type.option_junk)
        self.assertEqual(_olympus_coliseum_locations(self.world), [])
        self.assertEqual(self.world.build_runtime_slot_data()["olympus_coliseum_pool"], 1)

    def test_randomize_mode_adds_all_cups_and_allows_progression(self) -> None:
        self.options = {"olympus_coliseum_pool": 2}
        self.world_setup(seed=102)

        locations = _olympus_coliseum_locations(self.world)
        self.assertEqual(len(locations), len(OLYMPUS_COLISEUM_CUP_IDS))
        location = self.multiworld.get_location(locations[0]["name"], self.player)
        self.assertTrue(location.item_rule(self.world.create_item("Proof of Promises")))
        self.assertEqual(self.world.build_runtime_slot_data()["olympus_coliseum_pool"], 2)

    def test_randomize_mode_does_not_duplicate_native_keyblade_rewards(self) -> None:
        self.options = {"olympus_coliseum_pool": 2}
        self.world_setup(seed=104)

        player_items = [
            item
            for item in (
                self.multiworld.itempool
                + self.multiworld.precollected_items[self.player]
            )
            if item.player == self.player
        ]
        item_counts = Counter(item.name for item in player_items)
        self.assertEqual(item_counts["Oathkeeper"], 1)
        self.assertEqual(item_counts["Oblivion"], 1)
        self.assertEqual(
            {
                location["primary_item_name"]
                for location in _olympus_coliseum_locations(self.world)
            },
            {"Potion"},
        )

    def test_junk_mode_adds_all_cup_clears_as_bridge_delivered_checks(self) -> None:
        self.options = {"olympus_coliseum_pool": 3}
        self.world_setup(seed=103)

        locations = _olympus_coliseum_locations(self.world)
        self.assertEqual(len(locations), len(OLYMPUS_COLISEUM_CUP_IDS))
        self.assertEqual(
            {location["source_id"] for location in locations},
            set(OLYMPUS_COLISEUM_CUP_IDS),
        )
        self.assertEqual(
            {location["name"] for location in locations},
            {
                f"Olympus Coliseum - {cup_name} Clear"
                for cup_name in OLYMPUS_COLISEUM_CUP_NAMES_BY_ID.values()
            },
        )

        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["olympus_coliseum_pool"], 3)
        bridge_checks = [
            location
            for location in slot_data["bridge"]["locations"]
            if location.get("source_table") == OLYMPUS_COLISEUM_CUP_SOURCE_TABLE
        ]
        self.assertEqual(len(bridge_checks), len(OLYMPUS_COLISEUM_CUP_IDS))
        self.assertTrue(all(location["bridge_delivery_required"] for location in bridge_checks))
        location = self.multiworld.get_location(locations[0]["name"], self.player)
        self.assertTrue(location.filler_only)
        self.assertFalse(location.item_rule(self.world.create_item("Proof of Promises")))
