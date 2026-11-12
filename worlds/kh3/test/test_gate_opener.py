import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from BaseClasses import CollectionState, ItemClassification
from . import KH3TestBase
from ..Data import resolve_item_runtime_token, resolve_runtime_token_display_name
from ..Options import GateOpenerCount
from ..Items import item_name_to_id
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    EXTRACTED_DATA_PATH,
    bridge_only_item_runtime_token,
    build_item_name_to_token,
    export_randomized_options_from_bridge_data,
    load_json,
)


class TestGateOpenerExport(TestCase):
    def test_all_registered_items_have_export_tokens(self):
        mapping = build_item_name_to_token(load_json(EXTRACTED_DATA_PATH))
        missing = [
            name for name in item_name_to_id
            if name not in mapping and bridge_only_item_runtime_token(name) is None
        ]
        self.assertEqual(missing, [])

    def test_container_exports_gate_openers_in_kh3_and_kh02_chests(self):
        bridge_locations = [
            {
                "ap_location_id": 1,
                "ap_location_name": "KH3 chest",
                "bridge_key": "TreasureHE:HE_LBOX01",
                "source_table": "TreasureHE",
                "source_id": "HE_LBOX01",
                "type": "treasure",
                "chest_persistence_version": 1,
            },
            {
                "ap_location_id": 2,
                "ap_location_name": "0.2 - Forest of Thorns - Chest DW_LBOX03",
                "bridge_key": "KH02Chest:DW_LBOX03",
                "source_table": "KH02Chest",
                "source_id": "DW_LBOX03",
                "type": "kh02_chest",
            },
        ]
        for location in bridge_locations:
            location.update({
                "assigned_item_name": "Gate Opener",
                "assigned_item_id": item_name_to_id["Gate Opener"],
                "assigned_item_game": "Kingdom Hearts III",
                "assigned_item_player": 1,
            })
        with TemporaryDirectory() as temp_dir:
            default_path = Path(temp_dir) / "DefaultKH3.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
                "TreasureHE": {"HE_LBOX01": {"Treasure": "ITEM_POTION\0"}},
            }), encoding="utf-8")
            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=EXTRACTED_DATA_PATH,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_gate_opener.kh3",
            )
        self.assertEqual(len(result.placement_summary["locations"]), 2)
        for placement in result.placement_summary["locations"]:
            self.assertEqual(placement["runtime_token"], "KEY_ITEM20")
            self.assertEqual(placement["item_name"], "Gate Opener")
            self.assertTrue(placement["bridge_delivery_required"])
        self.assertEqual(
            result.randomized_options["TreasureHE"]["HE_LBOX01"]["Treasure"],
            "ETresVictoryBonusKind::NONE\0",
        )


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
