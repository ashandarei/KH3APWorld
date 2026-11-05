from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..Bridge import moogle_shop_native_item_row_name
from ..Data import (
    CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID,
    add_configurable_lucky_emblem_milestones,
    load_extracted_data,
    normalize_locations,
)
from ..export_ap_kh3_randomized_options import (
    ALWAYS_ON_EVENT_IDS,
    export_randomized_options_from_bridge_data,
)
from ..Locations import location_name_to_id
from . import KH3TestBase


class TestMoogleShopWorld(KH3TestBase):
    options = {
        "moogle_shop_checks": 3,
        "moogle_shop_price": 321,
    }

    def test_enabled_shop_checks_are_stable_seed_owned_buy_rows(self) -> None:
        shop_locations = [
            location
            for location in self.world.active_locations
            if location.get("type") == "shop"
        ]
        self.assertEqual(
            [location["shop_index"] for location in shop_locations],
            [1, 2, 3],
        )

        slot_data = self.world.build_runtime_slot_data()
        bridge_locations = [
            location
            for location in slot_data["bridge"]["locations"]
            if location.get("type") == "shop"
        ]
        self.assertEqual(slot_data["moogle_shop_checks"], 3)
        self.assertEqual(slot_data["moogle_shop_price"], 321)
        self.assertEqual(len(bridge_locations), 3)
        self.assertEqual(
            {location["shop_price"] for location in bridge_locations},
            {321},
        )
        self.assertTrue(all(location["bridge_delivery_required"] for location in bridge_locations))
        self.assertEqual(
            len({location["shop_item_token"] for location in bridge_locations}),
            3,
        )
        for location in bridge_locations:
            shop_index = int(location["source_id"].removeprefix("AP_ITEM_"))
            expected_row_name = f"I07{60 + shop_index:03d}"
            self.assertEqual(location["shop_item_row_name"], expected_row_name)
            self.assertRegex(
                location["shop_item_token"],
                rf"^ITEM_AP_{expected_row_name}_{location['ap_location_id']}_[0-9A-F]{{10}}$",
            )

    def test_shop_native_item_rows_use_reserved_material_item_ids(self) -> None:
        self.assertEqual(
            [
                moogle_shop_native_item_row_name({"shop_index": index, "name": f"Shop {index}"})
                for index in range(1, 21)
            ],
            [f"I07{index:03d}" for index in range(61, 81)],
        )

    def test_fixed_shop_catalog_is_appended_after_existing_locations(self) -> None:
        locations = normalize_locations(load_extracted_data())
        shop_indices = [
            index
            for index, location in enumerate(locations)
            if location.get("type") == "shop"
        ]
        self.assertEqual(len(shop_indices), 20)
        self.assertEqual(
            shop_indices,
            list(range(shop_indices[0], shop_indices[0] + 20)),
        )
        classic_indices = [
            index
            for index, location in enumerate(locations)
            if location.get("classic_kingdom_check_kind")
            or location.get("source_id") == CLASSIC_KINGDOM_ALL_GAMES_SOURCE_ID
        ]
        self.assertEqual(len(classic_indices), 24)
        self.assertTrue(all(index > shop_indices[-1] for index in classic_indices))
        self.assertEqual(
            location_name_to_id["Lucky Emblems - Milestone 2"],
            1263023325,
        )

    def test_lucky_emblem_helper_does_not_reorder_shop_rows(self) -> None:
        shop_location = {
            "name": "Test Shop Row",
            "type": "shop",
            "source_table": "MoogleShop",
            "source_id": "TEST_ITEM",
        }
        data = {"locations": [shop_location]}

        add_configurable_lucky_emblem_milestones(data)

        self.assertIs(data["locations"][0], shop_location)
        self.assertTrue(all(
            location.get("type") == "lucky_emblem"
            for location in data["locations"][1:]
        ))


