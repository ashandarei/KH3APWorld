from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_KH3_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_KH3_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_KH3_ROOT))

from ap_archive import (  # noqa: E402
    ArchiveValidationError,
    find_archipelago_entry_name,
    require_bridge_locations,
    resolve_kh3_slot,
)
from export_ap_kh3_randomized_options import (  # noqa: E402
    ALWAYS_ON_EVENT_IDS,
    SUPPRESSED_ALWAYS_ON_EVENT_TOKEN,
    apply_chrinit_sora_have_ability_injection,
    apply_event_reward_overrides,
    apply_vbonus_override,
    export_randomized_options_from_bridge_data,
    export_randomized_options,
    normalize_ability_injection_token,
    normalize_reward_override_token,
    suppress_always_on_event_rewards,
)
from worlds.kh3.Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION  # noqa: E402
from worlds.kh3.SlotData import encode_slot_data  # noqa: E402


def make_multidata() -> dict:
    return {
        "slot_info": {
            1: SimpleNamespace(name="shado", game="Kingdom Hearts III"),
            2: SimpleNamespace(name="alice", game="A Link to the Past"),
            3: SimpleNamespace(name="kh3-alt", game="Kingdom Hearts III"),
        },
        "slot_data": {
            1: {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "bridge_key": "TreasureTS:TS_SBOX_002",
                            "source_table": "TreasureTS",
                            "source_id": "TS_SBOX_002",
                            "ap_location_id": 1263022897,
                            "ap_location_name": "Toy Box - Small Chest 2",
                            "type": "treasure",
                        }
                    ]
                }
            }
        },
        "locations": {1: {1263022897: (9001, 1, 0)}},
        "datapackage": {},
    }


def wire_multidata(multidata: dict) -> dict:
    """Encode the fixture at the AP archive boundary, after per-test edits."""
    return {
        **multidata,
        "slot_data": {**multidata["slot_data"], 1: encode_slot_data(multidata["slot_data"][1])},
    }


def make_always_on_event_options() -> dict:
    return {
        event_id: {"RandomizedItem": f"{event_id}_OLD\u0000"}
        for event_id in ALWAYS_ON_EVENT_IDS
    }


class TestApArchiveHelpers(unittest.TestCase):
    def test_find_archipelago_entry_name_accepts_single_payload(self) -> None:
        self.assertEqual(find_archipelago_entry_name(["docs/readme.txt", "seed.archipelago"]), "seed.archipelago")

    def test_find_archipelago_entry_name_rejects_missing_payload(self) -> None:
        with self.assertRaises(ArchiveValidationError):
            find_archipelago_entry_name(["docs/readme.txt"])

    def test_find_archipelago_entry_name_rejects_multiple_payloads(self) -> None:
        with self.assertRaises(ArchiveValidationError):
            find_archipelago_entry_name(["a.archipelago", "b.archipelago"])

    def test_resolve_kh3_slot_requires_explicit_slot_for_multiple_kh3_players(self) -> None:
        multidata = make_multidata()
        with self.assertRaises(ArchiveValidationError):
            resolve_kh3_slot(multidata, None)

    def test_resolve_kh3_slot_accepts_explicit_slot(self) -> None:
        multidata = make_multidata()
        self.assertEqual(resolve_kh3_slot(multidata, 3), 3)

    def test_require_bridge_locations_rejects_missing_bridge_metadata(self) -> None:
        multidata = make_multidata()
        multidata["slot_data"][1] = {}
        with self.assertRaises(ArchiveValidationError):
            require_bridge_locations(multidata, 1)

    def test_require_bridge_locations_returns_locations(self) -> None:
        multidata = make_multidata()
        multidata["slot_data"][1]["bridge"]["locations"] = tuple(multidata["slot_data"][1]["bridge"]["locations"])
        locations = require_bridge_locations(wire_multidata(multidata), 1)
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["bridge_key"], "TreasureTS:TS_SBOX_002")


