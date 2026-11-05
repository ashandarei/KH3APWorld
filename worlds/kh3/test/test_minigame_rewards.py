from __future__ import annotations

import json
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from BaseClasses import Item, ItemClassification
from Fill import distribute_items_restrictive
from ..seedgen.localization import Locres

from ..Data import (
    build_item_name_to_id,
    load_extracted_data,
    normalize_locations,
    resolve_item_runtime_token,
    resolve_runtime_token_display_name,
    uses_minigame_rank_junk,
)
from ..export_ap_kh3_randomized_options import (
    build_item_name_to_token,
    export_randomized_options_from_bridge_data,
)
from ..Locations import BASE_LOCATION_ID, location_name_to_id
from ..Minigames import (
    MINIGAME_APPENDED_ITEMS,
    MINIGAME_ITEM_REWARDS,
    MINIGAME_MEDAL_ITEMS,
    MINIGAME_POOL_OPTION_NAMES,
    MINIGAME_POOL_OPTIONS,
    MINIGAME_RANK_OPTIONS,
    MINIGAME_REWARDS,
    minigame_pool_option,
    minigame_reward_rank,
)
from ..Options import KH3Options
from . import KH3TestBase


class TestMinigameRewards(KH3TestBase):
    def test_medal_runtime_tokens_match_native_english_names(self):
        root = Path(__file__).resolve().parents[1]
        locres = Locres((root / "randomizer/Content/Localization/Game/en/kh3_item.locres").read_bytes())
        accessory_names = {
            key: locres.strings[entry[1]]
            for key, entry in locres.namespaces["kh3_item.accessary"].items()
        }
        exported = build_item_name_to_token(load_extracted_data())
        for number in (28, 29, 30):
            token = f"ACC_ITEM{number}"
            name = accessory_names[f"item_{number}"]
            with self.subTest(token=token, name=name):
                self.assertEqual(MINIGAME_MEDAL_ITEMS[name], token)
                self.assertEqual(resolve_item_runtime_token(name), token)
                self.assertEqual(resolve_runtime_token_display_name(token), name)
                self.assertEqual(exported[name], token)

    def test_rank_caps_fill_every_mode_and_preserve_unrelated_rewards(self):
        for option in MINIGAME_RANK_OPTIONS:
            for cap, label in KH3Options.type_hints[option].name_lookup.items():
                with self.subTest(option=option, cap=label):
                    self.options = {**dict.fromkeys(MINIGAME_RANK_OPTIONS, "a"), option: label,
                                    **dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, "randomize"), "flantastic_flans_pool": "randomize"}
                    self.world_setup(seed=6110 + cap)
                    ranked_rows = [r for r in self.world.active_locations
                                   if minigame_reward_rank(r, option) is not None]
                    self.assertEqual(len(ranked_rows), 6)
                    for row in ranked_rows:
                        restricted = minigame_reward_rank(row, option) > cap
                        location = self.multiworld.get_location(row["name"], self.player)
                        self.assertEqual(location.filler_only, restricted)
                        for player in (1, 2):
                            for classification in (ItemClassification.progression, ItemClassification.useful,
                                                   ItemClassification.progression | ItemClassification.useful):
                                self.assertEqual(location.item_rule(Item("Test reward", classification, 1, player)), not restricted)
                            self.assertTrue(location.item_rule(Item("Junk", ItemClassification.filler, 1, player)))
                            self.assertTrue(location.item_rule(Item("Trap", ItemClassification.trap, 1, player)))
                    for row in self.world.active_locations:
                        if row["type"] == "minigame_reward" or row["source_id"] in MINIGAME_POOL_OPTIONS:
                            if minigame_reward_rank(row, option) is None:
                                location = self.multiworld.get_location(row["name"], self.player)
                                self.assertTrue(location.item_rule(self.world.create_item("Magic: Fire")))
                                self.assertFalse(location.filler_only)
                    slot = self.world.build_runtime_slot_data()
                    self.assertEqual(slot[option], cap)
                    self.assertEqual(slot["universal_tracker"]["options"][option], cap)
                    self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
                    distribute_items_restrictive(self.multiworld)
                    self.assertFalse(self.multiworld.get_unfilled_locations())
                    self.assertTrue(self.multiworld.can_beat_game())
                    for row in ranked_rows:
                        if minigame_reward_rank(row, option) > cap:
                            reward = self.multiworld.get_location(row["name"], self.player).item
                            self.assertFalse(reward.classification & (ItemClassification.progression | ItemClassification.useful))

    def test_each_minigame_pool_is_independent_including_treasures(self):
        for selected in MINIGAME_POOL_OPTION_NAMES:
            with self.subTest(pool=selected):
                self.options = dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, "randomize")
                self.options.update(dict.fromkeys(MINIGAME_RANK_OPTIONS, "a"))
                self.options.update({selected: "junk", "flantastic_flans_pool": "randomize",
                                     "toy_box_pool": "junk", "arendelle_pool": "junk"})
                self.world_setup(seed=6160)
                counts = dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, 0)
                for row in self.world.active_locations:
                    pool = minigame_pool_option(row)
                    if pool is None:
                        continue
                    location = self.multiworld.get_location(row["name"], self.player)
                    self.assertEqual(location.filler_only, pool == selected, row["name"])
                    self.assertEqual(location.item_rule(self.world.create_item("Magic: Fire")), pool != selected)
                    if pool in counts:
                        counts[pool] += 1
                self.assertEqual(counts, {pool: 8 if pool == "frozen_slider_pool" else 6
                                          for pool in MINIGAME_POOL_OPTION_NAMES})
                exported = self.world.build_runtime_slot_data()["universal_tracker"]["options"]
                self.assertNotIn("minigames_pool", exported)
                for pool in counts:
                    self.assertEqual(exported[pool], 3 if pool == selected else 2)
                distribute_items_restrictive(self.multiworld)
                self.assertFalse(self.multiworld.get_unfilled_locations())
                self.assertTrue(self.multiworld.can_beat_game())

    def test_missing_rank_caps_keep_legacy_behavior(self):
        for row in load_extracted_data()["locations"]:
            self.assertFalse(uses_minigame_rank_junk(row, SimpleNamespace()))

    def test_junk_minigame_pools_restrict_every_rank(self):
        self.options = {option: "B" for option in MINIGAME_RANK_OPTIONS}
        self.options.update(dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, "junk"))
        self.world_setup(seed=6120)
        fire = self.world.create_item("Magic: Fire")
        for option in MINIGAME_RANK_OPTIONS:
            for row in self.world.active_locations:
                rank = minigame_reward_rank(row, option)
                if rank is not None:
                    location = self.multiworld.get_location(row["name"], self.player)
                    self.assertTrue(location.filler_only)
                    self.assertFalse(location.item_rule(fire))

    def test_mixed_caps_remain_independent_and_fill_together(self):
        self.options = dict(zip(MINIGAME_RANK_OPTIONS, ("junk", "d", "c", "b", "a")))
        self.options.update(dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, "randomize"))
        self.world_setup(seed=6140)
        for cap, option in enumerate(MINIGAME_RANK_OPTIONS):
            for row in self.world.active_locations:
                rank = minigame_reward_rank(row, option)
                if rank is not None:
                    location = self.multiworld.get_location(row["name"], self.player)
                    self.assertEqual(location.item_rule(self.world.create_item("Magic: Fire")), rank <= cap)
        distribute_items_restrictive(self.multiworld)
        self.assertFalse(self.multiworld.get_unfilled_locations())
        self.assertTrue(self.multiworld.can_beat_game())

    def test_item_prize_catalog_and_published_ids(self):
        current = load_extracted_data()
        rows = [r for r in current["locations"] if r["type"] == "minigame_reward"]
        self.assertEqual(len(rows), 26)
        self.assertEqual(len({r["source_id"] for r in rows}), 26)
        for reward in MINIGAME_ITEM_REWARDS:
            self.assertEqual(reward["crc"], zlib.crc32(reward["source_id"].encode("utf-16le")))
        with (patch("worlds.kh3.Data.append_minigame_item_locations"),
              patch("worlds.kh3.Data.append_workshop_locations"),
              patch("worlds.kh3.Data.append_adversary_locations")):
            previous = load_extracted_data()
        for index, row in enumerate(r for r in normalize_locations(previous) if r["source_id"] != "EVENT_REPORT_000"):
            self.assertEqual(location_name_to_id[row["name"]], BASE_LOCATION_ID + index)
        old_ids = build_item_name_to_id(previous)
        new_ids = build_item_name_to_id(current)
        for name, item_id in old_ids.items():
            self.assertEqual(new_ids[name], item_id)
        for name, token in MINIGAME_APPENDED_ITEMS.items():
            self.assertEqual(resolve_item_runtime_token(name), token)

    def test_item_prize_modes_logic_and_bridge_export(self):
        root = Path(__file__).resolve().parents[1]
        for mode in ("randomize", "junk"):
            with self.subTest(mode=mode):
                self.options = dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, mode)
                self.options.update(dict.fromkeys(MINIGAME_RANK_OPTIONS, "a"))
                self.world_setup(seed=6041)
                rows = [r for r in self.world.active_locations if r["type"] == "minigame_reward"]
                self.assertEqual(len(rows), 26)
                self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
                self.collect_all_but([])
                for row in rows:
                    location = self.multiworld.get_location(row["name"], self.player)
                    self.assertTrue(location.can_reach(self.multiworld.state))
                    self.assertEqual(location.filler_only, mode == "junk")
                bridge = [r for r in self.world.build_runtime_slot_data()["bridge"]["locations"]
                          if r["type"] == "minigame_reward"]
                names = list(MINIGAME_APPENDED_ITEMS)
                for index, row in enumerate(bridge):
                    row.update(assigned_item_name=names[index % len(names)] if index % 2 else "Remote item",
                               assigned_item_game="Kingdom Hearts III" if index % 2 else "Other Game",
                               assigned_item_player=1 if index % 2 else 2,
                               assigned_item_player_name="Tester" if index % 2 else "Other")
                    self.assertEqual(row["minigame_reward_crc"], zlib.crc32(row["source_id"].encode("utf-16le")))
                    self.assertTrue(row["minigame_reward_tokens"])
                result = export_randomized_options_from_bridge_data(
                    default_kh3=root / "randomizer/wwwroot/DefaultKH3.json",
                    default_kh3_overrides=root / "randomizer/wwwroot/DefaultKH3.ArchipelagoOverrides.json",
                    extracted_data_path=root / "data/extracted_locations.json", slot=1,
                    slot_name="Tester", archive_name="AP_test.zip", bridge_locations=bridge)
                self.assertEqual(len(result.placement_summary["locations"]), len(rows))
                for row in result.placement_summary["locations"]:
                    self.assertTrue(row["bridge_delivery_required"] or row["is_foreign_item"])
                self.assertNotIn("MinigameReward", result.randomized_options)

    def test_catalog_matches_native_reward_rows(self):
        root = Path(__file__).resolve().parents[1]
        native = json.loads((root / "randomizer/wwwroot/DefaultKH3.json").read_text())["VBonus"]
        for row in load_extracted_data()["locations"]:
            if row["source_id"] in MINIGAME_POOL_OPTIONS:
                self.assertEqual(row["vanilla_items"], {
                    field: token.rstrip("\0") for field, token in native[row["source_id"]].items()
                })

    def test_catalog_ids_logic_and_pool_modes(self):
        self.options = {"flantastic_flans_pool": "randomize", **dict.fromkeys(MINIGAME_POOL_OPTION_NAMES, "junk"), "toy_box_pool": "junk"}
        self.world_setup(seed=602)
        rows = [row for row in self.world.active_locations if row["source_id"] in MINIGAME_POOL_OPTIONS]
        self.assertEqual(len(rows), 13)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
        self.collect_all_but([])
        for row in rows:
            location = self.multiworld.get_location(row["name"], self.player)
            self.assertTrue(location.can_reach(self.multiworld.state), row["name"])
            self.assertEqual(location.filler_only, int(row["source_id"][-3:]) <= 6)
        strawberry = self.get_location_by_source("VBonus", "VBonus_Minigame008")
        self.remove(self.world.create_item("Favorite Deputy"))
        self.assertFalse(strawberry.can_reach(self.multiworld.state))
        # Compare every older ID against the same catalog without this addition.
        with (patch("worlds.kh3.Data.append_minigame_locations"),
              patch("worlds.kh3.Data.append_minigame_item_locations"),
              patch("worlds.kh3.Data.append_workshop_locations"),
              patch("worlds.kh3.Data.append_adversary_locations")):
            old = [r for r in normalize_locations(load_extracted_data()) if r["source_id"] != "EVENT_REPORT_000"]
        for index, row in enumerate(old):
            self.assertEqual(location_name_to_id[row["name"]], BASE_LOCATION_ID + index)
        self.assertEqual(min(location_name_to_id[row["name"]] for row in rows), BASE_LOCATION_ID + len(old))

    def test_challenges_inherit_story_completion_requirements(self):
        self.world_setup(seed=603)
        self.collect_all_but([])
        for suffix, _, _, _, completion in MINIGAME_REWARDS:
            challenge = self.get_location_by_source("VBonus", f"VBonus_Minigame{suffix}")
            story = self.get_location_by_source("Event", completion)
            with patch.object(story, "access_rule", return_value=False):
                self.reset_reachability(self.multiworld.state)
                self.assertFalse(challenge.can_reach(self.multiworld.state), suffix)
            self.reset_reachability(self.multiworld.state)

    def test_pak_export_suppresses_local_and_remote_rewards(self):
        self.world_setup(seed=604)
        bridge = self.world.build_runtime_slot_data()["bridge"]["locations"]
        rows = [row for row in bridge if row["source_id"] in MINIGAME_POOL_OPTIONS]
        for index, row in enumerate(rows):
            row.update(assigned_item_name="Potion" if index % 2 == 0 else "Foreign Test Item",
                       assigned_item_game="Kingdom Hearts III" if index % 2 == 0 else "Other Game",
                       assigned_item_player=1 if index % 2 == 0 else 2,
                       assigned_item_player_name="Tester" if index % 2 == 0 else "Other")
        root = Path(__file__).resolve().parents[1]
        result = export_randomized_options_from_bridge_data(
            default_kh3=root / "randomizer/wwwroot/DefaultKH3.json",
            default_kh3_overrides=root / "randomizer/wwwroot/DefaultKH3.ArchipelagoOverrides.json",
            extracted_data_path=root / "data/extracted_locations.json", slot=1,
            slot_name="Tester", archive_name="AP_test.zip", bridge_locations=rows)
        self.assertEqual(len(result.placement_summary["locations"]), 13)
        for row in rows:
            self.assertTrue(all(token.rstrip("\0").endswith("::NONE")
                                for token in result.randomized_options["VBonus"][row["source_id"]].values()))
        for row in result.placement_summary["locations"]:
            self.assertTrue(row["bridge_delivery_required"] or row["is_foreign_item"])
