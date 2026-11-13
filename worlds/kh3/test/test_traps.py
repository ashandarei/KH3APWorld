from __future__ import annotations

from unittest import TestCase

from BaseClasses import ItemClassification

from ..Data import (
    BAD_STATUS_TRAP_ITEM_NAMES,
    TRAP_ITEM_NAMES,
    KING_MICKEY_TRAP_ITEM_NAME,
    BETRAYAL_TRAP_ITEM_NAME,
    BETRAYAL_TRAP_RUNTIME_TOKEN,
    SPELLWEAVER_UNLOCK_ITEM,
    KING_MICKEY_TRAP_RUNTIME_TOKEN,
    local_seeded_reward_requires_bridge_delivery,
    BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME,
    PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES,
    classify_runtime_grant_method,
    resolve_item_runtime_token,
)
from ..Items import classify_item, item_name_to_id
from ..Options import KH3LocationPoolMode
from . import KH3TestBase


class TestKH3TrapItems(TestCase):
    def test_traps_are_appended_ap_items_with_direct_status_grants(self) -> None:
        previous_ap_only_max = max(
            item_name_to_id[item_name]
            for item_name in PROGRESSIVE_KEYBLADE_LEVEL_ITEM_NAMES
        )

        for item_name, runtime_token in BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.items():
            with self.subTest(item_name=item_name):
                self.assertGreater(item_name_to_id[item_name], previous_ap_only_max)
                self.assertEqual(classify_item(item_name), ItemClassification.trap)
                self.assertEqual(resolve_item_runtime_token(item_name), runtime_token)
                self.assertEqual(classify_runtime_grant_method(runtime_token), "direct_player_bad_status")


    def test_mickey_trap_uses_native_swap_and_bridge_delivery(self) -> None:
        self.assertEqual(set(TRAP_ITEM_NAMES), {"Sneeze Trap", "Burn Trap", "Stun Trap", "King Mickey Trap", "Betrayal Trap"})
        self.assertEqual(classify_item(KING_MICKEY_TRAP_ITEM_NAME), ItemClassification.trap)
        self.assertEqual(resolve_item_runtime_token(KING_MICKEY_TRAP_ITEM_NAME), KING_MICKEY_TRAP_RUNTIME_TOKEN)
        self.assertEqual(classify_runtime_grant_method(KING_MICKEY_TRAP_RUNTIME_TOKEN), "king_mickey_trap")
        self.assertGreater(item_name_to_id[KING_MICKEY_TRAP_ITEM_NAME], item_name_to_id[SPELLWEAVER_UNLOCK_ITEM])
        self.assertTrue(local_seeded_reward_requires_bridge_delivery(
            "Treasure:TEST", "treasure", KING_MICKEY_TRAP_RUNTIME_TOKEN, persistent_chests=False))

    def test_betrayal_trap_uses_party_hostility_and_bridge_delivery(self) -> None:
        self.assertEqual(classify_item(BETRAYAL_TRAP_ITEM_NAME), ItemClassification.trap)
        self.assertEqual(resolve_item_runtime_token(BETRAYAL_TRAP_ITEM_NAME), BETRAYAL_TRAP_RUNTIME_TOKEN)
        self.assertEqual(classify_runtime_grant_method(BETRAYAL_TRAP_RUNTIME_TOKEN), "betrayal_trap")
        self.assertGreater(item_name_to_id[BETRAYAL_TRAP_ITEM_NAME], item_name_to_id["Playable Character: Riku"])
        self.assertTrue(local_seeded_reward_requires_bridge_delivery(
            "Treasure:TEST", "treasure", BETRAYAL_TRAP_RUNTIME_TOKEN, persistent_chests=False))

    def test_supported_bad_status_traps(self) -> None:
        self.assertEqual(set(BAD_STATUS_TRAP_ITEM_NAMES), {"Sneeze Trap", "Burn Trap", "Stun Trap"})
        self.assertEqual(
            set(BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME.values()),
            {
                "ETresBadStatusType::TRES_BADSTAT_SNEEZE",
                "ETresBadStatusType::TRES_BADSTAT_BURN",
                "ETresBadStatusType::TRES_BADSTAT_STUN",
            },
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresBadStatusType::TRES_BADSTAT_STUN"),
            "direct_player_bad_status",
        )
        self.assertEqual(
            classify_runtime_grant_method("ETresBadStatusType::TRES_BADSTAT_FREEZE"),
            "bp_tres_get_item",
        )

    def test_stun_is_appended_and_uses_bridge_delivery(self) -> None:
        self.assertEqual(item_name_to_id["Betrayal Trap"], 1263022660)
        self.assertEqual(item_name_to_id["Stun Trap"], item_name_to_id["Betrayal Trap"] + 1)
        self.assertTrue(local_seeded_reward_requires_bridge_delivery(
            "Treasure:TEST", "treasure", BAD_STATUS_TRAP_RUNTIME_TOKEN_BY_ITEM_NAME["Stun Trap"],
            persistent_chests=False))


class TestKH3TrapGenerationDisabled(KH3TestBase):
    options = {
        "remind_pool": KH3LocationPoolMode.option_junk,
        "trap_percentage": 0,
    }

    def test_zero_percent_keeps_traps_out_of_the_generated_pool(self) -> None:
        self.world_setup(seed=1)

        self.assertTrue(
            set(TRAP_ITEM_NAMES).isdisjoint(
                item.name for item in self.multiworld.itempool
            )
        )


class TestKH3TrapGenerationFull(KH3TestBase):
    options = {
        "remind_pool": KH3LocationPoolMode.option_junk,
        "trap_percentage": 100,
    }

    def test_full_percentage_replaces_every_filler_without_resizing_pool(self) -> None:
        self.world_setup(seed=1)

        local_items = [
            item
            for item in self.multiworld.itempool
            if item.player == self.player
        ]
        trap_items = [item for item in local_items if item.classification == ItemClassification.trap]
        self.assertTrue(trap_items)
        self.assertEqual({item.name for item in trap_items}, set(TRAP_ITEM_NAMES))
        self.assertFalse(
            any(item.classification == ItemClassification.filler for item in local_items)
        )
        active_locations = [
            self.multiworld.get_location(row["name"], self.player)
            for row in self.world.active_locations
        ]
        locked_rewards = {
            location.name: location.item.name
            for location in active_locations
            if location.locked and location.item is not None
        }
        self.assertEqual(locked_rewards, {
            "Re Mind - Unlock Kairi": "Playable Character: Kairi",
            "Keyblade Graveyard - Unlock Roxas": "Playable Character: Roxas",
            "0.2 - Unlock Aqua": "Playable Character: Aqua",
            "Data Battles - Unlock Riku": "Playable Character: Riku",
        })
        randomized_locations = [location for location in active_locations if location.item is None]
        self.assertEqual(len(active_locations), len(randomized_locations) + len(locked_rewards))
        self.assertEqual(len(self.multiworld.itempool), len(randomized_locations))
        self.assertEqual(self.world.build_runtime_slot_data()["trap_percentage"], 100)
