import copy
import json
import random
from types import SimpleNamespace
from unittest import TestCase

from BaseClasses import CollectionState
from NetUtils import convert_to_base_types

from ..Data import PORTAL_KEYBLADE_ITEM_NAMES, WORLD_LOCATION_POOL_OPTIONS, uses_junk_chests
from ..KeybladeChests import KEYBLADE_CHEST_FIELDS, assign_keyblade_chests, native_chest_keyblade
from ..MelodyOfMemory import select_music_chests
from . import KH3TestBase


def options(enabled=True, **values):
    values = {**dict.fromkeys(WORLD_LOCATION_POOL_OPTIONS.values(), 2), "chestsanity": enabled,
              "portal_unlocks": 0,
              "junk_san_fransokyo_chests": False, "junk_the_caribbean_chests": False, **values}
    return SimpleNamespace(**{k: SimpleNamespace(value=v) for k, v in values.items()})


def candidates():
    return [{"type": "treasure", "source_table": "TreasureRA", "source_id": f"RA_SBOX_{i:03}",
             "world": "Kingdom of Corona"} for i in range(60)]


class TestChestsanitySelection(TestCase):

    def test_disabled_does_not_consume_rng(self):
        rows = candidates()
        rng = random.Random(3)
        state = rng.getstate()
        assign_keyblade_chests(rng, rows, options(False))
        self.assertEqual(candidates(), rows)
        self.assertEqual(state, rng.getstate())

    def test_keyblade_doors_use_only_the_three_non_door_chest_keys(self):
        rows, other = candidates(), candidates()
        assign_keyblade_chests(random.Random(3), rows, options())
        assign_keyblade_chests(random.Random(3), other, options())
        self.assertEqual(rows, other)
        self.assertTrue(all("keyblade_chest_keyblade" in row for row in rows))
        self.assertEqual({"Classic Tone", "Grand Chef", "Hunny Spout"},
                         {row["keyblade_chest_keyblade"] for row in rows})
        self.assertFalse(any(row["keyblade_chest_native"] for row in rows))

    def test_character_doors_use_door_keyblades_and_gate_same_world_chests(self):
        rows = candidates()
        assign_keyblade_chests(random.Random(3), rows, options(portal_unlocks=1))
        self.assertEqual(PORTAL_KEYBLADE_ITEM_NAMES, {row["keyblade_chest_keyblade"] for row in rows})
        self.assertTrue(any(row["keyblade_chest_keyblade"] == "Ever After" for row in rows))
        self.assertFalse(any(row["keyblade_chest_native"] for row in rows))

    def test_tracker_rejects_assignments_from_the_other_door_mode(self):
        for mode in (0, 1):
            rows = candidates()
            assign_keyblade_chests(random.Random(3), rows, options(portal_unlocks=mode))
            saved = {"bridge": {"locations": rows}}
            restored = candidates()
            assign_keyblade_chests(random.Random(98), restored, options(portal_unlocks=mode), saved)
            self.assertEqual(rows, restored)
            with self.assertRaises(ValueError):
                assign_keyblade_chests(random.Random(3), candidates(), options(portal_unlocks=1-mode), saved)

    def test_melody_overrides_keyblade_and_junk_worlds_are_excluded(self):
        rows = candidates()
        select_music_chests(random.Random(4), rows, 0, 20, 0)
        assign_keyblade_chests(random.Random(3), rows, options())
        self.assertEqual(40, sum("keyblade_chest_keyblade" in row for row in rows))
        self.assertTrue(all(not any(f in row for f in KEYBLADE_CHEST_FIELDS)
                            for row in rows if "mom_song_id" in row))
        assign_keyblade_chests(random.Random(3), rows, options(kingdom_of_corona_pool=3))
        self.assertTrue(all(not any(f in row for f in KEYBLADE_CHEST_FIELDS) for row in rows))

    def test_tracker_restores_exact_assignments_and_rejects_invalid_keys(self):
        rows = candidates()
        assign_keyblade_chests(random.Random(3), rows, options())
        saved = {"bridge": {"locations": rows}}
        restored = candidates()
        assign_keyblade_chests(random.Random(98), restored, options(), saved)
        self.assertEqual(rows, restored)
        for field, invalid in (("keyblade_chest_keyblade", "Ultima Weapon"),
                               ("keyblade_chest_token", "ITEM_POTION"),
                               ("keyblade_chest_native", "false")):
            bad = copy.deepcopy(saved)
            bad["bridge"]["locations"][0][field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                assign_keyblade_chests(random.Random(3), candidates(), options(), bad)
        with self.assertRaises(ValueError):
            assign_keyblade_chests(random.Random(3), candidates()[:-1], options(), saved)

    def test_junk_chest_toggles_do_not_change_keyblade_assignment(self):
        rows = [dict(row, world="San Fransokyo", source_table="TreasureBX") for row in candidates()]
        other = copy.deepcopy(rows)
        assign_keyblade_chests(random.Random(3), rows, options(junk_san_fransokyo_chests=True))
        assign_keyblade_chests(random.Random(3), other, options())
        self.assertEqual(rows, other)
        self.assertTrue(uses_junk_chests(rows[0], options(junk_san_fransokyo_chests=True)))
        self.assertFalse(uses_junk_chests(dict(rows[0], type="event"),
                                        options(junk_san_fransokyo_chests=True)))


class TestChestsanityWorld(KH3TestBase):
    options = {"chestsanity": 1, "music_chest_count": 8, "music_chest_vanilla_conversion": 32,
               "the_caribbean_pool": "junk"}

    def test_tracker_reconstructs_wire_assignments_without_rerolling(self):
        from .test_universal_tracker import TestKH3UniversalTracker
        slot = convert_to_base_types(self.world.build_runtime_slot_data())
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot).worlds[1]
        self.assertEqual(self.world.active_locations, tracker.active_locations)

    def test_cross_world_requirement_is_enforced_and_keyblade_is_progression(self):
        row = next(r for r in self.world.active_locations if r.get("keyblade_chest_keyblade")
                   and not r["keyblade_chest_native"])
        blade = row["keyblade_chest_keyblade"]
        state = CollectionState(self.multiworld)
        while state.has(blade, self.player):
            state.remove(self.world.create_item(blade))
        for item in self.multiworld.get_items():
            if item.name != blade:
                state.collect(item, True)
        location = self.multiworld.get_location(row["name"], self.player)
        self.assertFalse(location.can_reach(state))
        item = self.world.create_item(blade)
        self.assertTrue(item.advancement)
        state.collect(item, True)
        self.assertTrue(location.can_reach(state))

    def test_client_publishes_seed_metadata_without_overwriting_native_manifest(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from ..bridge_support.files import atomic_write_json, load_optional_json_file
        from ..client_support.bridge_sync_service import BridgeSyncService
        service = object.__new__(BridgeSyncService)
        service._published_payloads = {}
        service.load_optional_json = load_optional_json_file
        service.atomic_write_json = atomic_write_json
        slot = self.world.build_runtime_slot_data()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "runtime_keyblade_chests.json"
            service.publish(root, slot_data=slot, status_payload={}, items_payload={})
            self.assertFalse(path.exists())
            native = {"owner": "dll", "chests": ["native projection"]}
            path.write_text(json.dumps(native))
            service.publish(root, slot_data=slot, status_payload={}, items_payload={})
            disabled = {"chestsanity": 0, "kh3_seed": {"seed_name": "next", "slot": 1},
                        "bridge": {"locations": []}}
            service.publish(root, slot_data=disabled, status_payload={}, items_payload={})
            self.assertEqual(native, json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(disabled, json.loads((root / "slot_data.json").read_text(encoding="utf-8")))

    def test_junk_world_and_all_melody_checks_have_no_keyblade_requirement(self):
        for row in self.world.active_locations:
            if row["world"] == "The Caribbean" or "mom_song_id" in row:
                self.assertFalse(any(field in row for field in KEYBLADE_CHEST_FIELDS))


    def test_disconnected_client_leaves_manifest_construction_to_dll(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from ..bridge_support.files import atomic_write_json, load_optional_json_file
        from ..client_support.offline_bridge_service import OfflineBridgeInitializationInput
        from .test_client_status import TestOfflineBridgeService
        slot = self.world.build_runtime_slot_data()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pak = root / "seed.pak"
            pak.write_bytes(b"test pak")
            service = TestOfflineBridgeService.build_service(
                load_optional_json=load_optional_json_file, atomic_write_json=atomic_write_json,
            )
            source = OfflineBridgeInitializationInput(None, root, root, None, None, None, None)
            service.write_offline_files(
                source=source, metadata={"slot_data": slot, "seed": "test", "slot": 1, "slot_name": "Sora"},
                pak_path=pak, bridge_dir=root, seed_name="test", slot_data=slot,
                location_count=len(slot["bridge"]["locations"]), preserve_previous_status=False,
            )
            self.assertFalse((root / "runtime_keyblade_chests.json").exists())
            # Runtime publication omits network-only hint/player metadata.
            # Generated location assignments must survive that projection.
            published = load_optional_json_file(root / "slot_data.json")
            self.assertEqual(json.loads(json.dumps(slot["bridge"]["locations"])),
                             published["bridge"]["locations"])
            self.assertEqual(published["chestsanity"], 1)


class TestCharacterDoorChestsanity(KH3TestBase):
    options = {"chestsanity": 1, "portal_unlocks": "party_members"}

    def test_native_world_keyblade_still_gates_the_chest(self):
        row = next(r for r in self.world.active_locations
                   if r.get("keyblade_chest_keyblade") and r["keyblade_chest_keyblade"] == native_chest_keyblade(r))
        name = row["keyblade_chest_keyblade"]
        state = CollectionState(self.multiworld)
        while state.has(name, self.player):
            state.remove(self.world.create_item(name))
        for item in self.multiworld.get_items():
            if item.name != name:
                state.collect(item, True)
        location = self.multiworld.get_location(row["name"], self.player)
        self.assertTrue(self.get_location_base_region(location).can_reach(state))
        self.assertFalse(location.can_reach(state))
        blade = self.world.create_item(name)
        self.assertTrue(blade.advancement)
        state.collect(blade, True)
        self.assertTrue(location.can_reach(state))

    def test_tracker_reconstructs_character_door_chests(self):
        from .test_universal_tracker import TestKH3UniversalTracker
        slot = convert_to_base_types(self.world.build_runtime_slot_data())
        tracker = TestKH3UniversalTracker.build_yamlless_tracker_multiworld(self, slot).worlds[1]
        self.assertEqual(self.world.active_locations, tracker.active_locations)


class TestJunkChestRewards(KH3TestBase):
    options = {"junk_san_fransokyo_chests": 1, "junk_the_caribbean_chests": 1}

    def test_excluded_vanilla_rewards_are_replaced_in_generated_pool(self):
        from ..Data import build_item_counts

        # These source rewards exist even though repeatable filler never rolls
        # Orichalcum+. Check the assembled pool, not just the filler chooser.
        counts = build_item_counts(self.world.active_locations + self.world.starting_inventory_locations)
        pool_names = {item.name for item in self.multiworld.itempool}
        for name in ("Orichalcum+", "Golden Herc Figure", "Gummiphone", "AR Device"):
            with self.subTest(item=name):
                self.assertGreater(counts[name], 0)
                self.assertNotIn(name, pool_names)
        self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))

    def test_orichalcum_plus_is_excluded_in_every_ultima_mode(self):
        from collections import Counter
        from ..Data import ULTIMA_WEAPON_SHARD_ITEM_NAME
        from ..Options import UltimaWeaponMode

        for mode in range(5):
            with self.subTest(mode=mode):
                self.options = {"junk_san_fransokyo_chests": 1, "junk_the_caribbean_chests": 1,
                                "ultima_weapon_mode": mode, "frozen_slider_pool": "randomize",
                                "frozen_slider_max_rank": "a"}
                self.world_setup(seed=73)
                counts = Counter(item.name for item in self.multiworld.itempool)
                self.assertEqual(counts["Orichalcum+"], 0)
                self.assertNotIn("Orichalcum+", self.world.item_name_to_id)
                self.assertEqual(counts[ULTIMA_WEAPON_SHARD_ITEM_NAME],
                                 7 if mode == UltimaWeaponMode.option_shards else 0)
                self.assertEqual(len(self.multiworld.itempool), len(self.multiworld.get_unfilled_locations()))
                # Generic plando/item-link creation must not reintroduce it.
                with self.assertRaisesRegex(ValueError, "use Ultima Weapon Shard"):
                    self.world.create_item("Orichalcum+")

    def test_only_chests_reject_progression_and_useful(self):
        progression = self.world.create_item("Proof of Promises")
        useful = self.world.create_item("Map: Huddled Isles")
        filler = self.world.create_item("Potion")
        checked = set()
        for row in self.world.active_locations:
            if row["source_table"] in {"TreasureBX", "TreasureCA"}:
                location = self.multiworld.get_location(row["name"], self.player)
                self.assertFalse(location.item_rule(progression))
                self.assertFalse(location.item_rule(useful))
                self.assertTrue(location.item_rule(filler))
                checked.add(row["source_table"])
        self.assertEqual({"TreasureBX", "TreasureCA"}, checked)
        row = next(r for r in self.world.active_locations
                   if r["world"] == "San Fransokyo" and r["type"] != "treasure")
        self.assertTrue(self.multiworld.get_location(row["name"], self.player).item_rule(progression))
        self.assertFalse(any("keyblade_chest_keyblade" in r for r in self.world.active_locations))
