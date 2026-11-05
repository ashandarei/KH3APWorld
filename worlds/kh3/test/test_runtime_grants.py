from __future__ import annotations

from worlds.kh3.test.runtime_sources import native_project_root

from .runtime_sources import cpp_function_body, read_vbonus_sources

import asyncio
import json
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from .. import RuntimeProfiler as runtime_profiler
from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION
from ..bridge_support.observability import RuntimeGrantProfileRecord
from ..bridge_support.diagnostic_history import history_for
from ..BridgeSession import (
    KH3BridgeSession,
)
from ..Client import KH3Context
from ..client_support.seed_paks import infer_kh3_ap_slot_name_from_pak_name
from ..client_support.seed_policy_service import InstalledPakDecision
from ..ExternalBridge import SaveFilesBackend
from .test_helpers import (
    KH3BridgeTestCase,
    KH3RuntimeGrantTestCase,
    apply_native_item_event,
    build_ap_item_ledger,
    build_runtime_grant_acknowledgement,
    build_slot_data,
    temporary_bridge_directory,
)


class TestKH3GrantMetadata(KH3BridgeTestCase):
    def test_link_heartbinders_have_inventory_tokens_in_generation_data(self) -> None:
        from ..Data import classify_runtime_grant_method, resolve_item_runtime_token
        for name, token in (("Dream Heartbinder", "KEY_ITEM06"), ("Pixel Heartbinder", "KEY_ITEM07"),
                            ("'Ohana Heartbinder", "KEY_ITEM08"), ("Pride Heartbinder", "KEY_ITEM09"),
                            ("Ocean Heartbinder", "KEY_ITEM10")):
            self.assertEqual(resolve_item_runtime_token(name), token)
            self.assertEqual(classify_runtime_grant_method(token), "bp_tres_get_item")

    def test_python_profiler_flushes_all_scopes_in_one_interval_batch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with runtime_profiler._profile_lock:
                runtime_profiler._profile_buckets.clear()
                runtime_profiler._profile_next_flush_at = None
            runtime_profiler.configure_python_profiler(Path(temp_dir))

            with (
                patch.object(runtime_profiler, "ENABLE_KH3_PYTHON_PROFILING", True),
                patch.object(runtime_profiler, "KH3_PYTHON_PROFILE_INTERVAL_SECONDS", 1.0),
                patch.object(runtime_profiler.time, "monotonic", side_effect=[1.0, 1.1, 2.1]),
            ):
                runtime_profiler.record_python_profile_sample("scope:a", 2.0)
                runtime_profiler.record_python_profile_sample("scope:b", 3.0)
                runtime_profiler.record_python_profile_sample("scope:a", 4.0)

            rows = (Path(temp_dir) / "python_profile.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 3)
            self.assertIn("scope:a,2,3.000,4.000,4.000", rows[1])
            self.assertIn("scope:b,1,3.000,3.000,3.000", rows[2])

    def test_runtime_grant_profile_record_preserves_envelope_and_is_frozen(self) -> None:
        record = RuntimeGrantProfileRecord(
            protocol_name=BRIDGE_PROTOCOL_NAME,
            protocol_version=BRIDGE_PROTOCOL_VERSION,
            event="runtime_grant_replay_source_snapshot",
            bridge_dir="bridge",
            fields={"source": "poll"},
        )

        self.assertEqual(
            record.to_payload(profile_seq=3, recorded_at_utc="2026-07-11T12:00:00+00:00"),
            {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "event": "runtime_grant_replay_source_snapshot",
                "bridge_dir": "bridge",
                "source": "poll",
                "profile_seq": 3,
                "recorded_at_utc": "2026-07-11T12:00:00+00:00",
            },
        )
        self.assertNotIn("profile_seq", record.signature_payload())
        with self.assertRaises(FrozenInstanceError):
            record.event = "changed"
        with self.assertRaises(TypeError):
            record.fields["source"] = "changed"


    def test_apply_event_does_not_retire_a_different_runtime_request(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_minimal_slot_data(bridge_dir)
            session = KH3BridgeSession(bridge_dir)
            self._write_json(bridge_dir / "runtime_item_grant.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "request_id": "new-request",
                "action_kind": "runtime_item_grant_batch",
                "grants": [{"index": 5}],
            })

            retired = session.retire_acknowledged_runtime_item_grant_requests({
                "request_id": "old-request",
                "action_kind": "save_load_replay_batch",
                "status": "applied",
            })

            self.assertEqual(retired, [])
            self.assertTrue((bridge_dir / "runtime_item_grant.json").exists())


class TestKH3BridgeSessionAutoAck(KH3BridgeTestCase):
    def test_native_queue_partial_ack_keeps_submission_and_final_ack_retires_it(self) -> None:
        with temporary_bridge_directory() as root:
            self._write_json(root / "slot_data.json", build_slot_data())
            self._write_json(root / "ap_items.json", build_ap_item_ledger([
                {"index": i, "item_name": "Potion", "kh3_bridge_delivery_required": True} for i in range(3)
            ]))
            self._write_ready_runtime_context(root)
            session = KH3BridgeSession(root)
            session.initialize()
            items = session.load_ap_items_payload()["items"]
            session.store.write_json("runtime_item_grant.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME, "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "request_id": "explicit-test", "action_kind": "runtime_item_grant_batch",
                "grants": [{"index": int(item["index"])} for item in items],
            })
            request = json.loads((root / "runtime_item_grant.json").read_text())
            first = {**build_runtime_grant_acknowledgement(request_id=request["request_id"], applied_indices=[0]),
                     "request_complete": False}
            with (patch.object(session, "sync_native_receipt_catalog") as refresh,
                  patch.object(session, "runtime_item_apply_event_context_status", return_value={"ready": True})):
                result = apply_native_item_event(session, first)
                refresh.assert_not_called()
            self.assertEqual(result["newly_applied_indices"], [0])
            self.assertEqual(session.native_reconciliation_state()["active_receipts"]["through"], 0)
            self.assertEqual(json.loads((root / "runtime_item_grant.json").read_text())["request_id"], request["request_id"])
            last = {**build_runtime_grant_acknowledgement(request_id=request["request_id"], applied_indices=[1, 2]),
                    "request_complete": True}
            with patch.object(session, "runtime_item_apply_event_context_status", return_value={"ready": True}):
                result = apply_native_item_event(session, last)
            self.assertEqual(result["newly_applied_indices"], [1, 2])
            self.assertEqual(session.list_pending_items(), [])
            self.assertFalse((root / "runtime_item_grant.json").exists())
            self.assertEqual(apply_native_item_event(session, last)["status"], "duplicate_event")


    def test_runtime_grant_profile_dedupes_repeated_signatures_per_session(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_minimal_slot_data(bridge_dir)

            session = KH3BridgeSession(bridge_dir)
            for _ in range(2):
                session.append_runtime_grant_profile_event(
                    "runtime_item_apply_event_processed", request_id="grant-1", status="applied",
                )
                session.append_runtime_grant_profile_event(
                    "runtime_ap_message_result_consumed", request_id="message-1", status="information",
                )

            profile_lines = history_for(bridge_dir).lines("runtime_grant_profile.jsonl")
            self.assertEqual(2, len(profile_lines))
            self.assertIn('"event":"runtime_item_apply_event_processed"', profile_lines[0])
            self.assertIn('"event":"runtime_ap_message_result_consumed"', profile_lines[1])


    def test_runtime_item_activity_wakes_backend_poll_wait(self) -> None:
        activity_event = threading.Event()
        backend = SaveFilesBackend(
            Path("synthetic-save-dir"),
            activity_event=activity_event,
        )
        activity_event.set()

        started_at = time.perf_counter()
        backend._wait_for_next_poll(1.0)

        self.assertLess(time.perf_counter() - started_at, 0.1)
        self.assertFalse(activity_event.is_set())

class TestKH3RuntimeItemGrantPayload(KH3RuntimeGrantTestCase):
    def test_runtime_grant_ack_uses_transient_direct_write(self) -> None:
        io_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "bridge"
            / "transport/bridge_io_helpers.cpp"
        ).read_text(encoding="utf-8")

        apply_writers = io_source.split(
            "auto write_runtime_item_apply_event(", 1
        )[1].split(
            "auto write_manual_runtime_item_grant_result(", 1
        )[0]
        self.assertEqual(apply_writers.count("write_text_file("), 2)
        self.assertNotIn("write_text_file_atomic(", apply_writers)
        self.assertNotIn("write_text_file_atomic_verified(", apply_writers)

    def test_runtime_grant_arms_without_parsing_request_twice(self) -> None:
        poll_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "grants"
            / "delivery"
            / "item_grant_poll_helpers.cpp"
        ).read_text(encoding="utf-8")

        arm_branch = poll_source.split(
            "if (!deps.runtime_state.grant_state().item_grant_poll_state().is_armed())", 1
        )[1].split(
            "if (!deps.runtime_state.grant_state().item_grant_poll_state().ready_to_process", 1
        )[0]
        self.assertNotIn("read_runtime_item_grant_batch_request", arm_branch)
        self.assertNotIn("read_runtime_item_grant_request", arm_branch)

    def test_runtime_grant_map_gate_precedes_both_lanes_and_preserves_pending_requests(self) -> None:
        grants_dir = (
            native_project_root() / 'KH3APProbeCpp/kh3ap/features/grants'
        )
        poll_source = (grants_dir / "delivery" / "item_grant_poll_helpers.cpp").read_text(encoding="utf-8")
        context_source = (grants_dir / "delivery" / "item_delivery_context.cpp").read_text(encoding="utf-8")
        execution_source = (grants_dir / "delivery" / "item_delivery_execution.cpp").read_text(encoding="utf-8")
        flow_source = (grants_dir / "delivery" / "item_delivery_flow.cpp").read_text(encoding="utf-8")
        process = poll_source.split("auto process_pending_runtime_item_grant(", 1)[1]
        self.assertNotIn("const auto map_read_status =", process)
        self.assertIn("space::supports_inventory_grant(grant.grant_method, grant.runtime_token)", flow_source)
        self.assertIn("deps.runtime_state, policy.supports_space_inventory, &controller", context_source)
        batch = execution_source.split("auto process_batch_delivery(", 1)[1].split("auto process_single_delivery(", 1)[0]
        waiting = batch.split("if (!safety_status.ready)", 1)[1].split("if constexpr", 1)[0]
        self.assertIn("return;", waiting)
        self.assertNotIn("consume_request()", waiting)
        self.assertNotIn("write_runtime_item_apply", waiting)
        # Only Game Over can bypass active special battles and the named map exceptions.
        self.assertEqual(context_source.count("current_runtime_unsafe_map_status(deps.runtime_state, allow_special_battle_game_over)"), 1)
        self.assertIn("prepare_delivery_context(deps, policy, true, now)", execution_source)
        self.assertIn("prepare_delivery_context(deps, policy, false, now)", execution_source)
        safety = (grants_dir / "grant_safety.cpp").read_text(encoding="utf-8")
        self.assertIn('rule->id == STR("game_levels_dw_dw_21_dw_21")', safety)
        self.assertIn('rule->id == STR("game_levels_ca_ca_04_ca_04")', safety)
        self.assertIn("allow_excluded_map_game_over &&\n                      (runtime_state.grant_state().special_battle_mode_active() ||", safety)
        self.assertIn("current_runtime_unsafe_map_status(runtime_state, allow_excluded_map_game_over)", safety)

    def test_vbonus_runtime_grants_use_cached_direct_durable_routes(self) -> None:
        grants_dir = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "grants"
        )
        apply_source = (grants_dir / "delivery" / "item_grant_apply_helpers.cpp").read_text(encoding="utf-8")

        self.assertIn('request.grant_method == STR("durable_sora_ability")', apply_source)
        self.assertIn('request.grant_method == STR("durable_sora_vbonus")', apply_source)
        self.assertIn("try_call_durable_sora_ability_grant(", apply_source)
        self.assertIn("try_call_durable_sora_vbonus_grant(", apply_source)

        vbonus_source = read_vbonus_sources()
        self.assertIn("try_add_direct_durable_vbonus(", vbonus_source)
        self.assertIn("banks.add_vbonus_to_equipment(", vbonus_source)
        self.assertRegex(vbonus_source, r"banks\.add_vbonus\(\s*reinterpret_cast<void\*>\(banks\.staging_record\)")
        self.assertIn("display_helpers.resolve_magic_command(", vbonus_source)
        self.assertIn("display_helpers.refresh_magic_shortcuts(", vbonus_source)
        self.assertIn("s_vbonus_hud_magic_type", vbonus_source)

        # The dispatcher has already established that the world-context
        # pointer is its trusted cached Sora pawn, and the bank resolver proves
        # that pawn reaches the active persistent Sora record. Keep the cheap
        # UObject validity check in each durable route without rebuilding
        # UObject names and repeating the pawn catalog scan per grant.
        durable_vbonus_helper = cpp_function_body(vbonus_source, "try_call_durable_sora_vbonus_grant")
        durable_ability_helper = cpp_function_body(vbonus_source, "try_call_durable_sora_ability_grant")
        for durable_helper in (durable_vbonus_helper, durable_ability_helper):
            self.assertIn("kh3ap::is_uobject_valid_fast(sora_pawn)", durable_helper)
            self.assertNotIn("is_sora_ability_pawn_candidate", durable_helper)
        bank_resolver = cpp_function_body(vbonus_source, "try_resolve_vanilla_sora_ability_banks")
        self.assertIn("vanilla_vbonus_active_sora_record_identity_mismatch", bank_resolver)

        # Fixed storefront RVAs and their byte signatures are immutable for
        # the lifetime of the process. Keep those checks in process-local
        # caches; only the save, pawn, equipment, controller, and HUD object
        # pointers should be resolved and validated for each grant.
        self.assertIn("native_vbonus_bank_function_cache()", vbonus_source)
        self.assertIn("native_vbonus_hud_function_cache()", vbonus_source)
        self.assertIn("native_vbonus_stat_function_cache()", vbonus_source)
        self.assertIn("native_vbonus_magic_function_cache()", vbonus_source)
        egs_rvas = (
            "s_egs_add_vbonus_to_sora_pawn_rva = 0x75B0F0",
            "s_egs_save_accessor_active_data_tail_rva = 0x1877D04",
            "s_egs_add_vbonus_to_ability_table_rva = 0x1116B70",
            "s_egs_refresh_vbonus_character_record_rva = 0x1116390",
            "s_egs_resolve_vbonus_hud_owner_rva = 0x1718EE0",
            "s_egs_present_vbonus_hud_rva = 0x18DF840",
            "s_egs_resolve_vbonus_stat_display_amounts_rva = 0x7063A0",
            "s_egs_resolve_magic_display_command_rva = 0x114BEC0",
            "s_egs_resolve_magic_shortcut_manager_inline_rva = 0x1138C3E",
            "s_egs_refresh_magic_shortcuts_rva = 0x18F6F50",
        )
        for egs_rva in egs_rvas:
            self.assertIn(egs_rva, vbonus_source)
        egs_signatures = (
            "s_egs_add_vbonus_to_sora_pawn_signature",
            "s_egs_save_accessor_active_data_tail_signature",
            "s_egs_add_vbonus_to_ability_table_signature",
            "s_egs_refresh_vbonus_character_record_signature",
            "s_egs_resolve_vbonus_hud_owner_signature",
            "s_egs_present_vbonus_hud_signature",
            "s_egs_resolve_vbonus_stat_display_amounts_signature",
            "s_egs_resolve_magic_display_command_signature",
            "s_egs_resolve_magic_shortcut_manager_inline_signature",
            "s_egs_refresh_magic_shortcuts_signature",
        )
        for egs_signature in egs_signatures:
            self.assertGreaterEqual(vbonus_source.count(egs_signature), 3)
        self.assertIn("resolve_egs_vbonus_save_owner", vbonus_source)
        self.assertIn("active_save_data - s_active_save_data_offset", vbonus_source)
        self.assertIn("active_vbonus_target_is_sora_pawn", vbonus_source)
        self.assertIn("s_egs_add_vbonus_virtual_slot_offset", bank_resolver)
        self.assertIn("add_egs_vbonus_to_character_record", vbonus_source)
        self.assertIn("resolve_egs_vbonus_stat_display_amounts", vbonus_source)
        self.assertIn("resolve_egs_magic_shortcut_manager", vbonus_source)
        self.assertNotIn("PLH::x64Detour", vbonus_source)
        self.assertNotIn("native_signature_matches(", bank_resolver)
        hud_resolver = cpp_function_body(vbonus_source, "try_resolve_native_vbonus_hud_presenter")
        self.assertNotIn("native_signature_matches(", hud_resolver)

        startup_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "app"
            / "startup_service.cpp"
        ).read_text(encoding="utf-8")
        begin_index = startup_source.index(
            "config_.game_thread_driver.begin_unreal_init();"
        )
        enum_prewarm_index = startup_source.index(
            "maybe_initialize_runtime_enum_value_maps();"
        )
        diagnostics_index = startup_source.index(
            "initialize_runtime_startup_diagnostics("
        )
        self.assertLess(begin_index, enum_prewarm_index)
        self.assertLess(enum_prewarm_index, diagnostics_index)
        enum_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "app"
            / "enums.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("RuntimeEnumResolver::prewarm_reflected_values()", enum_source)
        self.assertIn("append_reflected_enum_values(", enum_source)
        self.assertIn('L"/Script/TresGame.ETresVictoryBonusKind"', enum_source)
        self.assertIn('L"/Script/TresGame.ETresAbilityKind"', enum_source)
        self.assertIn("values.insert_or_assign(", enum_source)

    def test_runtime_shards_use_the_normal_material_item_grant_path(self) -> None:
        grant_source = (
            native_project_root()
            / "KH3APProbeCpp"
            / "kh3ap"
            / "features"
            / "grants"
            / "delivery"
            / "item_grant_apply_helpers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn(
            's_ultima_weapon_shard_inventory_runtime_token = STR("MAT_ITEM57")',
            ' '.join(grant_source.split()),
        )
        self.assertRegex(grant_source, r"try_call_bp_tres_get_item\(\s*s_ultima_weapon_shard_inventory_runtime_token")
        self.assertIn("shard.awards_weapon()", grant_source)
        policy = (native_project_root() / "KH3APProbeCpp/kh3ap/features/grants/delivery/ultima_weapon_shard_policy.h").read_text()
        self.assertIn("return ordinal == required_count;", policy)
        self.assertIn("s_ultima_weapon_runtime_token", grant_source)
        self.assertNotIn("maybe_show_ultima_weapon_shard_information", grant_source)
        self.assertNotIn('STR("Ultima Weapon Shard {}/{}")', grant_source)

    def test_client_build_items_payload_leaves_runtime_reward_decoding_to_dll(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            manifest_payload = {
                "schema_version": 1,
                "seed_name": "unit-test-seed",
                "rows_by_item_name": {
                    "Ability: Thunder Boost": [
                        {
                            "row_name": "GIVESORA_THUNDER_UP_01",
                            "reward_kind": "ability",
                            "reward_display_names": ["Ability: Thunder Boost"],
                            "reward_tokens": ["ETresAbilityKind::THUNDER_UP"],
                        },
                        {
                            "row_name": "GIVESORA_THUNDER_UP_02",
                            "reward_kind": "ability",
                            "reward_display_names": ["Ability: Thunder Boost"],
                            "reward_tokens": ["ETresAbilityKind::THUNDER_UP"],
                        },
                    ],
                },
            }
            self._write_json(bridge_dir / "Kh3RuntimeRewardManifest-unit-test-seed.json", manifest_payload)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.game = "Kingdom Hearts III"
            ctx.items_received = [type("NetItem", (), {"item": 1, "location": 2001, "player": 2, "flags": 0})()]
            ctx.item_names = type("Lookup", (), {"lookup_in_game": staticmethod(lambda _item, _game: "Ability: Thunder Boost")})()
            ctx.location_names = type("Lookup", (), {"lookup_in_slot": staticmethod(lambda _loc, _game: "Link's Uncle")})()
            ctx.player_names = {2: "zelda"}
            ctx.location_id_to_bridge_key = {}

            payload = ctx.build_items_payload()
            item = payload["items"][0]

            self.assertEqual(item["item_name"], "Ability: Thunder Boost")
            self.assertEqual(item["item_id"], 1)
            self.assertFalse(any(key.startswith("kh3_") for key in item))

    def test_client_build_items_payload_keeps_seed_delivery_policy_in_slot_data(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.game = "Kingdom Hearts III"
            ctx.items_received = [
                type("NetItem", (), {"item": 1263022431, "location": 1263023266, "player": 1, "flags": 1})(),
            ]
            ctx.item_names = type(
                "Lookup",
                (),
                {"lookup_in_game": staticmethod(lambda _item, _game: "Ability: Pole Spin")},
            )()
            ctx.location_names = type(
                "Lookup",
                (),
                {"lookup_in_slot": staticmethod(lambda _loc, _game: "Lucky Emblems - Milestone 30")},
            )()
            ctx.player_names = {1: "GusxandiKH3"}
            ctx.location_id_to_bridge_key = {1263023266: "LuckyMark:30"}
            ctx.slot_data = {
                "kh3_seed": {"seed_name": "grant-test", "slot": 1},
                "bridge": {
                    "locations": [
                        {
                            "bridge_key": "LuckyMark:30",
                            "source_table": "LuckyMark",
                            "source_id": "30",
                            "type": "lucky_emblem",
                            "location_description": "Lucky Emblems - Milestone 30",
                            "bridge_delivery_required": True,
                        },
                    ],
                },
            }

            payload = ctx.build_items_payload()
            item = payload["items"][0]

            self.assertNotIn("kh3_bridge_delivery_required", item)
            self.assertEqual(item["bridge_key"], "LuckyMark:30")
            self.assertFalse(any(key.startswith("kh3_") for key in item))

    def test_client_build_items_payload_skips_duplicate_ap_source_location(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.game = "Kingdom Hearts III"
            ctx.items_received = [
                type("NetItem", (), {"item": 1263022402, "location": 27, "player": 1, "flags": 0})(),
                type("NetItem", (), {"item": 1263022402, "location": 27, "player": 1, "flags": 0})(),
                type("NetItem", (), {"item": 1263022430, "location": 28, "player": 1, "flags": 0})(),
            ]
            item_lookup = {
                1263022402: "Ether",
                1263022430: "Hi-Potion",
            }
            ctx.item_names = type("Lookup", (), {"lookup_in_game": staticmethod(lambda item, _game: item_lookup[item])})()
            ctx.location_names = type("Lookup", (), {"lookup_in_slot": staticmethod(lambda loc, _game: f"Location {loc}")})()
            ctx.player_names = {1: "Metroid Fusion"}
            ctx.location_id_to_bridge_key = {}

            payload = ctx.build_items_payload()

            self.assertEqual([item["index"] for item in payload["items"]], [0, 2])
            self.assertEqual([item["item_name"] for item in payload["items"]], ["Ether", "Hi-Potion"])

    def test_client_write_bridge_files_preserves_cached_items_until_snapshot_is_authoritative(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            cached_items = {
                "kh3_seed": {"seed_name": "grant-test", "slot": 1},
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "items": [
                    {
                        "index": 21,
                        "item_name": "CK: The Barnyard Battle",
                        "from_player": 1,
                        "from_player_name": "shado",
                        "bridge_key": "VBonus:Vbonus_016",
                    }
                ],
            }
            self._write_json(bridge_dir / "ap_items.json", cached_items)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.slot_data = {
                "kh3_seed": {"seed_name": "grant-test", "slot": 1},
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [],
                },
            }
            ctx.server = None
            ctx.server_address = None
            ctx.slot = None
            ctx.team = None
            ctx.player_names = {}
            ctx.auth = "shado"
            ctx.seed_name = None
            ctx.offline_seed_name = "seed"
            ctx.checked_locations = set()
            ctx.missing_locations = set()
            ctx.locations_checked = set()
            ctx.location_key_to_id = {}
            ctx.items_received = []
            ctx.kh3_received_items_snapshot_ready = False
            ctx.finished_game = False
            ctx.last_processed_game_state_seq = 0
            ctx.save_seed_mismatch_active = False
            seed_policy = Mock()
            seed_policy.installed_pak_decision.return_value = InstalledPakDecision(None, (), "-", True)
            ctx._get_seed_policy_service = lambda: seed_policy
            ctx.build_save_seed_status = lambda: "-"

            ctx.write_bridge_files()

            payload = json.loads((bridge_dir / "ap_items.json").read_text(encoding="utf-8"))
            status = json.loads((bridge_dir / "ap_status.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["items"], cached_items["items"])
            self.assertEqual(status["received_item_count"], 1)

            ctx.server = type(
                "ConnectedServer",
                (),
                {"socket": type("OpenSocket", (), {"closed": False})()},
            )()
            ctx.write_bridge_files()
            payload = json.loads((bridge_dir / "ap_items.json").read_text(encoding="utf-8"))
            status = json.loads((bridge_dir / "ap_status.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["items"], cached_items["items"])
            self.assertEqual(status["received_item_count"], 1)

            ctx.kh3_received_items_snapshot_ready = True
            ctx.write_bridge_files()
            payload = json.loads((bridge_dir / "ap_items.json").read_text(encoding="utf-8"))
            status = json.loads((bridge_dir / "ap_status.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["items"], [])
            self.assertEqual(status["received_item_count"], 0)

    def test_client_reset_bridge_observation_state_excludes_server_only_checks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_json(bridge_dir / "bridge_notifications.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 12,
                "notifications": [
                    {"seq": 10, "message": "old"},
                    {"seq": 12, "message": "older"},
                ],
            })
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.checked_locations = {1001}
            ctx.locations_checked = {1002}
            ctx.location_id_to_bridge_key = {
                1001: "TreasureTT:TT_LBOX_001",
                1002: "VBonus:Vbonus_014",
            }
            ctx.last_processed_game_state_seq = -1

            ctx.reset_bridge_observation_state(reason="connected_bridge_initialized")

            game_state = json.loads((bridge_dir / "game_state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                game_state["checked_locations"],
                ["VBonus:Vbonus_014"],
            )
            self.assertEqual(game_state["seq"], 0)
            self.assertEqual(ctx.last_processed_game_state_seq, -1)
            self.assertEqual(ctx.last_processed_bridge_notification_seq, 12)

    def test_client_bridge_notification_cursor_uses_highest_existing_notification_seq(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_json(bridge_dir / "bridge_notifications.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 1,
                "notifications": [
                    {"seq": 3, "message": "old high seq"},
                ],
            })
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.last_processed_bridge_notification_seq = 0

            ctx.mark_existing_bridge_notifications_processed()

            self.assertEqual(ctx.last_processed_bridge_notification_seq, 3)

    def test_client_connect_initialization_preserves_preconnect_bridge_checks(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_root = root / "bridge"
            seed_name = "AP_85269139745991038903"
            auth = "PlayerKH"
            bridge_dir = bridge_root / f"{seed_name}_{auth}"
            bridge_dir.mkdir(parents=True)
            self._write_json(bridge_dir / "game_state.json", {
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "seq": 3,
                "checked_locations": ["TreasureTT:TT_LBOX_001"],
                "goal_complete": False,
            })

            sent_messages: list[list[dict]] = []

            async def fake_send_msgs(messages: list[dict]) -> None:
                sent_messages.append(messages)

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_root_override = bridge_root
            ctx.bridge_dir = bridge_dir
            ctx.slot_data = {
                "kh3_seed": {"seed_name": "grant-test", "slot": 1},
                "protocol_name": BRIDGE_PROTOCOL_NAME,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "bridge": {
                    "protocol_name": BRIDGE_PROTOCOL_NAME,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "locations": [
                        {
                            "bridge_key": "TreasureTT:TT_LBOX_001",
                            "source_table": "TreasureTT",
                            "source_id": "TT_LBOX_001",
                            "ap_location_name": "Twilight Town - Large Chest 1",
                            "ap_location_id": 1001,
                            "world": "Twilight Town",
                            "type": "treasure",
                        }
                    ],
                },
            }
            ctx.seed_name = seed_name
            ctx.auth = auth
            ctx.player_names = {1: auth}
            ctx.slot = 1
            ctx.checked_locations = set()
            ctx.locations_checked = set()
            ctx.finished_game = False
            ctx.goal_status_sent = False
            ctx.kh3_check_reporting_enabled = True
            ctx.is_installed_pak_compatible = lambda: True
            ctx.refresh_save_seed_guard = lambda: True
            ctx.write_bridge_files = lambda: None
            ctx.send_msgs = fake_send_msgs

            ctx.initialize_bridge()

            game_state = json.loads((bridge_dir / "game_state.json").read_text(encoding="utf-8"))
            self.assertEqual(game_state["checked_locations"], ["TreasureTT:TT_LBOX_001"])
            self.assertEqual(ctx.locations_checked, {1001})
            self.assertEqual(ctx.last_processed_game_state_seq, -1)

            asyncio.run(ctx.handle_game_state_file())

            self.assertEqual(sent_messages, [[{"cmd": "LocationChecks", "locations": [1001]}]])
            self.assertEqual(ctx.last_processed_game_state_seq, 0)

    def test_infer_slot_name_from_kh3_ap_pak_name(self) -> None:
        slot_name = infer_kh3_ap_slot_name_from_pak_name(
            "pakchunk9-KH3-Rando-Boss-randomizer-AP_12822259376545329464-1_99_P.pak"
        )

        self.assertEqual(slot_name, "KH3-Rando-Boss")

    def test_infer_slot_name_from_kh3_ap_pak_name_preserves_underscores(self) -> None:
        slot_name = infer_kh3_ap_slot_name_from_pak_name(
            "pakchunk9-left_middle_right-randomizer-AP_44685372886330020568-1_99_P.pak"
        )

        self.assertEqual(slot_name, "left_middle_right")

    def test_infer_slot_name_ignores_old_kh3_ap_pak_name(self) -> None:
        slot_name = infer_kh3_ap_slot_name_from_pak_name(
            "pakchunk99-randomizer-AP_12822259376545329464-1-KH3-Rando-Boss_99_P.pak"
        )

        self.assertIsNone(slot_name)

    def test_client_infers_slot_name_from_cached_seed_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            bridge_dir = Path(temp_dir)
            self._write_json(bridge_dir / "kh3_ap_seed_metadata.json", {
                "format": "kh3_ap_seed_metadata",
                "slot_name": "KH3-Rando-Boss",
            })

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = Path(temp_dir) / "mods"
            ctx.expected_seed_id = lambda: None

            self.assertEqual(ctx.infer_slot_name_from_installed_pak(), "KH3-Rando-Boss")

    def test_client_prefers_installed_pak_filename_over_cached_seed_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge_dir = root / "bridge"
            mods_dir = root / "mods"
            bridge_dir.mkdir()
            mods_dir.mkdir()
            self._write_json(bridge_dir / "kh3_ap_seed_metadata.json", {
                "format": "kh3_ap_seed_metadata",
                "slot_name": "KH3-Rando-Boss",
            })
            (
                mods_dir
                / "pakchunk9-PlayerKH-randomizer-AP_71851026333167960055-1_99_P.pak"
            ).write_bytes(b"pak")

            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = bridge_dir
            ctx.kh3_mods_dir = mods_dir
            ctx.seed_name = "AP_71851026333167960055"
            ctx.offline_seed_name = None

            self.assertEqual(ctx.infer_slot_name_from_installed_pak(), "PlayerKH")

    def test_client_build_items_payload_does_not_add_vbonus_secondary_rewards_from_slot_data(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = Path(".")
        ctx.game = "Kingdom Hearts III"
        ctx.items_received = [
            type("NetItem", (), {"item": 1, "location": 2001, "player": 1, "flags": 0})(),
            type("NetItem", (), {"item": 2, "location": 2002, "player": 1, "flags": 0})(),
            type("NetItem", (), {"item": 3, "location": 2003, "player": 1, "flags": 0})(),
        ]
        ctx.item_names = type(
            "Lookup",
            (),
            {"lookup_in_game": staticmethod(lambda item, _game: {
                1: "Aegis Chain",
                2: "Guardian Belt",
                3: "Ability: Withstand Combo",
            }[item])},
        )()
        ctx.location_names = type(
            "Lookup",
            (),
            {"lookup_in_slot": staticmethod(lambda loc, _game: {
                2001: "Toy Box - Victory Bonus 017",
                2002: "Twilight Town - Victory Bonus 014",
                2003: "Olympus - Victory Bonus 082",
            }[loc])},
        )()
        ctx.player_names = {1: "shado"}
        ctx.location_id_to_bridge_key = {
            2001: "VBonus:Vbonus_017",
            2002: "VBonus:Vbonus_014",
            2003: "VBonus:Vbonus_082",
        }
        ctx.slot_data = {
            "kh3_seed": {"seed_name": "grant-test", "slot": 1},
            "bridge": {
                "locations": [
                    {
                        "bridge_key": "VBonus:Vbonus_014",
                        "source_table": "VBonus",
                        "source_id": "Vbonus_014",
                        "type": "vbonus",
                        "location_description": "Defeat Demon Tide in Twilight Town - The Neighborhood: Tram Common",
                        "vanilla_item_names": {
                            "Sora_Ability1": "Ability: MP Safety",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                            "Sora_Bonus1": "Magic: Cure",
                            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                        },
                    },
                    {
                        "bridge_key": "VBonus:Vbonus_017",
                        "source_table": "VBonus",
                        "source_id": "Vbonus_017",
                        "type": "vbonus",
                        "location_description": "Toy Box - Andy's House Heartless",
                        "vanilla_item_names": {
                            "Sora_Ability1": "Ability: Aerial Recovery",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                            "Sora_Bonus1": "Heart Piece",
                            "Sora_Bonus2": "ETresVictoryBonusKind::NONE",
                        },
                    },
                    {
                        "bridge_key": "VBonus:Vbonus_082",
                        "source_table": "VBonus",
                        "source_id": "Vbonus_082",
                        "type": "vbonus",
                        "location_description": "Defeat Darkside in The Final World",
                        "vanilla_item_names": {
                            "Sora_Ability1": "ETresAbilityKind::NONE",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                            "Sora_Bonus1": "Magic: Fire",
                            "Sora_Bonus2": "Magic: Water",
                        },
                    }
                ]
            }
        }

        payload = ctx.build_items_payload()

        self.assertNotIn("kh3_secondary_rewards", payload["items"][0])
        self.assertNotIn("kh3_secondary_rewards", payload["items"][1])
        self.assertNotIn("kh3_secondary_rewards", payload["items"][2])
        self.assertEqual(payload["items"][0]["location_name"], "Toy Box - Andy's House Heartless")
        self.assertEqual(
            payload["items"][1]["location_name"],
            "Defeat Demon Tide in Twilight Town - The Neighborhood: Tram Common",
        )
        self.assertEqual(payload["items"][2]["location_name"], "Defeat Darkside in The Final World")

    def test_client_build_items_payload_ignores_legacy_level_up_path_rewards(self) -> None:
        with TemporaryDirectory() as temp_dir:
            save_dir = Path(temp_dir)
            ctx = KH3Context.__new__(KH3Context)
            ctx.bridge_dir = Path(".")
            ctx.bridge_save_dir = save_dir
            ctx.game = "Kingdom Hearts III"
            ctx.items_received = [
                type("NetItem", (), {"item": 1, "location": 2001, "player": 1, "flags": 0})(),
                type("NetItem", (), {"item": 2, "location": 2002, "player": 1, "flags": 0})(),
                type("NetItem", (), {"item": 3, "location": 2014, "player": 1, "flags": 0})(),
                type("NetItem", (), {"item": 4, "location": 2006, "player": 1, "flags": 0})(),
            ]
            ctx.item_names = type(
                "Lookup",
                (),
                {"lookup_in_game": staticmethod(lambda item, _game: {
                    1: "Mage's Staff+",
                    2: "CK: How to Play Golf",
                    3: "Ability: Fire Boost",
                    4: "Magic Boost",
                }[item])},
            )()
            ctx.location_names = type(
                "Lookup",
                (),
                {"lookup_in_slot": staticmethod(lambda loc, _game: {
                    2001: "Level Ups - Sora Level 2",
                    2002: "Level Ups - Sora Level 3",
                    2006: "Level Ups - Sora Level 6",
                    2014: "Level Ups - Sora Level 14",
                }[loc])},
            )()
            ctx.player_names = {1: "testkh3"}
            ctx.location_id_to_bridge_key = {
                2001: "LevelUp:2",
                2002: "LevelUp:3",
                2006: "LevelUp:6",
                2014: "LevelUp:14",
            }
            ctx.slot_data = {
                "kh3_seed": {"seed_name": "grant-test", "slot": 1},
                "bridge": {
                    "locations": [
                        {
                            "bridge_key": "LevelUp:2",
                            "source_table": "LevelUp",
                            "source_id": "2",
                            "type": "level_up",
                            "vanilla_item_names": {
                                "TypeA": "Ability: Combo Plus",
                                "TypeB": "Ability: Magic Combo Thrift",
                                "TypeC": "Ability: Damage Control",
                            },
                        },
                        {
                            "bridge_key": "LevelUp:3",
                            "source_table": "LevelUp",
                            "source_id": "3",
                            "type": "level_up",
                            "vanilla_item_names": {
                                "TypeA": "Ability: Treasure Magnet",
                                "TypeB": "Ability: Treasure Magnet",
                                "TypeC": "Ability: Treasure Magnet",
                            },
                        },
                        {
                            "bridge_key": "LevelUp:6",
                            "source_table": "LevelUp",
                            "source_id": "6",
                            "type": "level_up",
                            "vanilla_item_names": {
                                "TypeA": "Ability: MP Haste",
                                "TypeB": "Ability: Combo Plus",
                                "TypeC": "Ability: Combo Plus",
                            },
                        },
                        {
                            "bridge_key": "LevelUp:14",
                            "source_table": "LevelUp",
                            "source_id": "14",
                            "type": "level_up",
                            "vanilla_item_names": {
                                "TypeA": "Ability: Fire Boost",
                                "TypeB": "Ability: Leaf Bracer",
                                "TypeC": "Ability: Air Combo Plus",
                            },
                        },
                    ]
                }
            }

            payload = ctx.build_items_payload()

            for item in payload["items"]:
                self.assertNotIn("kh3_secondary_rewards", item)
                self.assertNotIn("kh3_level_up_ability_path", item)

    def test_client_build_items_payload_uses_chest_location_description(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = Path(".")
        ctx.game = "Kingdom Hearts III"
        ctx.items_received = [
            type("NetItem", (), {"item": 1, "location": 2001, "player": 1, "flags": 0})(),
        ]
        ctx.item_names = type(
            "Lookup",
            (),
            {"lookup_in_game": staticmethod(lambda item, _game: "Potion")},
        )()
        ctx.location_names = type(
            "Lookup",
            (),
            {"lookup_in_slot": staticmethod(lambda loc, _game: "Twilight Town - Large Chest 1")},
        )()
        ctx.player_names = {1: "shado"}
        ctx.location_id_to_bridge_key = {2001: "TreasureTT:TT_LBOX_001"}
        ctx.slot_data = {
            "kh3_seed": {"seed_name": "grant-test", "slot": 1},
            "bridge": {
                "locations": [
                    {
                        "bridge_key": "TreasureTT:TT_LBOX_001",
                        "source_table": "TreasureTT",
                        "source_id": "TT_LBOX_001",
                        "type": "treasure",
                        "location_description": "Chest 1 (Large Chest, The Neighborhood: Tram Common, Near Gummi Shop)",
                    }
                ]
            }
        }

        payload = ctx.build_items_payload()

        self.assertEqual(
            payload["items"][0]["location_name"],
            "Chest 1 (Large Chest, The Neighborhood: Tram Common, Near Gummi Shop)",
        )

    def test_client_print_json_locations_use_location_description(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.slot = 1
        ctx.slot_info = {}
        ctx.location_id_to_display_name = {
            1263022969: "Chest 1 (Large Chest, The North Mountain: Treescape, Near Moogle Shop)",
        }
        ctx.location_name_to_display_name = {
            "toy box - victory bonus 017": "Toy Box - Andy's House Heartless",
        }

        translated = ctx._get_client_notification_service().translate_location_json_nodes([
            {"type": "location_id", "text": "1263022969", "player": 1},
            {"type": "text", "text": " / "},
            {"type": "location_name", "text": "Toy Box - Victory Bonus 017", "player": 1},
            {"type": "text", "text": " / "},
            {"type": "location_id", "text": "1263022969", "player": 2},
        ])

        self.assertEqual(
            translated[0],
            {
                "type": "location_name",
                "text": "Chest 1 (Large Chest, The North Mountain: Treescape, Near Moogle Shop)",
                "player": 1,
            },
        )
        self.assertEqual(
            translated[2],
            {
                "type": "location_name",
                "text": "Toy Box - Andy's House Heartless",
                "player": 1,
            },
        )
        self.assertEqual(
            translated[4],
            {"type": "location_id", "text": "1263022969", "player": 2},
        )

    def test_client_build_items_payload_keeps_manifest_vbonus_secondary_rewards_suppressed(self) -> None:
        ctx = KH3Context.__new__(KH3Context)
        ctx.bridge_dir = Path(".")
        ctx.game = "Kingdom Hearts III"
        ctx.items_received = [
            type("NetItem", (), {"item": 1, "location": 2001, "player": 1, "flags": 0})(),
        ]
        ctx.item_names = type(
            "Lookup",
            (),
            {"lookup_in_game": staticmethod(lambda item, _game: "Elixir")},
        )()
        ctx.location_names = type(
            "Lookup",
            (),
            {"lookup_in_slot": staticmethod(lambda loc, _game: "Olympus - Victory Bonus 082")},
        )()
        ctx.player_names = {1: "shado"}
        ctx.location_id_to_bridge_key = {2001: "VBonus:Vbonus_082"}
        ctx.slot_data = {
            "kh3_seed": {"seed_name": "grant-test", "slot": 1},
            "bridge": {
                "locations": [
                    {
                        "bridge_key": "VBonus:Vbonus_082",
                        "source_table": "VBonus",
                        "source_id": "Vbonus_082",
                        "type": "vbonus",
                        "vanilla_item_names": {
                            "Sora_Ability1": "ETresAbilityKind::NONE",
                            "Sora_Ability2": "ETresAbilityKind::NONE",
                            "Sora_Bonus1": "Magic: Fire",
                            "Sora_Bonus2": "Magic: Water",
                        },
                    }
                ]
            }
        }

        payload = ctx.build_items_payload()

        self.assertNotIn("kh3_secondary_rewards", payload["items"][0])
