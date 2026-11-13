from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..bridge_support.core_session import CoreSession
from ..bridge_support.deathlink import DeathLinkService
from ..bridge_support.files import load_optional_json_file
from ..bridge_support.game_state import GameStateService
from ..bridge_support.location_index import LocationIndex
from ..bridge_support.models import (
    PayloadValidationError,
    ReconciliationDecision,
    ReconciliationOutcome,
    parse_game_state_payload,
    serialize_game_state_payload,
)
from ..bridge_support.paths import (
    BRIDGE_NOTIFICATIONS_FILE,
    GAME_STATE_FILE,
    RUNTIME_AP_MESSAGE_FILE,
    RUNTIME_CONTEXT_FILE,
    RUNTIME_ITEM_GRANT_FILE,
    SHARED_SIDECAR_FILENAMES,
    SLOT_DATA_FILE,
    bridge_sidecar_path,
)
from ..bridge_support.status import BridgeStatusService, BridgeStatusSnapshot
from ..bridge_support.store import BridgeStore
from ..bridge_support.time import format_utc_datetime, parse_utc_datetime, utc_now_text
from ..BridgeSession import KH3BridgeSession
from .test_helpers import DeterministicClock, KH3BridgeTestCase


class TestBridgeFoundations(unittest.TestCase):
    def test_method_lookup_and_pure_helpers_do_not_load_seed_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir) / "missing-bridge"
            session = KH3BridgeSession(bridge_dir)
            with patch.object(session._core, "initialize", side_effect=AssertionError("Unexpected seed read")):
                # Callback registration must be safe even before slot_data exists.
                for name in dir(KH3BridgeSession):
                    if not name.startswith("_") and callable(getattr(KH3BridgeSession, name)):
                        self.assertTrue(callable(getattr(session, name)), name)
                self.assertEqual(session.display_location_name({}, "Unknown"), "Unknown")
                payload = {"event_key": "ack:1", "indices": [1, 2], "action_kind": "item"}
                self.assertEqual(session.runtime_item_apply_event_key(payload), "ack:1")
                self.assertEqual(session.runtime_item_apply_event_indices(payload), [1, 2])
                self.assertEqual(session.infer_runtime_item_apply_action_kind(payload), "item")
            self.assertFalse(session._initialized)
            self.assertFalse(bridge_dir.exists())

    def test_metadata_access_loads_once_and_retries_after_missing_seed(self) -> None:
        location = {
            "bridge_key": "TreasureTT:TT_SBOX_001",
            "ap_location_name": "Twilight Town Chest",
            "ap_location_id": 12345,
            "source_table": "TreasureTT",
            "source_id": "TT_SBOX_001",
            "world": "Twilight Town",
        }
        slot_data = {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "bridge": {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "locations": [location],
            },
        }
        operations = {
            "slot_data": lambda session: session.slot_data,
            "list_locations": lambda session: session.list_locations(),
            "resolve_location": lambda session: session.resolve_location(location["bridge_key"]),
            "resolve_source_id": lambda session: session.resolve_source_id("TreasureTT", "TT_SBOX_001"),
            "resolve_ap_location_id": lambda session: session.resolve_ap_location_id(12345),
        }
        for name, access in operations.items():
            with self.subTest(access=name), TemporaryDirectory() as temp_dir:
                bridge_dir = Path(temp_dir)
                session = KH3BridgeSession(bridge_dir)
                with self.assertRaises(FileNotFoundError):
                    access(session)
                self.assertFalse(session._initialized)
                self.assertFalse(session._initializing)
                (bridge_dir / SLOT_DATA_FILE).write_text(json.dumps(slot_data), encoding="utf-8")
                with patch.object(session.store, "read_json", wraps=session.store.read_json) as read:
                    access(session)
                    self.assertEqual(session.slot_data, slot_data)
                    self.assertEqual(session.list_locations(), [location])
                    self.assertEqual(session.resolve_location(location["bridge_key"]), location)
                    self.assertIs(session.initialize(), session)
                    read.assert_called_once_with(SLOT_DATA_FILE)
                self.assertEqual([path.name for path in bridge_dir.iterdir()], [SLOT_DATA_FILE])

    def test_bridge_session_service_construction_is_side_effect_free(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            session = KH3BridgeSession(bridge_dir)
            self.assertIsInstance(session._deathlink, DeathLinkService)
            self.assertEqual(list(bridge_dir.iterdir()), [])

    def test_bridge_store_preserves_json_jsonl_signature_and_delete_behavior(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = BridgeStore(Path(temp_dir))
            payload = {"protocol_name": BRIDGE_PROTOCOL_NAME, "items": [{"index": 2}]}

            self.assertIsNone(store.read_optional_json("state.json"))
            self.assertEqual(store.signature("state.json"), ("state.json", None, None))

            store.write_json("state.json", payload)
            self.assertEqual(store.read_json("state.json"), payload)
            self.assertFalse(any(path.suffix == ".tmp" for path in Path(temp_dir).iterdir()))
            self.assertIs(store.path("state.json"), store.path("state.json"))
            signature = store.signature("state.json")
            self.assertEqual(signature[0], "state.json")
            self.assertIsInstance(signature[1], int)
            self.assertGreater(signature[2], 0)

            store.append_jsonl("events.jsonl", {"seq": 1, "event": "first"})
            store.append_jsonl("events.jsonl", {"seq": 2, "event": "second"})
            self.assertEqual(
                [json.loads(line) for line in store.path("events.jsonl").read_text(encoding="utf-8").splitlines()],
                [{"event": "first", "seq": 1}, {"event": "second", "seq": 2}],
            )

            self.assertTrue(store.delete("state.json"))
            self.assertFalse(store.delete("state.json"))
            with self.assertRaises(ValueError):
                store.delete("../outside.json")

    def test_bridge_store_protocol_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            store = BridgeStore(bridge_dir)

            self.assertEqual(store.protocol_metadata(), {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
            })

    def test_bridge_session_construction_has_no_filesystem_side_effects(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [],
                },
            }
            (bridge_dir / SLOT_DATA_FILE).write_text(json.dumps(slot_data), encoding="utf-8")
            before = {path.name: path.read_bytes() for path in bridge_dir.iterdir()}

            session = KH3BridgeSession(bridge_dir)

            self.assertEqual({path.name: path.read_bytes() for path in bridge_dir.iterdir()}, before)
            self.assertFalse(session._initialized)

            self.assertEqual(session.list_locations(), [])
            self.assertTrue(session._initialized)
            self.assertFalse((bridge_dir / "item_delivery.json").exists())
            self.assertFalse((bridge_dir / "item_consumer_plan.json").exists())

    def test_explicit_initialization_preserves_disabled_startup_write_semantics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [],
                },
            }
            (bridge_dir / SLOT_DATA_FILE).write_text(json.dumps(slot_data), encoding="utf-8")
            session = KH3BridgeSession(
                bridge_dir,

                defer_initialization=True,
            )

            self.assertIs(session.initialize(), session)
            self.assertIs(session.initialize(), session)
            self.assertEqual(session.list_locations(), [])
            self.assertFalse((bridge_dir / "item_delivery.json").exists())

    def test_core_session_composition_preserves_facade_location_and_state_parity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            location = {
                "bridge_key": "TreasureTT:TT_SBOX_001",
                "ap_location_name": "Twilight Town - Tram Common Chest",
                "ap_location_id": 12345,
                "source_table": "TreasureTT",
                "source_id": "TT_SBOX_001",
                "world": "Twilight Town",
                "location_description": "Tram Common Chest",
            }
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [location],
                },
            }
            (bridge_dir / SLOT_DATA_FILE).write_text(json.dumps(slot_data), encoding="utf-8")
            session = KH3BridgeSession(
                bridge_dir,

                defer_initialization=True,
            )

            self.assertIsInstance(session._core, CoreSession)
            self.assertIsInstance(session._core.game_state, GameStateService)
            self.assertIsInstance(session._core.status, BridgeStatusService)
            self.assertIsNone(session._core.locations)
            self.assertEqual([path.name for path in bridge_dir.iterdir()], [SLOT_DATA_FILE])

            session.initialize()

            self.assertIsInstance(session._core.locations, LocationIndex)
            self.assertEqual(session.resolve_location(location["bridge_key"]), location)
            self.assertEqual(session.resolve_location(location["ap_location_name"].upper()), location)
            self.assertEqual(session.resolve_source_id("TreasureTT", "TT_SBOX_001"), location)
            self.assertEqual(session.resolve_ap_location_id(12345), location)
            self.assertEqual(session.list_locations("twilight town"), [location])
            self.assertEqual(session.display_location_name(location), "Tram Common Chest")
            self.assertEqual(session.add_checks([location["ap_location_name"]]), 1)
            self.assertEqual(session.load_game_state()["checked_locations"], [location["bridge_key"]])
            self.assertEqual(session.load_ap_items_payload(), {"items": []})
            self.assertEqual(session.load_ap_status_payload(), {})
            status_snapshot = session._core.status.build_snapshot(session)
            self.assertIsInstance(status_snapshot, BridgeStatusSnapshot)
            self.assertEqual(status_snapshot.to_payload(), session.build_status())

    def test_bridge_session_package_import_uses_shared_services(self) -> None:
        from .. import BridgeSession as module

        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [],
                },
            }
            (bridge_dir / SLOT_DATA_FILE).write_text(json.dumps(slot_data), encoding="utf-8")
            session = module.KH3BridgeSession(
                bridge_dir,

                defer_initialization=True,
            )

            self.assertEqual(session.list_locations(), [])

    def test_payload_parsers_accept_legacy_and_current_shapes_without_rewriting(self) -> None:
        legacy = {
            "protocol_name": BRIDGE_PROTOCOL_NAME,
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "seq": 2,
            "checked_locations": ["TreasureTT:TT_SBOX_001"],
            "goal_complete": False,
            "legacy_extension": {"kept": True},
        }
        current = {
            **legacy,
            "last_newly_reported_keys": ["TreasureTT:TT_SBOX_001"],
            "last_newly_reported_at_utc": "2026-07-09T12:00:00+00:00",
        }

        self.assertEqual(serialize_game_state_payload(parse_game_state_payload(legacy)), legacy)
        self.assertEqual(serialize_game_state_payload(parse_game_state_payload(current)), current)

    def test_payload_parsers_reject_invalid_boundary_data(self) -> None:
        invalid_payloads = (
            [],
            {"seq": True},
            {"checked_locations": "TreasureTT:TT_SBOX_001"},
            {"checked_locations": ["TreasureTT:TT_SBOX_001", 2]},
            {"goal_complete": 1},
            {"last_newly_reported_at_utc": 123},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(PayloadValidationError):
                    parse_game_state_payload(payload)

    def test_unchanged_reconciliation_decision_reports_no_change(self) -> None:
        unchanged = ReconciliationDecision(
            outcome=ReconciliationOutcome.UNCHANGED,
            newly_reported_keys=(),
            reported_check_count=1,
            observed_check_count=1,
            goal_complete=False,
        )
        self.assertFalse(unchanged.changed)

    def test_reconciliation_decision_preserves_public_serialized_shape(self) -> None:
        decision = ReconciliationDecision(
            outcome=ReconciliationOutcome.CHANGED,
            newly_reported_keys=("LevelUp:2",),
            reported_check_count=2,
            observed_check_count=1,
            goal_complete=True,
        )

        self.assertEqual(decision.to_payload(), {
            "changed": True,
            "newly_reported_keys": ["LevelUp:2"],
            "reported_check_count": 2,
            "observed_check_count": 1,
            "goal_complete": True,
        })

    def test_shared_sidecar_catalog_preserves_filenames_and_path_behavior(self) -> None:
        self.assertEqual(SLOT_DATA_FILE, "slot_data.json")
        self.assertEqual(GAME_STATE_FILE, "game_state.json")
        self.assertEqual(RUNTIME_CONTEXT_FILE, "runtime_context.json")
        self.assertEqual(RUNTIME_ITEM_GRANT_FILE, "runtime_item_grant.json")
        self.assertEqual(RUNTIME_AP_MESSAGE_FILE, "runtime_ap_message.json")
        self.assertEqual(BRIDGE_NOTIFICATIONS_FILE, "bridge_notifications.json")
        self.assertEqual(SHARED_SIDECAR_FILENAMES, {
            "runtime_item_descriptions.json",
            "runtime_status.json", "runtime_secret_report_hints.json",
            "runtime_notification_event.json", "runtime_check_state.json",
            "ap_items.json",
            "ap_status.json",
            "bridge_notifications.json",
            "bridge_runtime_status.json",
            "game_state.json",
            "item_delivery.json",
            "item_delivery_ledger.json",
            "item_delivery_ledger_summary.json", "item_consumer_plan.json", "runtime_receipt_ack_event.json",
            "item_delivery_state.json",
            "lucky_mark_native_ap_resolver_diagnostics.jsonl",
            "runtime_ap_message.json",
            "runtime_blocking_ui_state.json",
            "runtime_classic_kingdom_state.json",
            "runtime_ingredient_state.json",
            "runtime_adversary_state.json",
            "runtime_minigame_rewards.json",
            "runtime_workshop.json",
            "runtime_olympus_coliseum_state.json",
            "runtime_context.json",
            "runtime_current_save_slot.json",
            "runtime_death_retry_replay_window.json",
            "runtime_deathlink_state.json",
            "runtime_trap_queue_request.json",
            "runtime_trap_queue_ack.json",
            "runtime_trap_queue_state.json",
            "runtime_reconciliation_state.json", "runtime_receipt_catalog.json", "runtime_save_receipts.json",
            "runtime_death_retry_baseline.json", "runtime_display_save_slot.json",
            "runtime_grant_cpp_profile.jsonl",
            "runtime_grant_profile.jsonl",
            "runtime_item_apply_event.json",
            "runtime_item_apply_state.json",
            "runtime_item_grant.json",
            "runtime_item_grant.priority.json",
            "runtime_item_grant_state.json",
            "runtime_item_latency_trace.jsonl",
            "runtime_loot_event.json",
            "runtime_map_ready.json",
            "runtime_keyblade_chests.json",
            "runtime_mom_clear_state.json",
            "runtime_music_chests.json",
            "runtime_sora_state.json",
            "slot_data.json",
            "runtime_save_receipts.json",
            "runtime_space_checks.json",
            "runtime_vbonus_checks.json",
            "vbonus_progress.json",
            "event_progress.json",
            "chest_progress.json",
            "runtime_event_checks.json",
            "runtime_chest_checks.json",
            "runtime_lucky_mark_checks.json",
            "runtime_item_grant_readiness.json",
        })

        relative_bridge_dir = Path("relative") / "bridge"
        self.assertEqual(
            bridge_sidecar_path(relative_bridge_dir, RUNTIME_ITEM_GRANT_FILE),
            relative_bridge_dir / "runtime_item_grant.json",
        )

    def test_utc_parser_accepts_bridge_formats_and_normalizes_offsets(self) -> None:
        expected = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_utc_datetime("2026-05-05T12:00:00Z"), expected)
        self.assertEqual(parse_utc_datetime(" 2026-05-05T12:00:00 "), expected)
        self.assertEqual(parse_utc_datetime("2026-05-05T07:00:00-05:00"), expected)
        self.assertIsNone(parse_utc_datetime("not-a-timestamp"))
        self.assertIsNone(parse_utc_datetime(None))

    def test_utc_formatting_and_clock_injection_are_deterministic(self) -> None:
        clock = DeterministicClock()
        central = timezone(timedelta(hours=-6))
        self.assertEqual(utc_now_text(clock), clock.iso())
        self.assertEqual(
            format_utc_datetime(datetime(2026, 5, 5, 6, 0, tzinfo=central)),
            clock.iso(),
        )

    def test_bridge_session_uses_injected_clock_for_persisted_timestamps(self) -> None:
        clock = DeterministicClock()
        explicit_now_utc = clock.at(hours=2)

        class NaiveExplicitNow(datetime):
            def astimezone(self, tz=None):
                return explicit_now_utc

        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            payload = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [],
                },
            }
            (bridge_dir / SLOT_DATA_FILE).write_text(json.dumps(payload), encoding="utf-8")
            session = KH3BridgeSession(
                bridge_dir,

                clock=clock,
            )

            session.mark_goal_complete_once(reason="clock-test")
            notification = session.append_bridge_notification(event="test", message="clock-test")
            self.assertEqual(session.load_game_state()["last_goal_complete_at_utc"], clock.iso())
            self.assertEqual(notification["created_at_utc"], clock.iso())