class TestMoogleShopExport(unittest.TestCase):
    def test_shop_export_keeps_assigned_reward_for_bridge_delivery(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": {
                    event_id: {"RandomizedItem": "ITEM_POTION\0"}
                    for event_id in ALWAYS_ON_EVENT_IDS
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [{"id": "ITEM_POTION", "name": "Potion"}],
                "locations": [],
            }), encoding="utf-8")
            marker_token = "ITEM_AP_I07061_1263999001_DEADBEEF00"
            bridge_locations = [{
                "ap_location_id": 1263999001,
                "ap_location_name": "Moogle Shop - Archipelago Purchase 1",
                "assigned_item_game": "Kingdom Hearts III",
                "assigned_item_classification": 1,
                "assigned_item_name": "Potion",
                "assigned_item_player": 1,
                "assigned_item_player_name": "KH3",
                "bridge_delivery_required": True,
                "bridge_key": "MoogleShop:AP_ITEM_01",
                "shop_item_token": marker_token,
                "shop_price": 321,
                "source_id": "AP_ITEM_01",
                "source_table": "MoogleShop",
                "type": "shop",
                "world": "Moogle Shop",
            }]

            result = export_randomized_options_from_bridge_data(
                default_kh3=default_path,
                default_kh3_overrides=None,
                extracted_data_path=extracted_path,
                bridge_locations=bridge_locations,
                slot=1,
                slot_name="KH3",
                archive_name="AP_test.zip",
            )

            placement = result.placement_summary["locations"][0]
            self.assertEqual(placement["export_token"], marker_token)
            self.assertEqual(placement["native_reward_token"], marker_token)
            self.assertEqual(placement["runtime_token"], "ITEM_POTION")
            self.assertEqual(placement["shop_item_token"], marker_token)
            self.assertEqual(placement["shop_price"], 321)
            self.assertEqual(placement["item_classification"], 1)
            self.assertTrue(placement["bridge_delivery_required"])
            self.assertNotIn("MoogleShop", result.randomized_options)


