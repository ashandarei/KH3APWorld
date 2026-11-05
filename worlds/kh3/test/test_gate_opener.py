from BaseClasses import CollectionState, ItemClassification
from . import KH3TestBase
from ..Data import resolve_item_runtime_token, resolve_runtime_token_display_name
from ..Options import GateOpenerCount


class TestGateOpeners(KH3TestBase):
    options = {"gate_opener_count": 7}

    def test_count_and_useful_classification(self):
        items = [item for item in self.multiworld.get_items() if item.name == "Gate Opener"]
        self.assertEqual(len(items), 7)
        self.assertTrue(all(item.classification == ItemClassification.useful for item in items))
        self.assertEqual(self.world.fill_slot_data()["gate_opener_count"], 7)

    def test_does_not_unlock_logic(self):
        state = CollectionState(self.multiworld)
        before = {region.name for region in self.multiworld.get_regions() if state.can_reach(region)}
        for _ in range(7):
            state.collect(self.world.create_item("Gate Opener"))
        after = {region.name for region in self.multiworld.get_regions() if state.can_reach(region)}
        self.assertEqual(before, after)

    def test_native_inventory_identity(self):
        self.assertEqual(resolve_item_runtime_token("Gate Opener"), "KEY_ITEM20")
        self.assertEqual(resolve_runtime_token_display_name("KEY_ITEM20"), "Gate Opener")
        for invalid in (-1, 8):
            with self.assertRaises(Exception):
                GateOpenerCount.from_any(invalid)


class TestNoGateOpeners(KH3TestBase):
    options = {"gate_opener_count": 0}

    def test_no_gate_items_by_default(self):
        self.assertFalse(any(item.name == "Gate Opener" for item in self.multiworld.get_items()))


class TestGateOpenersWithShops(TestGateOpeners):
    options = {"gate_opener_count": 7, "moogle_shop_checks": 20, "trap_percentage": 100}

    def test_preserves_shop_funding(self):
        plan = self.world._moogle_shop_funding_plan
        pouches = [item for item in self.multiworld.get_items() if item.name == "Large Munny Pouch"]
        self.assertEqual(len(pouches), plan.large_pouch_count)