class TestKH3BridgeSessionAutoAck(KH3BridgeTestCase):

    def test_optional_bridge_json_empty_file_is_treated_as_absent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime_item_grant_state.json"
            path.write_text("", encoding="utf-8")

            self.assertIsNone(load_optional_json_file(path))

    def test_mark_goal_complete_once_is_idempotent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_minimal_slot_data(bridge_dir)
            session = KH3BridgeSession(bridge_dir)
            runtime_context = {
                "hook": "RuntimePlayablePawnContext",
                "world": "World /Game/Levels/ss/ss_50.ss_50",
                "outer": "Package /Game/Levels/ss/ss_50",
            }

            first_result = session.mark_goal_complete_once(
                reason="runtime_context:ss_50",
                runtime_context=runtime_context,
            )
            second_result = session.mark_goal_complete_once(
                reason="runtime_context:ss_50",
                runtime_context=runtime_context,
            )

            game_state = session.load_game_state()
            self.assertEqual(first_result["status"], "marked_complete")
            self.assertEqual(second_result["status"], "already_complete")
            self.assertEqual(first_result["seq"], 1)
            self.assertEqual(second_result["seq"], 1)
            self.assertTrue(game_state["goal_complete"])
            self.assertEqual(game_state["seq"], 1)
            self.assertEqual(game_state["last_goal_complete_reason"], "runtime_context:ss_50")
            self.assertEqual(game_state["last_goal_complete_runtime_context"], runtime_context)

    def test_sync_snapshot_preserves_existing_goal_complete(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_minimal_slot_data(bridge_dir)
            session = KH3BridgeSession(bridge_dir)
            session.mark_goal_complete_once(reason="runtime_context:ss_50")

            changed = session.sync_snapshot([], False)

            game_state = session.load_game_state()
            self.assertFalse(changed)
            self.assertTrue(game_state["goal_complete"])
            self.assertEqual(game_state["seq"], 1)


    def test_snapshot_reconcile_new_foreign_vbonus_leaves_presentation_to_native(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            slot_data = {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "bridge_key": "VBonus:Vbonus_014",
                            "source_table": "VBonus",
                            "source_id": "Vbonus_014",
                            "ap_location_name": "Twilight Town - Victory Bonus 014",
                            "ap_location_id": 3014,
                            "world": "Twilight Town",
                            "assigned_item_name": "Missile Tank",
                            "assigned_item_player": 2,
                            "assigned_item_player_name": "Aes",
                            "assigned_item_game": "Metroid Fusion",
                            "assigned_item_classification": 1,
                            "is_foreign_item": True,
                        }
                    ],
                },
            }

            self._write_json(bridge_dir / "slot_data.json", slot_data)

            session = KH3BridgeSession(bridge_dir)
            result = session.reconcile_snapshot(["VBonus:Vbonus_014"], False)

            self.assertEqual(result["newly_reported_keys"], ["VBonus:Vbonus_014"])
            self.assertFalse((bridge_dir / "bridge_notifications.json").exists())
            self.assertFalse((bridge_dir / "runtime_ap_message.json").exists())

    def test_snapshot_reconcile_new_foreign_level_up_leaves_presentation_to_native(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_json(bridge_dir / "slot_data.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "bridge_key": "LevelUp:4",
                            "source_table": "LevelUp",
                            "source_id": "4",
                            "ap_location_name": "Level Ups - Sora Level 4",
                            "ap_location_id": 2004,
                            "world": "Level Ups",
                            "assigned_item_name": "Missile Tank",
                            "assigned_item_player": 2,
                            "assigned_item_player_name": "Aes",
                            "assigned_item_game": "Metroid Fusion",
                            "assigned_item_classification": 1,
                            "is_foreign_item": True,
                        }
                    ],
                },
            })

            result = KH3BridgeSession(bridge_dir).reconcile_snapshot(["LevelUp:4"], False)

            self.assertEqual(result["newly_reported_keys"], ["LevelUp:4"])
            self.assertFalse((bridge_dir / "bridge_notifications.json").exists())
            self.assertFalse((bridge_dir / "runtime_ap_message.json").exists())
