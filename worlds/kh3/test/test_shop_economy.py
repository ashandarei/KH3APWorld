from __future__ import annotations

import unittest
from collections import Counter

from BaseClasses import CollectionState, ItemClassification
from Fill import balance_multiworld_progression, distribute_items_restrictive
from test.general import setup_multiworld
from worlds.AutoWorld import call_all
from worlds.kh1 import KH1World

from .. import KH3World
from ..Data import (
    BAD_STATUS_TRAP_ITEM_NAMES,
    LARGE_MUNNY_POUCH_ITEM_NAME,
    LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
    MUNNY_POUCH_ITEM_NAME,
)
from ..ShopEconomy import (
    build_moogle_shop_funding_plan,
    build_moogle_shop_price_tiers,
    moogle_shop_price_for_classification,
)
from ..Options import KH3LocationPoolMode, KH3Options
from . import KH3TestBase


class TestMoogleShopEconomyAlgorithm(unittest.TestCase):
    def test_classification_price_tiers_use_progression_precedence(self) -> None:
        tiers = build_moogle_shop_price_tiers(100)

        self.assertEqual(tiers.as_dict(), {
            "filler": 100,
            "useful": 500,
            "progression": 1000,
            "trap": 50,
        })
        self.assertEqual(
            moogle_shop_price_for_classification(ItemClassification.filler, tiers),
            100,
        )
        self.assertEqual(
            moogle_shop_price_for_classification(ItemClassification.useful, tiers),
            500,
        )
        self.assertEqual(
            moogle_shop_price_for_classification(ItemClassification.progression, tiers),
            1000,
        )
        self.assertEqual(
            moogle_shop_price_for_classification(ItemClassification.trap, tiers),
            50,
        )
        self.assertEqual(
            moogle_shop_price_for_classification(
                ItemClassification.progression | ItemClassification.useful,
                tiers,
            ),
            1000,
        )

    def test_budget_uses_large_pouches_for_sixty_percent_of_worst_case(self) -> None:
        plan = build_moogle_shop_funding_plan(
            shop_check_count=14,
            requested_base_price=100,
            existing_pouch_munny=0,
            available_filler_slots=100,
            large_pouch_munny=LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
        )

        self.assertEqual(plan.maximum_shop_cost, 14_000)
        self.assertEqual(plan.funding_target, 8_400)
        self.assertEqual(plan.large_pouch_count, 9)
        self.assertEqual(plan.planned_pouch_munny, 9_000)
        self.assertFalse(plan.price_was_reduced)

    def test_existing_small_pouches_reduce_large_pouch_count(self) -> None:
        plan = build_moogle_shop_funding_plan(
            shop_check_count=14,
            requested_base_price=100,
            existing_pouch_munny=400,
            available_filler_slots=100,
            large_pouch_munny=LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
        )

        self.assertEqual(plan.large_pouch_count, 8)
        self.assertEqual(plan.planned_pouch_munny, 8_400)

    def test_price_falls_back_when_the_pool_has_too_few_filler_slots(self) -> None:
        plan = build_moogle_shop_funding_plan(
            shop_check_count=14,
            requested_base_price=100,
            existing_pouch_munny=0,
            available_filler_slots=2,
            large_pouch_munny=LARGE_MUNNY_POUCH_MUNNY_AMOUNT,
        )

        self.assertEqual(plan.effective_base_price, 23)
        self.assertEqual(plan.large_pouch_count, 2)
        self.assertLessEqual(plan.funding_target, plan.planned_pouch_munny)
        self.assertTrue(plan.price_was_reduced)


