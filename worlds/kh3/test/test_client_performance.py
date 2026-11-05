"""Regression checks for cached reads and asynchronous bridge publication."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from NetUtils import NetworkItem

from .. import Client as client
from ..bridge_support.files import load_optional_json_file_once
from ..client_support import command_executor, received_items_trace, runtime_artifact_executor
from ..client_support.bridge_publisher import OrderedBridgePublisher
from ..client_support.bridge_sync_service import GameStateSyncInput
from ..client_support.item_sync_service import ItemSyncInput
from ..client_support.presentation import ClientStatusPresenter
from ..client_support.runtime_artifact_service import RuntimeArtifactAction, RuntimeArtifactActionKind
from . import test_client_status as fixtures


class TestRuntimeInspectionCache(unittest.TestCase):
    def test_installation_inspection_verifies_goa_once_then_rechecks_after_invalidation(self):
        ctx = client.KH3Context.__new__(client.KH3Context)
        ctx.kh3_dir = Path("egs")
        ctx.kh3_mods_dir = Path("egs/mods")
        ctx.goa_mod_status = Mock(return_value={"status": "unknown"})
        presenter = ClientStatusPresenter(ctx, runtime_status_loader=lambda _: {})
        with patch.object(client, "goa_mod_status_cache_path", return_value=Path("cache.json")), \
                patch.object(client, "goa_mod_install_status", return_value={
                    "status": "current", "expected_version": "2.0.0",
                }) as inspect:
            self.assertEqual(presenter.build_installed_runtime_summary(now=0).goa_status, "Current v2.0.0")
            presenter.build_installed_runtime_summary(now=3600)
            inspect.assert_called_once_with(
                ctx.kh3_mods_dir, cache_path=Path("cache.json"), allow_hash=True,
            )
            presenter.invalidate_goa_status_cache()
            presenter.build_installed_runtime_summary(now=3601)
            self.assertEqual(inspect.call_count, 2)
        ctx.goa_mod_status.assert_not_called()

    def test_patch_rechecks_goa_and_updates_readiness_without_restart(self):
        for previous_status in ("missing", "outdated", "unknown"):
            for installed_status in ("current", "missing"):
                with self.subTest(previous=previous_status, installed=installed_status):
                    ctx = client.KH3Context.__new__(client.KH3Context)
                    ctx.kh3_dir = Path("egs")
                    ctx.kh3_mods_dir = Path("egs/mods")
                    ctx.goa_mod_status = Mock(return_value={"status": previous_status})
                    ctx.refresh_goa_mod_status = ctx.goa_mod_status
                    loader = Mock(return_value={
                        "ue4ss_files": {"UE4SS.dll": True},
                        "ue4ss_proxy_installed": True,
                        "ue4ss_settings_compliant": True,
                        "probe_single_layout_ready": True,
                        "probe_dll_installed": True,
                        "probe_dll_matches_bundle": True,
                        "probe_mod_enabled": True,
                        "probe_config_installed": True,
                    })
                    presenter = ClientStatusPresenter(ctx, runtime_status_loader=loader)
                    ctx.client_status_presenter = presenter
                    self.assertFalse(presenter.build_installed_runtime_summary(now=0).installed)

                    def download(*args, **kwargs):
                        ctx.goa_mod_status.return_value = {"status": installed_status}

                    with patch.object(command_executor, "download_goa_mod", side_effect=download), \
                            patch.object(client, "goa_mod_status_cache_path", return_value=Path("cache.json")):
                        ctx.ensure_goa_mod_installed()

                    summary = presenter.build_installed_runtime_summary(now=1)
                    self.assertEqual(summary.installed, installed_status == "current")
                    self.assertEqual(ctx.goa_mod_status.call_count, 2)
                    loader.assert_called_once_with(ctx.kh3_dir)
                    if summary.installed:
                        self.assertEqual(summary.goa_status, "Current")
                        self.assertIn("already ready", summary.patch_tooltip)

    def test_failed_goa_download_rechecks_status(self):
        ctx = client.KH3Context.__new__(client.KH3Context)
        ctx.kh3_dir = Path("egs")
        ctx.kh3_mods_dir = Path("egs/mods")
        ctx.goa_mod_status = Mock(return_value={"status": "missing"})
        ctx.refresh_goa_mod_status = ctx.goa_mod_status
        presenter = ClientStatusPresenter(ctx, runtime_status_loader=lambda _: {})
        ctx.client_status_presenter = presenter
        presenter.build_installed_runtime_summary(now=0)
        with patch.object(command_executor, "download_goa_mod", side_effect=OSError("partial install")), \
                patch.object(client, "goa_mod_status_cache_path", return_value=Path("cache.json")):
            with self.assertRaises(OSError):
                ctx.ensure_goa_mod_installed()
        self.assertFalse(presenter.build_installed_runtime_summary(now=1).installed)
        self.assertEqual(ctx.goa_mod_status.call_count, 2)

    def test_goa_inspection_overlapping_install_does_not_cache_stale_status(self):
        source = SimpleNamespace(kh3_dir=Path("egs"))
        presenter = ClientStatusPresenter(source, runtime_status_loader=lambda _: {})

        def inspect_before_install_finishes():
            presenter.invalidate_goa_status_cache()
            return {"status": "missing"}

        source.goa_mod_status = Mock(side_effect=inspect_before_install_finishes)
        presenter.build_installed_runtime_summary(now=0)
        source.goa_mod_status.side_effect = None
        source.goa_mod_status.return_value = {"status": "current"}
        summary = presenter.build_installed_runtime_summary(now=1)
        self.assertEqual(summary.goa_status, "Current")
        self.assertEqual(source.goa_mod_status.call_count, 2)

    def test_inspection_lasts_until_directory_change_or_explicit_invalidation(self):
        source = SimpleNamespace(kh3_dir=Path("egs"), goa_mod_status=Mock(return_value={}))
        loader = Mock(return_value={})
        presenter = ClientStatusPresenter(source, runtime_status_loader=loader)
        presenter.build_installed_runtime_summary(now=0)
        presenter.build_installed_runtime_summary(now=3600)
        loader.assert_called_once_with(Path("egs"))
        presenter.invalidate_goa_status_cache()
        presenter.build_installed_runtime_summary(now=3601)
        self.assertEqual(loader.call_count, 1)
        source.kh3_dir = Path("other-install")
        presenter.build_installed_runtime_summary(now=3602)
        self.assertEqual(loader.call_count, 2)
        presenter.invalidate_runtime_status_cache()
        presenter.build_installed_runtime_summary(now=3603)
        self.assertEqual(loader.call_count, 3)

    def test_failed_install_and_uninstall_invalidate_inspection(self):
        for kind, target in (
            (RuntimeArtifactActionKind.INSTALL_RUNTIME_FILES, "install_kh3_runtime_files"),
            (RuntimeArtifactActionKind.UNINSTALL_RUNTIME_ARTIFACTS, "uninstall_kh3_ap_files"),
        ):
            with self.subTest(kind=kind):
                ctx = client.KH3Context.__new__(client.KH3Context)
                ctx.kh3_dir = Path("egs")
                ctx.kh3_mods_dir = Path("egs/mods")
                ctx.bridge_dir = None
                presenter = Mock()
                ctx._get_client_status_presenter = lambda: presenter
                with patch.object(runtime_artifact_executor, target, side_effect=OSError("partial install")):
                    with self.assertRaises(OSError):
                        ctx._create_runtime_artifact_executor().apply(RuntimeArtifactAction(kind))
                presenter.invalidate_runtime_status_cache.assert_called_once_with()


class TestReadOnlyPayloads(unittest.TestCase):
    def test_unchanged_reads_share_snapshot_and_nested_edits_are_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "payload.json"
            path.write_text('{"items":[{"value":1}]}', encoding="utf-8")
            reader = Mock(wraps=load_optional_json_file_once)
            service = fixtures.TestClientTransportService.build_service(payload_reader=reader)
            first = service.load_sidecar_payload(root, path.name, read_only=True)
            second = service.load_sidecar_payload(root, path.name, read_only=True)
            self.assertIs(first, second)
            reader.assert_called_once_with(path)
            with self.assertRaises(TypeError):
                first["items"][0]["value"] = 99
            with self.assertRaises(TypeError):
                first["items"].append({})
            editable = service.load_sidecar_payload(root, path.name)
            editable["items"][0]["value"] = 99
            self.assertEqual(first["items"][0]["value"], 1)
            self.assertEqual(json.loads(json.dumps(first)), {"items": [{"value": 1}]})

    def test_failed_read_is_retried_without_waiting_for_a_new_file_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_text('{"value":2}', encoding="utf-8")
            reader = Mock(side_effect=[None, {"value": 2}])
            service = fixtures.TestClientTransportService.build_service(payload_reader=reader)
            self.assertIsNone(service.load_sidecar_payload(root, path.name, read_only=True))
            self.assertEqual(service.load_sidecar_payload(root, path.name, read_only=True), {"value": 2})
            self.assertEqual(reader.call_count, 2)

    def test_replacement_during_read_does_not_cache_old_contents_as_new_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            path.write_text('{"value":1}', encoding="utf-8")

            def read(current):
                payload = load_optional_json_file_once(current)
                current.write_text('{"value":200}', encoding="utf-8")
                return payload

            service = fixtures.TestClientTransportService.build_service(payload_reader=read)
            self.assertEqual(service.load_sidecar_payload(root, path.name), {"value": 1})
            self.assertEqual(service.load_sidecar_payload(root, path.name), {"value": 200})

    def test_event_loop_reader_never_sleeps_on_locks_or_partial_json(self):
        with patch("time.sleep", side_effect=AssertionError("blocking retry")):
            with patch.object(Path, "read_text", side_effect=PermissionError("locked")) as read:
                self.assertIsNone(load_optional_json_file_once(Path("state.json")))
                self.assertEqual(read.call_count, 1)
            with patch.object(Path, "read_text", return_value='{"partial":'):
                self.assertIsNone(load_optional_json_file_once(Path("state.json")))


class TestIncrementalItems(unittest.TestCase):
    def make_source(self, received):
        return ItemSyncInput(
            received_items=received,
            game="Kingdom Hearts III",
            player_names={1: "Sora"},
            location_id_to_bridge_key={},
            slot_data={"bridge": {"locations": []}},
            bridge_save_dir=None,
            lookup_item_name=Mock(side_effect=lambda item, _game: "Ultima Weapon Shard" if item == 2 else "Raw Potion"),
            lookup_location_name=Mock(side_effect=lambda location, _game: str(location)),
        )

    def test_status_refreshes_reuse_items_and_appends_normalize_only_the_suffix(self):
        service = fixtures.TestItemSyncService.build_service()
        received = [NetworkItem(1, 100, 1, 0), NetworkItem(2, 101, 1, 0)]
        source = self.make_source(received)
        first = service.cached_items_payload(source)
        for _ in range(10):
            self.assertIs(first, service.cached_items_payload(source))
        self.assertEqual(source.lookup_item_name.call_count, 2)
        received.extend([NetworkItem(2, 102, 1, 0), NetworkItem(1, 100, 1, 0)])
        second = service.cached_items_payload(source)
        self.assertEqual(source.lookup_item_name.call_count, 3)
        self.assertIs(first["items"][0], second["items"][0])
        self.assertEqual(second, service.build_items_payload(source))
        self.assertNotIn("kh3_runtime_token", second["items"][2])
        self.assertEqual(len(first["items"]), 2)

    def test_replacement_reset_player_names_and_received_shards_match_full_build(self):
        service = fixtures.TestItemSyncService.build_service()
        source = self.make_source([NetworkItem(2, -2, 1, 0)])
        service.cached_items_payload(source)
        source = replace(source, received_items=[NetworkItem(2, -2, 1, 0), NetworkItem(2, 101, 1, 0)])
        self.assertEqual(
            service.cached_items_payload(source), service.build_items_payload(source)
        )
        source.player_names[1] = "New name"
        self.assertEqual(service.cached_items_payload(source)["items"][0]["from_player_name"], "New name")
        source = replace(source, received_items=[NetworkItem(1, -2, 1, 0)])
        self.assertEqual(
            service.cached_items_payload(source), service.build_items_payload(source)
        )
        service.invalidate_items()
        self.assertEqual(
            service.cached_items_payload(source), service.build_items_payload(source)
        )

    def test_failed_name_lookup_does_not_mark_unbuilt_receipts_as_duplicates(self):
        service = fixtures.TestItemSyncService.build_service()
        source = self.make_source([NetworkItem(1, 10, 1, 0), NetworkItem(2, 11, 1, 0)])
        source.lookup_item_name.side_effect = ["Raw Potion", ValueError("data package not ready")]
        with self.assertRaises(ValueError):
            service.cached_items_payload(source)
        source.lookup_item_name.side_effect = lambda item, _game: "Ultima Weapon Shard" if item == 2 else "Raw Potion"
        self.assertEqual(
            service.cached_items_payload(source), service.build_items_payload(source)
        )
        self.assertEqual(len(service.cached_items_payload(source)["items"]), 2)



class TestOrderedPublication(unittest.TestCase):
    @staticmethod
    def submit(publisher, value, on_success=None, directory=Path("bridge")):
        return publisher.submit(
            directory,
            slot_data={"seed": "test"},
            status_payload={"seq": value},
            items_payload={"items": [{"index": value}]},
            on_success=on_success,
        )

    def test_writes_are_ordered_off_thread_and_waiters_do_not_cancel_publication(self):
        async def run_case():
            started, release = threading.Event(), threading.Event()
            main_thread = threading.get_ident()
            observed, completed = [], []

            def publish(_directory, **payload):
                self.assertNotEqual(threading.get_ident(), main_thread)
                if payload["status_payload"]["seq"] == 1:
                    started.set()
                    self.assertTrue(release.wait(3))
                observed.append(payload["status_payload"]["seq"])

            publisher = OrderedBridgePublisher(publish, Mock())
            try:
                self.submit(publisher, 1, lambda: completed.append(1))
                self.submit(publisher, 2, lambda: completed.append(2))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertEqual(completed, [])
                flush = asyncio.create_task(publisher.flush())
                await asyncio.sleep(0)
                flush.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await flush
            finally:
                release.set()
                await publisher.flush()
            self.assertEqual(observed, [1, 2])
            self.assertEqual(completed, [1, 2])

        asyncio.run(run_case())

    def test_failure_retries_latest_snapshot_and_success_notification_waits(self):
        async def run_case():
            publish, on_success, errors = Mock(side_effect=[PermissionError("locked"), None]), Mock(), Mock()
            publisher = OrderedBridgePublisher(publish, errors)
            with self.assertRaises(PermissionError):
                await self.submit(publisher, 1, on_success)
            on_success.assert_not_called()
            await publisher.flush()
            self.assertEqual(publish.call_count, 2)
            on_success.assert_called_once_with()
            self.assertEqual(errors.call_count, 1)

        asyncio.run(run_case())

    def test_mutating_source_cannot_change_an_already_queued_snapshot(self):
        async def run_case():
            recorded = []
            publisher = OrderedBridgePublisher(lambda path, **payload: recorded.append((path, payload)), Mock())
            items = {"items": [{"index": 1}]}
            publisher.submit(Path("old-run"), slot_data={"seed": "old"}, status_payload={}, items_payload=items)
            items["items"][0]["index"] = 99
            self.submit(publisher, 2, directory=Path("new-run"))
            await publisher.flush()
            self.assertEqual(recorded[0][0], Path("old-run"))
            self.assertEqual(recorded[0][1]["items_payload"]["items"][0]["index"], 1)
            self.assertEqual(recorded[1][0], Path("new-run"))

        asyncio.run(run_case())

    def test_received_items_wakeup_and_trace_follow_completed_disk_publication(self):
        async def run_case(root):
            started, release = threading.Event(), threading.Event()
            ctx = client.KH3Context.__new__(client.KH3Context)
            ctx.bridge_dir, ctx.slot_data = root, {}
            ctx._is_ap_connected = lambda: True
            ctx.kh3_received_items_snapshot_ready = False
            ctx.integrated_bridge_activity_event = threading.Event()
            ctx.build_items_payload = lambda: {"items": [{"index": 0}]}
            ctx.build_status_payload = lambda _items: {"connected": True}
            service = fixtures.TestBridgeSyncService.build_service()

            def publish(path, **payload):
                started.set()
                self.assertTrue(release.wait(3))
                service.publish(path, **payload)

            ctx.bridge_publisher = OrderedBridgePublisher(publish, Mock())
            with patch.object(received_items_trace, "append_runtime_item_latency_trace_event") as trace:
                try:
                    ctx.on_package("ReceivedItems", {"index": 0, "items": [NetworkItem(1, 10, 1, 0)]})
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    self.assertFalse(ctx.integrated_bridge_activity_event.is_set())
                    self.assertEqual([call.args[1] for call in trace.call_args_list], ["received_items_packet"])
                finally:
                    release.set()
                    await ctx.flush_bridge_publications()
                self.assertTrue(ctx.integrated_bridge_activity_event.is_set())
                self.assertEqual(load_optional_json_file_once(root / "ap_items.json"), {"items": [{"index": 0}]})
                self.assertEqual(
                    [call.args[1] for call in trace.call_args_list],
                    ["received_items_packet", "ap_items_snapshot_published"],
                )

        with TemporaryDirectory() as directory:
            asyncio.run(run_case(Path(directory)))

    def test_failed_async_progress_write_retries_without_resending_checks(self):
        async def run_case(root):
            sends = []
            attempts = 0

            async def send(messages):
                sends.extend(messages)

            async def publish():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("locked")

            service = fixtures.TestBridgeSyncService.build_service(send_msgs=send, publish_bridge_files=publish)
            (root / "game_state.json").write_text(
                json.dumps(
                    {
                        "protocol_name": fixtures.BRIDGE_PROTOCOL_NAME,
                        "protocol_version": fixtures.BRIDGE_PROTOCOL_VERSION,
                        "seq": 1,
                        "checked_locations": ["chest"],
                    }
                ),
                encoding="utf-8",
            )
            source = GameStateSyncInput(root, {"chest": 10}, True)
            with self.assertRaises(PermissionError):
                await service.synchronize_game_state(source)
            self.assertIsNone(service.last_file_signature)
            await service.synchronize_game_state(source)
            self.assertEqual(len(sends), 1)
            self.assertEqual(attempts, 2)
            self.assertIsNotNone(service.last_file_signature)

        with TemporaryDirectory() as directory:
            asyncio.run(run_case(Path(directory)))

    def test_unchanged_publication_skips_reads_but_recreates_deleted_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = fixtures.TestBridgeSyncService.build_service()
            payload = dict(slot_data={}, status_payload={"connected": True}, items_payload={"items": []})
            service.publish(root, **payload)
            with patch.object(service, "load_optional_json", side_effect=AssertionError("redundant read")):
                service.publish(root, **payload)
            (root / "ap_items.json").unlink()
            service.publish(root, **payload)
            self.assertEqual(load_optional_json_file_once(root / "ap_items.json"), {"items": []})

    def test_reset_during_publication_does_not_commit_the_old_observation_signature(self):
        async def run_case():
            started, release = asyncio.Event(), asyncio.Event()

            async def publish():
                started.set()
                await release.wait()

            service = fixtures.TestBridgeSyncService.build_service(publish_bridge_files=publish)
            ctx = client.KH3Context.__new__(client.KH3Context)
            ctx.bridge_sync_service = service
            pending = asyncio.create_task(service._publish_game_state_progress(5, (1, 2)))
            await started.wait()
            with patch.object(client.CommonContext, "reset_server_state"):
                ctx.reset_server_state()
            service.last_sequence = -1
            release.set()
            await pending
            self.assertEqual(service.last_sequence, -1)
            self.assertIsNone(service.last_file_signature)

        asyncio.run(run_case())
