from unittest import TestCase

from BaseClasses import Item, ItemClassification
from Fill import distribute_items_restrictive
from test.general import setup_multiworld

from .. import KH3World
from ..Adversaries import ADVERSARIES, ADVERSARY_EXCLUSIONS
from ..SlotData import decode_slot_data, encode_slot_data
from . import KH3TestBase
from .test_helpers import KH3WorldIntegrityMixin


class TestAdversaryChecks(KH3WorldIntegrityMixin, KH3TestBase):
    options = {"adversary_checks": 1}

    def test_catalog_and_delivery(self):
        self.world_setup()
        checks = [r for r in self.world.active_locations if r["type"] == "adversary"]
        self.assertEqual(len(checks), 48)
        self.assertFalse(ADVERSARY_EXCLUSIONS & {e["name"] for e in ADVERSARIES})
        self.assertEqual({r["source_id"] for r in checks}, {e["id"] for e in ADVERSARIES})
        slots = self.world.build_runtime_slot_data()
        self.assertEqual(slots["adversary_checks"], 1)
        rows = [r for r in slots["bridge"]["locations"] if r["type"] == "adversary"]
        self.assertTrue(all(r["bridge_delivery_required"] for r in rows))
        self.assertEqual(decode_slot_data(encode_slot_data(slots)), slots)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_only_filler_and_useful_for_every_player(self):
        self.world_setup()
        for row in self.world.active_locations:
            if row["type"] != "adversary":
                continue
            location = self.multiworld.get_location(row["name"], self.player)
            for player in (self.player, self.player + 1):
                for classification in (ItemClassification.filler, ItemClassification.useful,
                                       ItemClassification.trap, ItemClassification.progression,
                                       ItemClassification.progression_skip_balancing,
                                       ItemClassification.progression | ItemClassification.useful,
                                       ItemClassification.useful | ItemClassification.trap):
                    with self.subTest(location=location.name, player=player, classification=classification):
                        item = Item("test", classification, 1, player)
                        self.assertEqual(location.item_rule(item), classification in (
                            ItemClassification.filler, ItemClassification.useful))

    def test_one_source_world_is_enough(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        soldier = self.multiworld.get_location("Adversaries - Soldier", self.player)
        self.assertFalse(soldier.can_reach(state))
        state.collect(self.world.create_item("Hero's Origin"))
        self.assertTrue(soldier.can_reach(state))

    def test_sorcerer_inherits_battlegate_story_gate(self):
        self.world_setup()
        state = self._state_without_precollected_items()
        sorcerer = self.multiworld.get_location("Adversaries - Sorcerer", self.player)
        self.assertFalse(sorcerer.can_reach(state))
        self.collect_all_but([])
        self.assertTrue(sorcerer.can_reach(self.multiworld.state))


class TestAdversaryGeneration(TestCase):
    def test_junk_worlds_and_randomized_enemies(self):
        mw = setup_multiworld(KH3World, seed=33, options={
            "adversary_checks": 1, "enemies_pool": "randomize", "trap_percentage": 100,
            "olympus_pool": "junk", "twilight_town_pool": "junk", "battlegates_pool": "junk"})
        distribute_items_restrictive(mw)
        self.assertFalse(mw.get_unfilled_locations())
        self.assertTrue(mw.can_beat_game())
        state = mw.get_all_state(False)
        for row in mw.worlds[1].active_locations:
            if row["type"] == "adversary":
                location = mw.get_location(row["name"], 1)
                self.assertTrue(location.can_reach(state), location.name)
                self.assertIn(location.item.classification, (ItemClassification.filler, ItemClassification.useful))

    def test_off_adds_no_checks(self):
        mw = setup_multiworld(KH3World, seed=32, options={"adversary_checks": 0})
        self.assertFalse(any(r["type"] == "adversary" for r in mw.worlds[1].active_locations))
        self.assertEqual(mw.worlds[1].build_runtime_slot_data()["adversary_checks"], 0)

    def test_fill_with_zero_and_all_traps(self):
        for trap_percentage in (0, 100):
            with self.subTest(trap_percentage=trap_percentage):
                mw = setup_multiworld(KH3World, seed=31, options={
                    "adversary_checks": 1, "trap_percentage": trap_percentage})
                distribute_items_restrictive(mw)
                self.assertFalse(mw.get_unfilled_locations())
                for row in mw.worlds[1].active_locations:
                    if row["type"] == "adversary":
                        self.assertIn(mw.get_location(row["name"], 1).item.classification,
                                      (ItemClassification.filler, ItemClassification.useful))
                self.assertTrue(mw.can_beat_game())