class TestKh3ExactExporterHelpers(unittest.TestCase):
    def test_always_on_event_rewards_are_suppressed_in_exact_export(self) -> None:
        default_options = {
            "Event": {
                event_id: {"RandomizedItem": f"{event_id}_OLD\u0000"}
                for event_id in ALWAYS_ON_EVENT_IDS
            }
        }

        suppressed = suppress_always_on_event_rewards(default_options)

        self.assertEqual(len(suppressed), len(ALWAYS_ON_EVENT_IDS))
        for event_id in ALWAYS_ON_EVENT_IDS:
            self.assertEqual(
                default_options["Event"][event_id]["RandomizedItem"],
                f"{SUPPRESSED_ALWAYS_ON_EVENT_TOKEN}\u0000",
            )
            self.assertIn({
                "event_id": event_id,
                "field_name": "RandomizedItem",
                "previous_token": f"{event_id}_OLD\u0000",
                "runtime_token": SUPPRESSED_ALWAYS_ON_EVENT_TOKEN,
            }, suppressed)

    def test_vbonus_override_suppresses_non_primary_vanilla_rewards(self) -> None:
        default_options = {"VBonus": {}}
        bridge_location = {"source_id": "Vbonus_082"}
        extracted_location = {
            "source_id": "Vbonus_082",
            "vanilla_items": {
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
                "Sora_Bonus1": "ETresVictoryBonusKind::MELEM_FIRE",
                "Sora_Bonus2": "ETresVictoryBonusKind::MELEM_WATER",
            },
            "vanilla_item_names": {
                "Sora_Ability1": "ETresAbilityKind::NONE",
                "Sora_Ability2": "ETresAbilityKind::NONE",
                "Sora_Bonus1": "Magic: Fire",
                "Sora_Bonus2": "Magic: Water",
            },
        }

        apply_vbonus_override(default_options, bridge_location, extracted_location, "ITEM_ELIXIR")

        self.assertEqual(default_options["VBonus"]["Vbonus_082"], {
            "Sora_Ability1": "ETresAbilityKind::NONE\u0000",
            "Sora_Ability2": "ETresAbilityKind::NONE\u0000",
            "Sora_Bonus1": "ITEM_ELIXIR\u0000",
            "Sora_Bonus2": "ETresVictoryBonusKind::NONE\u0000",
        })

    def test_chrinit_ability_injection_accepts_friendly_ability_name(self) -> None:
        token = normalize_ability_injection_token(
            "Thunder Boost",
            {"Ability: Thunder Boost": "ETresAbilityKind::THUNDER_UP"},
        )

        self.assertEqual(token, "ETresAbilityKind::THUNDER_UP")

    def test_chrinit_ability_injection_rejects_non_zero_exp_override(self) -> None:
        default_options = {
            "ChrInit": {
                "m_PlayerSora": {
                    "HaveAbility": "ETresAbilityKind::EXPZERO\u0000",
                },
            },
        }

        with self.assertRaisesRegex(ValueError, "fixed to Ability: Zero EXP"):
            apply_chrinit_sora_have_ability_injection(
                default_options,
                "Ability: Thunder Boost",
                {"Ability: Thunder Boost": "ETresAbilityKind::THUNDER_UP"},
            )

        self.assertEqual(
            default_options["ChrInit"]["m_PlayerSora"]["HaveAbility"],
            "ETresAbilityKind::EXPZERO\u0000",
        )

    def test_chrinit_ability_injection_rejects_multiple_values(self) -> None:
        default_options = {
            "ChrInit": {
                "m_PlayerSora": {
                    "HaveAbility": "ETresAbilityKind::EXPZERO\u0000",
                },
            },
        }

        with self.assertRaisesRegex(ValueError, "single enum field"):
            apply_chrinit_sora_have_ability_injection(
                default_options,
                ["Ability: Thunder Boost", "ETresAbilityKind::WATER_UP"],
                {
                    "Ability: Thunder Boost": "ETresAbilityKind::THUNDER_UP",
                    "Ability: Water Boost": "ETresAbilityKind::WATER_UP",
                },
            )

    def test_event_reward_override_accepts_friendly_item_name(self) -> None:
        token = normalize_reward_override_token(
            "Ability: Thunder Boost",
            {"Ability: Thunder Boost": "ETresAbilityKind::THUNDER_UP"},
        )

        self.assertEqual(token, "ETresAbilityKind::THUNDER_UP")

    def test_event_reward_override_replaces_randomized_item(self) -> None:
        default_options = {
            "Event": {
                "EVENT_KEYITEM_003": {
                    "RandomizedItem": "KEY_ITEM03\u0000",
                },
            },
        }

        summary = apply_event_reward_overrides(
            default_options,
            ["EVENT_KEYITEM_003=Ability: Thunder Boost"],
            {"Ability: Thunder Boost": "ETresAbilityKind::THUNDER_UP"},
        )

        self.assertEqual(
            default_options["Event"]["EVENT_KEYITEM_003"]["RandomizedItem"],
            "ETresAbilityKind::THUNDER_UP\u0000",
        )
        self.assertEqual(summary[0]["target"], "Event.EVENT_KEYITEM_003.RandomizedItem")
        self.assertEqual(summary[0]["previous_token"], "KEY_ITEM03\u0000")
        self.assertEqual(summary[0]["runtime_token"], "ETresAbilityKind::THUNDER_UP")

    def test_event_reward_override_replaces_explicit_field(self) -> None:
        default_options = {
            "Event": {
                "TresUIMobilePortalDataAsset": {
                    "Reward": "KEY_ITEM02\u0000",
                },
            },
        }

        summary = apply_event_reward_overrides(
            default_options,
            "TresUIMobilePortalDataAsset:Reward=KEY_ITEM03",
            {},
        )

        self.assertEqual(
            default_options["Event"]["TresUIMobilePortalDataAsset"]["Reward"],
            "KEY_ITEM03\u0000",
        )
        self.assertEqual(summary[0]["target"], "Event.TresUIMobilePortalDataAsset.Reward")

    def test_export_replaces_foreign_item_with_archipelago_placeholder(self) -> None:
        default_options = {
            "Event": make_always_on_event_options(),
            "TreasureTS": {
                "TS_SBOX_002": {
                    "Treasure": "ITEM_POTION\u0000",
                },
            },
        }
        extracted_data = {
            "items": [
                {"id": "KEY_ITEM02", "name": "Gummiphone"},
                {"id": "ITEM_POTION", "name": "Potion"},
            ],
            "locations": [
                {
                    "name": "Toy Box - Small Chest 2",
                    "source_table": "TreasureTS",
                    "source_id": "TS_SBOX_002",
                    "type": "treasure",
                    "world": "Toy Box",
                    "vanilla_item": "ITEM_POTION",
                    "vanilla_item_name": "Potion",
                },
            ],
        }
        multidata = make_multidata()
        multidata["locations"] = {1: {1263022897: (7001, 2, 0)}}
        multidata["datapackage"] = {
            "Kingdom Hearts III": {
                "item_name_to_id": {
                    "Potion": 1,
                    "Gummiphone": 2,
                },
            },
            "A Link to the Past": {
                "item_name_to_id": {
                    "Hookshot": 7001,
                },
            },
        }

        with (
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_default_kh3", return_value=default_options),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_json", return_value=extracted_data),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=wire_multidata(multidata)),
        ):
            result = export_randomized_options(SimpleNamespace(
                ap_zip=Path("AP_test.zip"),
                default_kh3=Path("DefaultKH3.json"),
                default_kh3_overrides=Path("DefaultKH3.ArchipelagoOverrides.json"),
                extracted_data=Path("extracted_locations.json"),
                runtime_root=Path("."),
                slot=1,
                chrinit_sora_have_ability=None,
                event_reward_override=[],
            ))

        self.assertEqual(
            result.randomized_options["TreasureTS"]["TS_SBOX_002"]["Treasure"],
            "AP_ITEM_1263022897\u0000",
        )
        self.assertEqual(result.foreign_item_manifest["foreign_item_count"], 1)
        self.assertEqual(result.foreign_item_manifest["placeholder_item_name"], "Archipelago Item")
        self.assertEqual(result.foreign_item_manifest["placeholder_token"], "ARCHIPELAGO_ITEM")
        self.assertEqual(result.foreign_item_manifest["legacy_placeholder_token"], "ARCHIPELAGO_ITEM")
        self.assertEqual(result.foreign_item_manifest["placeholder_token_prefix"], "AP_ITEM_")
        self.assertEqual(result.foreign_item_manifest["placeholder_runtime_token"], "AP_ITEM_000")
        self.assertEqual(result.foreign_item_manifest["foreign_items"][0]["placeholder_token"], "AP_ITEM_1263022897")
        self.assertEqual(result.foreign_item_manifest["foreign_items"][0]["item_name"], "Hookshot")
        self.assertEqual(result.foreign_item_manifest["foreign_items"][0]["item_game"], "A Link to the Past")
        self.assertEqual(result.placement_summary["foreign_item_count"], 1)
        self.assertEqual(result.placement_summary["locations"][0]["export_token"], "AP_ITEM_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["runtime_token"], "AP_ITEM_000")

    def test_export_defaults_lucky_emblem_foreign_item_to_native_ap_token(self) -> None:
        default_options = {
            "Event": make_always_on_event_options(),
            "LuckyMark": {
                "3": {
                    "Reward": "ITEM_MEGAPOTION\u0000",
                },
            },
        }
        extracted_data = {
            "items": [
                {"id": "ITEM_MEGAPOTION", "name": "Mega-Potion"},
            ],
            "locations": [
                {
                    "name": "Lucky Emblems - Milestone 3",
                    "source_table": "LuckyMark",
                    "source_id": "3",
                    "type": "lucky_emblem",
                    "world": "Lucky Emblems",
                    "vanilla_item": "ITEM_MEGAPOTION",
                    "vanilla_item_name": "Mega-Potion",
                },
            ],
        }
        multidata = make_multidata()
        multidata["slot_data"][1]["bridge"]["locations"][0].update({
            "bridge_key": "LuckyMark:3",
            "source_table": "LuckyMark",
            "source_id": "3",
            "ap_location_id": 1263022897,
            "ap_location_name": "Lucky Emblems - Milestone 3",
            "type": "lucky_emblem",
        })
        multidata["locations"] = {1: {1263022897: (7001, 2, 0)}}
        multidata["datapackage"] = {
            "Kingdom Hearts III": {
                "item_name_to_id": {
                    "Mega-Potion": 1,
                },
            },
            "A Link to the Past": {
                "item_name_to_id": {
                    "Hookshot": 7001,
                },
            },
        }

        with (
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_default_kh3", return_value=default_options),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_json", return_value=extracted_data),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=wire_multidata(multidata)),
        ):
            result = export_randomized_options(SimpleNamespace(
                ap_zip=Path("AP_test.zip"),
                default_kh3=Path("DefaultKH3.json"),
                default_kh3_overrides=Path("DefaultKH3.ArchipelagoOverrides.json"),
                extracted_data=Path("extracted_locations.json"),
                runtime_root=Path("."),
                slot=1,
                chrinit_sora_have_ability=None,
                event_reward_override=[],
            ))

        self.assertEqual(result.randomized_options["LuckyMark"]["3"]["Reward"], "ITEM_AP_I02200_1263022897\u0000")
        self.assertEqual(result.foreign_item_manifest["foreign_items"][0]["placeholder_token"], "AP_ITEM_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["location_type"], "lucky_emblem")
        self.assertEqual(result.placement_summary["locations"][0]["export_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["native_reward_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["runtime_token"], "AP_ITEM_000")

    def test_export_defaults_lucky_emblem_controller_unlock_to_native_ap_token(self) -> None:
        default_options = {
            "Event": make_always_on_event_options(),
            "LuckyMark": {
                "3": {
                    "Reward": "ITEM_MEGAPOTION\u0000",
                },
            },
        }
        extracted_data = {
            "items": [
                {"id": "ITEM_MEGAPOTION", "name": "Mega-Potion"},
            ],
            "locations": [
                {
                    "name": "Lucky Emblems - Milestone 3",
                    "source_table": "LuckyMark",
                    "source_id": "3",
                    "type": "lucky_emblem",
                    "world": "Lucky Emblems",
                    "vanilla_item": "ITEM_MEGAPOTION",
                    "vanilla_item_name": "Mega-Potion",
                },
            ],
        }
        multidata = make_multidata()
        multidata["slot_data"][1]["bridge"]["locations"][0].update({
            "bridge_key": "LuckyMark:3",
            "source_table": "LuckyMark",
            "source_id": "3",
            "ap_location_id": 1263022897,
            "ap_location_name": "Lucky Emblems - Milestone 3",
            "type": "lucky_emblem",
        })
        multidata["locations"] = {1: {1263022897: (8001, 1, 0)}}
        multidata["datapackage"] = {
            "Kingdom Hearts III": {
                "item_name_to_id": {
                    "Unlock: Wall Run": 8001,
                },
            },
        }

        with (
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_default_kh3", return_value=default_options),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_json", return_value=extracted_data),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=wire_multidata(multidata)),
        ):
            result = export_randomized_options(SimpleNamespace(
                ap_zip=Path("AP_test.zip"),
                default_kh3=Path("DefaultKH3.json"),
                default_kh3_overrides=Path("DefaultKH3.ArchipelagoOverrides.json"),
                extracted_data=Path("extracted_locations.json"),
                runtime_root=Path("."),
                slot=1,
                chrinit_sora_have_ability=None,
                event_reward_override=[],
            ))

        self.assertEqual(result.randomized_options["LuckyMark"]["3"]["Reward"], "ITEM_AP_I02200_1263022897\u0000")
        self.assertEqual(result.foreign_item_manifest["foreign_item_count"], 0)
        self.assertEqual(result.placement_summary["locations"][0]["location_type"], "lucky_emblem")
        self.assertEqual(result.placement_summary["locations"][0]["is_foreign_item"], False)
        self.assertEqual(result.placement_summary["locations"][0]["export_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["native_reward_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(
            result.placement_summary["locations"][0]["runtime_token"],
            "KH3AP_CONTROLLER_UNLOCK::wall_run",
        )
        self.assertTrue(result.placement_summary["locations"][0]["bridge_delivery_required"])

    def test_export_defaults_lucky_emblem_local_ability_to_native_ap_token(self) -> None:
        default_options = {
            "Event": make_always_on_event_options(),
            "LuckyMark": {
                "3": {
                    "Reward": "ITEM_MEGAPOTION\u0000",
                },
            },
        }
        extracted_data = {
            "items": [
                {"id": "ETresAbilityKind::POLE_SPIN", "name": "Ability: Pole Spin"},
                {"id": "ITEM_MEGAPOTION", "name": "Mega-Potion"},
            ],
            "locations": [
                {
                    "name": "Lucky Emblems - Milestone 3",
                    "source_table": "LuckyMark",
                    "source_id": "3",
                    "type": "lucky_emblem",
                    "world": "Lucky Emblems",
                    "vanilla_item": "ITEM_MEGAPOTION",
                    "vanilla_item_name": "Mega-Potion",
                },
            ],
        }
        multidata = make_multidata()
        multidata["slot_data"][1]["bridge"]["locations"][0].update({
            "bridge_key": "LuckyMark:3",
            "source_table": "LuckyMark",
            "source_id": "3",
            "ap_location_id": 1263022897,
            "ap_location_name": "Lucky Emblems - Milestone 3",
            "type": "lucky_emblem",
        })
        multidata["locations"] = {1: {1263022897: (8002, 1, 0)}}
        multidata["datapackage"] = {
            "Kingdom Hearts III": {
                "item_name_to_id": {
                    "Ability: Pole Spin": 8002,
                },
            },
        }

        with (
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_default_kh3", return_value=default_options),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_json", return_value=extracted_data),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=wire_multidata(multidata)),
        ):
            result = export_randomized_options(SimpleNamespace(
                ap_zip=Path("AP_test.zip"),
                default_kh3=Path("DefaultKH3.json"),
                default_kh3_overrides=Path("DefaultKH3.ArchipelagoOverrides.json"),
                extracted_data=Path("extracted_locations.json"),
                runtime_root=Path("."),
                slot=1,
                chrinit_sora_have_ability=None,
                event_reward_override=[],
            ))

        self.assertEqual(result.randomized_options["LuckyMark"]["3"]["Reward"], "ITEM_AP_I02200_1263022897\u0000")
        self.assertEqual(result.foreign_item_manifest["foreign_item_count"], 0)
        placement = result.placement_summary["locations"][0]
        self.assertEqual(placement["location_type"], "lucky_emblem")
        self.assertFalse(placement["is_foreign_item"])
        self.assertEqual(placement["export_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(placement["native_reward_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(placement["runtime_token"], "ETresAbilityKind::POLE_SPIN")
        self.assertEqual(placement["item_display_name"], "Ability: Pole Spin")
        self.assertTrue(placement["bridge_delivery_required"])

    def test_export_defaults_lucky_emblem_local_native_item_to_native_ap_token(self) -> None:
        default_options = {
            "Event": make_always_on_event_options(),
            "LuckyMark": {"1": {"Reward": "ITEM_APUP\u0000"}},
        }
        extracted_data = {
            "items": [{"id": "PRT_ITEM03", "name": "Hero's Glove"}],
            "locations": [{
                "name": "Lucky Emblems - Milestone 1",
                "source_table": "LuckyMark",
                "source_id": "1",
                "type": "lucky_emblem",
                "world": "Lucky Emblems",
                "vanilla_item": "ITEM_APUP",
                "vanilla_item_name": "AP Boost",
            }],
        }
        multidata = make_multidata()
        multidata["slot_data"][1]["bridge"]["locations"][0].update({
            "bridge_key": "LuckyMark:1",
            "source_table": "LuckyMark",
            "source_id": "1",
            "ap_location_id": 1263022897,
            "ap_location_name": "Lucky Emblems - Milestone 1",
            "type": "lucky_emblem",
        })
        multidata["locations"] = {1: {1263022897: (8003, 1, 0)}}
        multidata["datapackage"] = {
            "Kingdom Hearts III": {
                "item_name_to_id": {"Hero's Glove": 8003},
            },
        }

        with (
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_default_kh3", return_value=default_options),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_json", return_value=extracted_data),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=wire_multidata(multidata)),
        ):
            result = export_randomized_options(SimpleNamespace(
                ap_zip=Path("AP_test.zip"),
                default_kh3=Path("DefaultKH3.json"),
                default_kh3_overrides=Path("DefaultKH3.ArchipelagoOverrides.json"),
                extracted_data=Path("extracted_locations.json"),
                runtime_root=Path("."),
                slot=1,
                chrinit_sora_have_ability=None,
                event_reward_override=[],
            ))

        self.assertEqual(result.randomized_options["LuckyMark"]["1"]["Reward"], "ITEM_AP_I02200_1263022897\u0000")
        placement = result.placement_summary["locations"][0]
        self.assertFalse(placement["is_foreign_item"])
        self.assertEqual(placement["export_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(placement["runtime_token"], "PRT_ITEM03")
        self.assertTrue(placement["bridge_delivery_required"])

    def test_export_ignores_retired_lucky_emblem_presentation_overrides(self) -> None:
        default_options = {
            "Event": make_always_on_event_options(),
            "LuckyMark": {
                "3": {
                    "Reward": "ITEM_MEGAPOTION\u0000",
                },
            },
        }
        extracted_data = {
            "items": [
                {"id": "ITEM_MEGAPOTION", "name": "Mega-Potion"},
            ],
            "locations": [
                {
                    "name": "Lucky Emblems - Milestone 3",
                    "source_table": "LuckyMark",
                    "source_id": "3",
                    "type": "lucky_emblem",
                    "world": "Lucky Emblems",
                    "vanilla_item": "ITEM_MEGAPOTION",
                    "vanilla_item_name": "Mega-Potion",
                },
            ],
        }
        multidata = make_multidata()
        multidata["slot_data"][1]["bridge"]["locations"][0].update({
            "bridge_key": "LuckyMark:3",
            "source_table": "LuckyMark",
            "source_id": "3",
            "ap_location_id": 1263022897,
            "ap_location_name": "Lucky Emblems - Milestone 3",
            "type": "lucky_emblem",
        })
        multidata["locations"] = {1: {1263022897: (7001, 2, 0)}}
        multidata["datapackage"] = {
            "Kingdom Hearts III": {
                "item_name_to_id": {
                    "Mega-Potion": 1,
                },
            },
            "A Link to the Past": {
                "item_name_to_id": {
                    "Hookshot": 7001,
                },
            },
        }

        with (
            patch.dict(os.environ, {
                "KH3_LUCKY_MARK_APUP_FALLBACK": "1",
                "KH3_LUCKY_MARK_NATIVE_AP_TOKENS": "0",
            }),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_default_kh3", return_value=default_options),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_json", return_value=extracted_data),
            patch("worlds.kh3.export_ap_kh3_randomized_options.load_multidata", return_value=wire_multidata(multidata)),
        ):
            result = export_randomized_options(SimpleNamespace(
                ap_zip=Path("AP_test.zip"),
                default_kh3=Path("DefaultKH3.json"),
                default_kh3_overrides=Path("DefaultKH3.ArchipelagoOverrides.json"),
                extracted_data=Path("extracted_locations.json"),
                runtime_root=Path("."),
                slot=1,
                chrinit_sora_have_ability=None,
                event_reward_override=[],
            ))

        self.assertEqual(result.randomized_options["LuckyMark"]["3"]["Reward"], "ITEM_AP_I02200_1263022897\u0000")
        self.assertEqual(result.foreign_item_manifest["foreign_items"][0]["placeholder_token"], "AP_ITEM_1263022897")
        self.assertNotIn("lucky_mark_native_ap_tokens", result.placement_summary)
        self.assertEqual(result.placement_summary["locations"][0]["export_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["native_reward_token"], "ITEM_AP_I02200_1263022897")
        self.assertEqual(result.placement_summary["locations"][0]["runtime_token"], "AP_ITEM_000")

    def test_bridge_data_export_defaults_lucky_emblem_foreign_item_to_native_ap_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": make_always_on_event_options(),
                "LuckyMark": {
                    "10": {
                        "Reward": "PRT_ITEM40\0",
                    },
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [],
                "locations": [
                    {
                        "name": "Lucky Emblems - Milestone 10",
                        "source_table": "LuckyMark",
                        "source_id": "10",
                        "type": "lucky_emblem",
                        "world": "Lucky Emblems",
                        "vanilla_item": "PRT_ITEM40",
                        "vanilla_item_name": "Star Charm",
                    },
                ],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023262,
                    "ap_location_name": "Lucky Emblems - Milestone 10",
                    "assigned_item_game": "Other Game",
                    "assigned_item_id": 7001,
                    "assigned_item_name": "Foreign Lucky Item",
                    "assigned_item_player": 2,
                    "assigned_item_player_name": "Other",
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

        self.assertEqual(result.randomized_options["LuckyMark"]["10"]["Reward"], "ITEM_AP_I02200_1263023262\0")
        self.assertEqual(result.placement_summary["locations"][0]["export_token"], "ITEM_AP_I02200_1263023262")
        self.assertEqual(result.placement_summary["locations"][0]["native_reward_token"], "ITEM_AP_I02200_1263023262")
        self.assertEqual(result.placement_summary["locations"][0]["runtime_token"], "AP_ITEM_000")

    def test_bridge_data_export_defaults_lucky_emblem_local_bonus_to_native_ap_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            default_path = temp_path / "DefaultKH3.json"
            extracted_path = temp_path / "extracted_locations.json"
            default_path.write_text(json.dumps({
                "Event": make_always_on_event_options(),
                "LuckyMark": {
                    "50": {
                        "Reward": "PRT_ITEM40\0",
                    },
                },
            }), encoding="utf-8")
            extracted_path.write_text(json.dumps({
                "items": [
                    {"id": "ETresVictoryBonusKind::DEF_SLOT_UP1", "name": "Bonus: Armor Slot +1"},
                ],
                "locations": [
                    {
                        "name": "Lucky Emblems - Milestone 50",
                        "source_table": "LuckyMark",
                        "source_id": "50",
                        "type": "lucky_emblem",
                        "world": "Lucky Emblems",
                        "vanilla_item": "PRT_ITEM40",
                        "vanilla_item_name": "Star Charm",
                    },
                ],
            }), encoding="utf-8")
            bridge_locations = [
                {
                    "ap_location_id": 1263023270,
                    "ap_location_name": "Lucky Emblems - Milestone 50",
                    "assigned_item_game": "Kingdom Hearts III",
                    "assigned_item_id": 1263022400,
                    "assigned_item_name": "Bonus: Armor Slot +1",
                    "assigned_item_player": 1,
                    "assigned_item_player_name": "KH3",
                    "bridge_key": "LuckyMark:50",
                    "source_id": "50",
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

        self.assertEqual(result.randomized_options["LuckyMark"]["50"]["Reward"], "ITEM_AP_I02200_1263023270\0")
        placement = result.placement_summary["locations"][0]
        self.assertEqual(placement["export_token"], "ITEM_AP_I02200_1263023270")
        self.assertEqual(placement["native_reward_token"], "ITEM_AP_I02200_1263023270")
        self.assertEqual(placement["runtime_token"], "ETresVictoryBonusKind::DEF_SLOT_UP1")
        self.assertTrue(placement["bridge_delivery_required"])


if __name__ == "__main__":
    unittest.main()
