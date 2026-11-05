from collections import Counter

from BaseClasses import CollectionState
from ..Data import HEART_PIECE_ITEM_NAME, PROOF_ITEMS, PORTAL_KEYBLADE_REGION_LOCKS
from ..Options import HeartPieceCount, WorldsRequired
from ..ColiseumLogic import COLISEUM_WORLD_COMPLETION_SOURCES
from . import KH3TestBase


class TestFinalDoor(KH3TestBase):
    options = {"heart_piece_count": 3, "worlds_required": 7}

    def test_ranges(self):
        self.assertEqual((HeartPieceCount.range_start, HeartPieceCount.range_end), (3, 30))
        self.assertEqual((WorldsRequired.range_start, WorldsRequired.range_end), (0, 7))

    def test_exact_pool_counts_and_export_at_boundaries(self):
        for count in (3, 7, 30):
            with self.subTest(count=count):
                self.options = {"heart_piece_count": count, "worlds_required": 7}
                self.world_setup(seed=14009901)
                counts = Counter(i.name for i in self.multiworld.itempool + self.multiworld.precollected_items[self.player])
                self.assertEqual(counts[HEART_PIECE_ITEM_NAME], count)
                self.assertTrue(PROOF_ITEMS.isdisjoint(counts))
                self.assertTrue(PROOF_ITEMS.isdisjoint(self.world.item_name_to_id))
                slot = self.world.build_runtime_slot_data()
                self.assertEqual(slot["heart_piece_count"], count)
                self.assertEqual(slot["worlds_required"], 7)
                self.assertEqual(slot["kh3_randomizer"]["clear_condition"]["conditions"], {"KEY_ITEM17\0": count})
                self.assertTrue(self.multiworld.get_region("Final Door", self.player).can_reach(
                    self.multiworld.get_all_state(False)))

    def test_both_requirements_and_dark_aqua_but_not_pooh(self):
        self.world_setup()
        region = self.multiworld.get_region("Final Door", self.player)
        world_keys = {PORTAL_KEYBLADE_REGION_LOCKS[name] for name in COLISEUM_WORLD_COMPLETION_SOURCES}
        excluded = world_keys | {"Oblivion", "Hunny Spout", HEART_PIECE_ITEM_NAME}
        state = CollectionState(self.multiworld)
        # CollectionState includes randomized starting items; explicitly isolate
        # the worlds under test regardless of which starter the seed chose.
        for name in excluded:
            while state.has(name, self.player):
                state.remove(self.world.create_item(name))
        for item in self.multiworld.get_items() + self.multiworld.precollected_items[self.player]:
            if item.name not in excluded:
                state.collect(item, prevent_sweep=True)
        for _ in range(3):
            state.collect(self.world.create_item(HEART_PIECE_ITEM_NAME))
        self.assertFalse(region.can_reach(state))
        for name in list(COLISEUM_WORLD_COMPLETION_SOURCES)[:6]:
            state.collect(self.world.create_item(PORTAL_KEYBLADE_REGION_LOCKS[name]))
        self.assertFalse(region.can_reach(state))
        state.collect(self.world.create_item("Hunny Spout"))
        self.assertTrue(self.multiworld.get_region("100 Acre Wood", self.player).can_reach(state))
        self.assertFalse(region.can_reach(state))
        state.collect(self.world.create_item("Oblivion"))
        self.assertTrue(region.can_reach(state))
        state.remove(self.world.create_item(HEART_PIECE_ITEM_NAME))
        self.assertFalse(region.can_reach(state))

    def test_zero_worlds_needs_only_hearts(self):
        self.options = {"heart_piece_count": 3, "worlds_required": 0}
        self.world_setup()
        state = CollectionState(self.multiworld)
        region = self.multiworld.get_region("Final Door", self.player)
        for _ in range(2):
            state.collect(self.world.create_item(HEART_PIECE_ITEM_NAME))
        self.assertFalse(region.can_reach(state))
        state.collect(self.world.create_item(HEART_PIECE_ITEM_NAME))
        self.assertTrue(region.can_reach(state))