class TestMoogleShopFundingWorld(KH3TestBase):
    options = {
        "moogle_shop_checks": 14,
        "moogle_shop_price": 100,
        "trap_percentage": 0,
    }

    def test_generation_injects_pouches_and_requests_standard_early_fill(self) -> None:
        plan = self.world._moogle_shop_funding_plan
        player_pool_names = Counter(
            item.name
            for item in self.multiworld.get_items()
            if item.player == self.player
        )

        self.assertEqual(
            player_pool_names[LARGE_MUNNY_POUCH_ITEM_NAME],
            plan.large_pouch_count,
        )
        self.assertGreater(plan.large_pouch_count, 0)
        self.assertTrue(all(item.location is None for item in self.multiworld.itempool
                            if item.name == LARGE_MUNNY_POUCH_ITEM_NAME))
        self.assertGreaterEqual(plan.planned_pouch_munny, plan.funding_target)
        self.assertGreaterEqual(
            self.multiworld.early_items[self.player][LARGE_MUNNY_POUCH_ITEM_NAME],
            1,
        )

        slot_data = self.world.build_runtime_slot_data()
        self.assertEqual(slot_data["moogle_shop_price_tiers"], {
            "filler": 100,
            "useful": 500,
            "progression": 1000,
            "trap": 50,
        })
        self.assertEqual(
            slot_data["moogle_shop_economy"]["large_pouch_count"],
            plan.large_pouch_count,
        )

    def test_farmable_money_allows_funding_pouches_in_shop_checks(self) -> None:
        pouch_items = (
            self.world.create_item(MUNNY_POUCH_ITEM_NAME),
            self.world.create_item(LARGE_MUNNY_POUCH_ITEM_NAME),
        )
        shop_locations = [
            self.multiworld.get_location(location["name"], self.player)
            for location in self.world.active_locations
            if location.get("type") == "shop"
        ]

        self.assertEqual(len(shop_locations), 14)
        for location in shop_locations:
            with self.subTest(location=location.name):
                self.assertTrue(location.item_rule(self.world.create_item("Potion")))
                for pouch_item in pouch_items:
                    self.assertTrue(location.item_rule(pouch_item))

    def test_pooh_income_keeps_opening_shop_reachable_without_ap_pouches(self) -> None:
        state = CollectionState(self.multiworld)
        for item in self.multiworld.precollected_items[self.player]:
            state.remove(item)
        shop = self.multiworld.get_location('Moogle Shop - Archipelago Purchase 1', self.player)
        self.assertTrue(shop.can_reach(state))
        self.assertEqual(state.count(LARGE_MUNNY_POUCH_ITEM_NAME, self.player), 0)
        self.assertEqual(state.count('Unlock: Wall Run', self.player), 0)
        self.assertEqual(state.count("Hero's Origin", self.player), 0)

    def test_free_shop_does_not_require_a_farming_route(self) -> None:
        mw = setup_multiworld(KH3World, seed=731, options={
            'moogle_shop_checks': 1, 'moogle_shop_price': 0,
        })
        state = CollectionState(mw)
        for item in mw.precollected_items[1]:
            state.remove(item)
        self.assertTrue(mw.get_location('Moogle Shop - Archipelago Purchase 1', 1).can_reach(state))

    def test_stat_bonus_insertion_preserves_planned_funding(self) -> None:
        # Seed 9 previously lost one of its nine planned large pouches when
        # the final stat-bonus insertion selected funding as removable filler.
        self.world_setup(seed=9)
        planned = self.world._moogle_shop_funding_plan
        pool_munny = sum(
            item.name == LARGE_MUNNY_POUCH_ITEM_NAME
            for item in self.multiworld.get_items()
            if item.player == self.player
        )
        self.assertEqual(pool_munny, planned.large_pouch_count)


class TestMoogleShopClassificationPrices(KH3TestBase):
    options = {
        "moogle_shop_checks": 4,
        "moogle_shop_price": 100,
        "trap_percentage": 0,
    }

    def test_filled_shop_rows_export_classification_prices(self) -> None:
        item_names = (
            "Potion",
            "Ability: Thunder Boost",
            "Proof of Promises",
            BAD_STATUS_TRAP_ITEM_NAMES[0],
        )
        shop_locations = sorted(
            (
                location
                for location in self.world.active_locations
                if location.get("type") == "shop"
            ),
            key=lambda location: int(location["shop_index"]),
        )
        for location_data, item_name in zip(shop_locations, item_names):
            location = self.multiworld.get_location(location_data["name"], self.player)
            location.place_locked_item(self.world.create_item(item_name))

        slot_data = self.world.build_runtime_slot_data()
        bridge_rows = [
            location
            for location in slot_data["bridge"]["locations"]
            if location.get("type") == "shop"
        ]

        self.assertEqual(
            [location["assigned_item_name"] for location in bridge_rows],
            list(item_names),
        )
        self.assertEqual(
            [location["shop_price"] for location in bridge_rows],
            [100, 500, 1000, 50],
        )
        self.assertEqual(slot_data["moogle_shop_economy"]["actual_shop_cost"], 1650)


class TestMoogleShopStandardFill(unittest.TestCase):
    def test_standard_fill_preserves_funding_in_solo_mixed_and_all_junk_seeds(self):
        junk = {name: 'junk' for name, kind in KH3Options.type_hints.items()
                if issubclass(kind, KH3LocationPoolMode)}
        cases = (
            ([KH3World], {'moogle_shop_checks': 14}),
            ([KH3World], {'moogle_shop_checks': 20, 'moogle_shop_price': 10000}),
            ([KH3World, KH1World], {'moogle_shop_checks': 20, 'moogle_shop_price': 10000}),
            ([KH3World], {**junk, 'moogle_shop_checks': 20, 'moogle_shop_price': 10000}),
        )
        for index, (worlds, options) in enumerate(cases):
            with self.subTest(worlds=[world.game for world in worlds], options=options):
                mw = setup_multiworld(worlds, seed=731 + index,
                                     options=[options] + [{}] * (len(worlds) - 1))
                pouches = [item for item in mw.itempool if item.player == 1
                           and item.name == LARGE_MUNNY_POUCH_ITEM_NAME]
                self.assertEqual(len(pouches), mw.worlds[1]._moogle_shop_funding_plan.large_pouch_count)
                self.assertTrue(all(item.location is None for item in pouches))
                distribute_items_restrictive(mw)
                call_all(mw, 'post_fill')
                if len(worlds) > 1:
                    balance_multiworld_progression(mw)
                call_all(mw, 'finalize_multiworld')
                self.assertFalse(mw.get_unfilled_locations())
                self.assertTrue(all(item.location is not None for item in pouches))
                self.assertTrue(mw.can_beat_game())
                self.assertTrue(mw.fulfills_accessibility())


if __name__ == "__main__":
    unittest.main()
