from __future__ import annotations

from ..client_support.poptracker import (
    build_context_payload,
    build_emblem_payload,
    build_emblem_sync_messages,
    emblem_storage_key,
)
from .test_helpers import (
    KH3BridgeTestCase,
)


class TestKH3BridgeSessionAutoAck(KH3BridgeTestCase):
    def test_poptracker_payload_formats_native_map_fields_and_route(self) -> None:
        payload = build_context_payload({
            "schema": "kh3ap.runtime_status.v1",
            "map": {"asset_path": "/Game/Levels/tt/tt_01/umap/tt_01_enemy_06",
                    "object_name": "tt_01_enemy_06", "path_kind": "Levels", "world_code": "tt",
                    "level_id": "tt_01", "room_id": "tt_01_enemy_06"},
            "context": {"hook": "BP_TresGetVictoryBonus", "opened_at": "2026-04-29T16:04:58Z"},
        }, slot=3, slot_name="PlayerKH", seed_name="UnitSeed")
        self.assertEqual(payload["kh3_tracker_event"], "map_changed")
        self.assertEqual(payload["kh3_level_path"], "/Game/Levels/tt/tt_01/umap/tt_01_enemy_06")
        self.assertEqual(payload["kh3_object_name"], "tt_01_enemy_06")
        self.assertEqual(payload["kh3_world_name"], "Twilight Town")
        self.assertEqual(payload["slot"], 3)
        self.assertEqual(payload["slot_name"], "PlayerKH")
        self.assertEqual(payload["seed_name"], "UnitSeed")

    def test_poptracker_rejects_raw_context_instead_of_interpreting_it(self) -> None:
        self.assertIsNone(build_context_payload({"world": "World /Game/Levels/tt/tt_01/tt_01.tt_01"}))

    def test_poptracker_lucky_emblem_payload_normalizes_exact_mark_ids(self) -> None:
        payload = build_emblem_payload(
            {
                "sequence": 7,
                "captured_global_ids": [13, "14", 14, 0, 91, True],
                "latest_capture_global_id": 15,
                "runtime_context": "World /Game/Levels/tt/tt_01/tt_01",
                "observed_at_utc": "2026-09-02T21:00:00Z",
            },
            slot=3,
            slot_name="PlayerKH",
            seed_name="UnitSeed",
            catalog={
                13: {
                    "world": "Twilight Town",
                    "world_index": 1,
                    "friendly_name": "Twilight Town Lucky Emblem 1 - Tram Common Wall",
                },
                14: {
                    "world": "Twilight Town",
                    "world_index": 2,
                    "friendly_name": "Twilight Town Lucky Emblem 2 - Tram Roof",
                },
                15: {
                    "world": "Twilight Town",
                    "world_index": 3,
                    "friendly_name": "Twilight Town Lucky Emblem 3 - Moogle Shop Chimney",
                },
            },
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["kh3_tracker_event"], "lucky_emblem_state")
        self.assertEqual(payload["kh3_lucky_emblem_sequence"], 7)
        self.assertEqual(payload["kh3_lucky_emblem_global_ids"], [13, 14, 15])
        self.assertEqual(payload["kh3_lucky_emblem_latest_global_id"], 15)
        self.assertEqual(
            payload["kh3_lucky_emblem_spots"][-1]["friendly_name"],
            "Twilight Town Lucky Emblem 3 - Moogle Shop Chimney",
        )
        self.assertEqual(payload["slot"], 3)
        self.assertEqual(payload["slot_name"], "PlayerKH")
        self.assertEqual(payload["seed_name"], "UnitSeed")

    def test_poptracker_uses_native_ids_for_actual_world_and_spot_labels(self) -> None:
        payload = build_emblem_payload({"captured_global_ids": [13, 31, 61, 78, 79, 87],
            "latest_capture_global_id": 79}, slot=1)
        spots = {s["global_id"]: s for s in payload["kh3_lucky_emblem_spots"]}
        self.assertEqual((spots[13]["world"], spots[13]["world_index"]), ("Toy Box", 1))
        self.assertEqual((spots[31]["world"], spots[31]["world_index"]), ("The Caribbean", 1))
        self.assertEqual((spots[61]["world"], spots[61]["world_index"]), ("Arendelle", 2))
        self.assertEqual((spots[78]["world"], spots[78]["world_index"]), ("San Fransokyo", 1))
        self.assertEqual((spots[79]["world"], spots[79]["world_index"]), ("Twilight Town", 1))
        self.assertNotIn("native_id", spots[79])
        self.assertNotIn("catalog_global_id", spots[79])
        self.assertEqual(spots[87]["friendly_name"], "Twilight Town Lucky Emblem 9 - Mansion Outer Wall")

    def test_poptracker_native_catalog_covers_all_ninety_without_aliases(self) -> None:
        payload = build_emblem_payload({"captured_global_ids": list(range(1, 91))}, slot=1)
        spots = payload["kh3_lucky_emblem_spots"]
        self.assertEqual({s["global_id"] for s in spots}, set(range(1, 91)))
        self.assertEqual(len({(s["world"], s["world_index"]) for s in spots}), 90)

    def test_poptracker_lucky_emblem_sync_is_persistent_and_live(self) -> None:
        payload = {
            "kh3_tracker_event": "lucky_emblem_state",
            "kh3_lucky_emblem_global_ids": [1, 13, 90],
        }

        messages = build_emblem_sync_messages(
            payload,
            team=2,
            slot=3,
        )

        self.assertEqual(emblem_storage_key(2, 3), "kh3_lucky_emblems_2_3")
        self.assertEqual(messages[0], {
            "cmd": "Set",
            "key": "kh3_lucky_emblems_2_3",
            "default": {},
            "want_reply": False,
            "operations": [{
                "operation": "update",
                "value": {"1": True, "13": True, "90": True},
            }],
        })
        self.assertEqual(messages[1], {
            "cmd": "Bounce",
            "slots": [3],
            "data": payload,
        })

    def test_poptracker_lucky_emblem_payload_ignores_empty_state(self) -> None:
        self.assertIsNone(build_emblem_payload(
            {
                "captured_global_ids": [0, 91, "bad"],
                "latest_capture_global_id": None,
            },
            slot=1,
        ))