class TestMoogleShopAssets(unittest.TestCase):
    def test_large_munny_pouch_reuses_the_small_pouch_icon(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        from ..seedgen.assets import get
        from ..seedgen.presentation import build_presentation
        from ..seedgen.tables import Tables
        tables = Tables(repo_root / "worlds/kh3/randomizer")
        metadata = {"slot_data":{"ultima_weapon_mode":4}, "placement_summary":{"locations":[]}}
        hints = {f"SecretReport{n:02}":[] for n in range(2,15)}
        build_presentation(tables, {}, metadata, hints)
        small, large = tables.load("Items").rows["I09018"], tables.load("Items").rows["I09019"]
        self.assertEqual(get(large, "m_KeyName").value, "KEY_ITEM19")
        self.assertEqual(get(small, "m_IconAsset").value, get(large, "m_IconAsset").value)
        self.assertEqual(get(large, "m_IconAsset").value, "/Game/UI/Icon/Item/ap_munny_pouch.ap_munny_pouch")
        runtime_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "grants"
            / "delivery"
            / "item_grant_apply_helpers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn('s_large_munny_pouch_runtime_token = STR("KEY_ITEM19")', runtime_source)
        self.assertIn("large_munny_pouch_amount = 1000;", runtime_source)
        self.assertIn("return large_munny_pouch_amount;", runtime_source)

    def test_checked_shop_marker_reconcile_restores_its_munny_cost(self) -> None:
        probe_root = native_project_root() / "KH3APProbeCpp" / "kh3ap"
        parser_header = (probe_root / "bridge" / "catalog/slot_data_lookup_parser.h").read_text(encoding="utf-8")
        parser_source = (probe_root / "bridge" / "catalog/slot_data_lookup_parser.cpp").read_text(encoding="utf-8")
        call_helpers = (probe_root / "features" / "grants" / "delivery" / "item_call_helpers.cpp").read_text(encoding="utf-8")
        shop_service_source = (probe_root / "features" / "moogle_shop" / "moogle_shop_service.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("int32_t shop_price{0};", parser_header)
        self.assertIn('location.int64_field("shop_price")', parser_source)
        self.assertIn(".shop_price = shop_price", parser_source)
        self.assertIn("munny == 0", call_helpers)
        self.assertNotIn("munny <= 0", call_helpers)
        self.assertIn("static_cast<uint64_t>(static_cast<int64_t>(munny))", call_helpers)
        self.assertIn("? -location.shop_price", shop_service_source)
        self.assertIn(
            "try_call_bp_tres_get_item_and_add_munny(",
            shop_service_source,
        )
        self.assertIn("restored_cost={} munny_delta={}", shop_service_source)

    def test_manual_runtime_request_accepts_a_signed_munny_delta(self) -> None:
        probe_root = native_project_root() / "KH3APProbeCpp" / "kh3ap"
        runtime_types = (probe_root / "core" / "types.h").read_text(encoding="utf-8")
        request_parser = (probe_root / "bridge" / "serialization/request_parser.cpp").read_text(encoding="utf-8")
        call_helpers = (probe_root / "features" / "grants" / "delivery" / "item_call_helpers.cpp").read_text(encoding="utf-8")
        apply_helpers = (probe_root / "features" / "grants" / "delivery" / "item_grant_apply_helpers.cpp").read_text(encoding="utf-8")

        self.assertIn("int64_t munny_delta{0};", runtime_types)
        self.assertIn('int64_field(request_text, "kh3_munny_delta")', request_parser)
        self.assertIn("auto try_call_bp_add_munny(", call_helpers)
        self.assertIn('request.grant_method == STR("bp_add_munny")', apply_helpers)
        self.assertIn("static_cast<int32_t>(request.munny_delta)", apply_helpers)

    def test_native_add_munny_patch_removes_only_the_lower_clamp(self) -> None:
        probe_root = native_project_root() / "KH3APProbeCpp" / "kh3ap"
        patch_source = (probe_root / "hooks" / "patches/kh3_signed_munny_patch.cpp").read_text(encoding="utf-8")
        config_source = (probe_root / "app" / "config.h").read_text(encoding="utf-8")
        lifecycle_source = (
            probe_root / "hooks" / "hook_lifecycle_service.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("s_steam_add_munny_rva = 0xE866A0", patch_source)
        native_build = (probe_root / "core" / "platform/game_build.h").read_text(encoding="utf-8")
        self.assertIn("Fingerprint egs{0x60547D11, 0xAEBF000}", native_build)
        self.assertIn("s_egs_image_size = kh3ap::native_build::egs.image_size", patch_source)
        self.assertIn("s_lower_bound_branch_offset = 0x37", patch_source)
        self.assertIn(
            "s_original_lower_bound_branch{0x78, 0x0A}",
            patch_source,
        )
        self.assertIn("s_signed_balance_patch{0x90, 0x90}", patch_source)
        self.assertIn("s_expected_add_munny_function", patch_source)
        self.assertIn("s_egs_add_munny_direct_site_rva = 0x1332F65", patch_source)
        self.assertIn("s_egs_add_munny_exec_site_rva = 0x25670CD", patch_source)
        self.assertIn("s_expected_egs_add_munny_direct_site", patch_source)
        self.assertIn("s_expected_egs_add_munny_exec_site", patch_source)
        self.assertIn(
            "s_egs_original_lower_bound_branch{0x78, 0x0C}",
            patch_source,
        )
        self.assertIn("resolved_site_count = 2", patch_source)
        self.assertIn("rollback_ok", patch_source)
        self.assertIn("VirtualProtect", patch_source)
        self.assertIn("FlushInstructionCache", patch_source)
        self.assertIn("s_enable_signed_munny_patch = true", config_source)
        self.assertIn(
            "config_.signed_munny_patch.install(config_.build_stamp)",
            lifecycle_source,
        )
        self.assertIn('shutdown(STR("destructor"))', lifecycle_source)
        self.assertIn(
            "config_.signed_munny_patch.uninstall(reason)",
            lifecycle_source,
        )

    def test_runtime_shop_movie_and_seed_icon_references(self) -> None:
        kh3_root = Path(__file__).resolve().parents[1]
        runtime_source = native_project_root() / "authoring/kh3/shop_ui"
        action_script = (runtime_source / "APShopRuntime.as").read_text(encoding="utf-8")
        self.assertIn("class APShopRuntime", action_script)
        self.assertIn("attachRow", action_script)
        self.assertIn("attachForge", action_script)
        self.assertIn("tres.controls.Icon", action_script)
        self.assertNotIn("[Embed", action_script)
        self.assertNotIn("class CoTradeListItem", action_script)
        self.assertNotIn("configureCommand", action_script)
        self.assertFalse((runtime_source / "CoTradeListItem.as").exists())
        self.assertFalse((runtime_source / "Shop_fla" / "McMenuMixTopObj_122.as").exists())

        randomizer_root = kh3_root / "randomizer"
        self.assertFalse((randomizer_root / "Content" / "UI" / "Icon" / "Item").exists())
        from ..seedgen.assets import get
        from ..seedgen.localization import SHARD, text_ids
        from ..seedgen.presentation import build_presentation, game_marker
        from ..seedgen.tables import Tables
        for game, icon_name, marker in (("Kingdom Hearts", "ap_kh1", "\u2065"),
                                      ("Kingdom Hearts II", "ap_kh2", "\u2066"),
                                      ("Kingdom Hearts Birth by Sleep", "ap_kh_bbs", "\u2067"),
                                      ("Kingdom Hearts Re:Chain of Memories", "ap_kh_com", "\u2068"),
                                      ("Kingdom Hearts Dream Drop Distance", "ap_kh_ddd", "\u2069")):
            self.assertEqual(game_marker(game), (marker, icon_name))
        hints = {f"SecretReport{n:02}":[] for n in range(2,15)}
        for mode in (0, 1):
            tables = Tables(randomizer_root)
            metadata = {"slot_data":{"ultima_weapon_mode":mode}, "placement_summary":{"locations":[]}}
            build_presentation(tables, {}, metadata, hints)
            item_rows = {get(row,"m_KeyName").value:row for row in tables.load("Items").rows.values()}
            if mode == 0:
                shard = item_rows["MAT_ITEM57"]
                self.assertEqual(get(shard,"m_IconAsset").value, "/Game/UI/Icon/Item/ap_ultima_weapon.ap_ultima_weapon")
                self.assertEqual(get(shard,"m_NameTextID").value, text_ids(SHARD)[0])
                self.assertEqual(get(shard,"m_MaxNum").value, 99)
                self.assertFalse(get(shard,"m_bCanSell").value)
            else:
                ultima = item_rows["WEP_KEYBLADE_SO_015"]
                self.assertEqual(get(ultima,"m_ShopLevel").value, 0)
                self.assertEqual(get(ultima,"m_MaxNum").value, 1)
                self.assertTrue(get(ultima,"m_bCanbeSoldOut").value)

        ultima_shop_helper = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "moogle_shop"
            / "ultima_weapon_shop_helpers.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('s_ultima_weapon_row_name = STR("I03015")', ultima_shop_helper)
        self.assertIn('GetPropertyByNameInChain(STR("m_ShopLevel"))', ultima_shop_helper)
        self.assertIn("desired_shop_level < 0", ultima_shop_helper)

        progressive_helper = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "grants"
            / "keyblades"
            / "progressive_keyblade_level_helpers.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("required_keyblade_names.empty()", progressive_helper)
        self.assertIn('group.name == STR("Pandora\'s Power")', progressive_helper)

        shop_service = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "moogle_shop"
            / "moogle_shop_service.cpp"
        ).read_text(encoding="utf-8")
        shop_root = native_project_root() / 'KH3APProbeCpp/kh3ap/features/moogle_shop'
        shop_catalog = (shop_root / "moogle_shop_catalog.cpp").read_text(encoding="utf-8")
        ultima_unlock = (shop_root / "ultima_weapon_shop_unlock.cpp").read_text(encoding="utf-8")
        self.assertIn('"ultima_weapon_shop_required_keyblades"', shop_catalog)
        self.assertIn('"keyblade_levels"', shop_catalog)
        self.assertIn("keyblade_levels_start_at_max", shop_catalog)
        self.assertIn("constexpr int locked_shop_level = 0", ultima_unlock)
        self.assertIn("catalog.try_load_seed(slot_data_path, slot_document)", shop_service)
        self.assertIn("ultima_unlock.process(now, catalog.seed_loaded(), catalog.options().ultima)", shop_service)


if __name__ == "__main__":
    unittest.main()
