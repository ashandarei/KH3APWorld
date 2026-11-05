"""Notification polling and completion guarantees for combined bridge writes."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from NetUtils import NetworkItem

from .. import Client as client
from ..bridge_support.files import load_optional_json_file_once
from ..client_support import received_items_trace
from ..client_support.bridge_publisher import OrderedBridgePublisher
from . import test_client_status as fixtures


def notification_payload(*entries, version=fixtures.BRIDGE_PROTOCOL_VERSION):
    return {
        "protocol_name": fixtures.BRIDGE_PROTOCOL_NAME,
        "protocol_version": version,
        "notifications": [{"seq": sequence, "message": message} for sequence, message in entries],
    }


class TestNotificationPolling(unittest.TestCase):
    @staticmethod
    def make_context(root):
        ctx = client.KH3Context.__new__(client.KH3Context)
        ctx.bridge_dir = root
        service = ctx._get_client_notification_service()
        service.log_info = Mock()
        service.log_warning = Mock()
        reader = Mock(wraps=load_optional_json_file_once)
        ctx._get_client_transport_service().payload_reader = reader
        return ctx, service, reader

    def test_unchanged_polls_share_one_read_and_keep_notification_order_and_cursor(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "bridge_notifications.json"
            path.write_text(json.dumps(notification_payload((2, "Old"))), encoding="utf-8")
            ctx, service, reader = self.make_context(root)
            service.mark_existing_bridge_notifications_processed()
            self.assertEqual(service.last_bridge_notification_seq, 2)
            path.write_text(
                json.dumps(notification_payload((4, "Fourth"), (2, "Old"), (3, "Third"))),
                encoding="utf-8",
            )
            with patch.object(service, "load_optional_json", side_effect=AssertionError("poll used retry reader")):
                for _ in range(10):
                    service.handle_bridge_notifications_file()
            self.assertEqual(reader.call_count, 1)
            self.assertEqual([call.args[0] for call in service.log_info.call_args_list], ["Third", "Fourth"])
            self.assertEqual(ctx.last_processed_bridge_notification_seq, 4)
            path.write_text(json.dumps(notification_payload((5, "New notification"))), encoding="utf-8")
            service.handle_bridge_notifications_file()
            self.assertEqual(reader.call_count, 2)
            service.log_info.assert_called_with("New notification")
            self.assertEqual(service.last_bridge_notification_seq, 5)

    def test_locked_or_partial_reads_retry_next_poll_without_sleep_or_cursor_loss(self):
        for unreadable in (PermissionError("locked"), "{partial"):
            with self.subTest(unreadable=unreadable), TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "bridge_notifications.json"
                contents = json.dumps(notification_payload((1, "Delivered once")))
                path.write_text(contents, encoding="utf-8")
                _ctx, service, reader = self.make_context(root)
                with (
                    patch.object(Path, "read_text", side_effect=[unreadable, contents]) as read,
                    patch("worlds.kh3.bridge_support.files.time.sleep", side_effect=AssertionError("blocking retry")),
                ):
                    service.handle_bridge_notifications_file()
                    service.log_info.assert_not_called()
                    self.assertEqual(service.last_bridge_notification_seq, 0)
                    service.handle_bridge_notifications_file()
                    service.handle_bridge_notifications_file()
                self.assertEqual(read.call_count, 2)
                self.assertEqual(reader.call_count, 2)
                service.log_info.assert_called_once_with("Delivered once")
                self.assertEqual(service.last_bridge_notification_seq, 1)

    def test_invalid_protocol_deletion_and_directory_change_do_not_deliver_stale_notices(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "bridge_notifications.json"
            path.write_text(json.dumps(notification_payload((3, "Bad"), version=9999)), encoding="utf-8")
            ctx, service, _reader = self.make_context(root)
            service.handle_bridge_notifications_file()
            service.log_info.assert_not_called()
            service.log_warning.assert_called_once()
            self.assertEqual(service.last_bridge_notification_seq, 0)

            path.unlink()
            service.handle_bridge_notifications_file()
            path.write_text(json.dumps(notification_payload((3, "Recreated"))), encoding="utf-8")
            service.handle_bridge_notifications_file()
            service.log_info.assert_called_once_with("Recreated")

            other = root / "other-run"
            other.mkdir()
            (other / path.name).write_text(json.dumps(notification_payload((1, "Other run"))), encoding="utf-8")
            ctx.bridge_dir = other
            ctx.last_processed_bridge_notification_seq = 0
            service.handle_bridge_notifications_file()
            self.assertEqual([call.args[0] for call in service.log_info.call_args_list], ["Recreated", "Other run"])


class TestCoalescedBridgePublication(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def submit(publisher, sequence, slot_data, on_success=None, directory=Path("bridge")):
        return publisher.submit(
            directory,
            slot_data=slot_data,
            status_payload={"seq": sequence},
            items_payload={"items": [{"index": index} for index in range(sequence)]},
            on_success=on_success,
        )

    def blocked_writer(self, *, failures=()):
        started, release = threading.Event(), threading.Event()
        writes, completed = [], []
        fail_once = set(failures)
        errors = Mock()

        def publish(path, **payload):
            sequence = payload["status_payload"]["seq"]
            if sequence == 0:
                started.set()
                self.assertTrue(release.wait(3))
            writes.append((path, sequence))
            if sequence in fail_once:
                fail_once.remove(sequence)
                raise PermissionError("locked")

        return OrderedBridgePublisher(publish, errors), started, release, writes, completed, errors

    async def test_burst_publishes_two_full_snapshots_and_completes_all_callers_after_disk_write(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial_started, final_started = threading.Event(), threading.Event()
            initial_release, final_release = threading.Event(), threading.Event()
            writes, completed = [], []
            slot_data = {"seed": "test"}
            service = fixtures.TestBridgeSyncService.build_service()

            def publish(path, **payload):
                sequence = payload["status_payload"]["seq"]
                if sequence == 0:
                    initial_started.set()
                    self.assertTrue(initial_release.wait(3))
                else:
                    final_started.set()
                    self.assertTrue(final_release.wait(3))
                service.publish(path, **payload)
                writes.append(sequence)

            def completion(sequence):
                disk_sequence = load_optional_json_file_once(root / "ap_status.json")["seq"]
                completed.append((sequence, disk_sequence))

            publisher = OrderedBridgePublisher(publish, Mock())
            try:
                first = self.submit(publisher, 0, slot_data, lambda: completion(0), root)
                self.assertTrue(await asyncio.to_thread(initial_started.wait, 2))
                waiting = [
                    self.submit(publisher, sequence, slot_data, lambda seq=sequence: completion(seq), root)
                    for sequence in range(1, 51)
                ]
                waiting[10].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting[10]
                self.assertEqual(completed, [])
                initial_release.set()
                self.assertTrue(await asyncio.to_thread(final_started.wait, 2))
                await first
                self.assertEqual(completed, [(0, 0)])
                self.assertTrue(all(not future.done() for future in waiting if not future.cancelled()))
                final_release.set()
                await asyncio.gather(*(future for future in waiting if not future.cancelled()))
            finally:
                initial_release.set()
                final_release.set()
                await publisher.flush()
            self.assertEqual(writes, [0, 50])
            self.assertEqual(completed, [(0, 0), *((sequence, 50) for sequence in range(1, 51))])
            self.assertEqual(
                load_optional_json_file_once(root / "ap_items.json"),
                {
                    "items": [{"index": index} for index in range(50)],
                },
            )

    async def test_connection_reset_slot_data_and_intervening_directories_keep_write_boundaries(self):
        publisher, started, release, writes, completed, _errors = self.blocked_writer()
        original_slot, next_slot = {"seed": "original"}, {"seed": "next"}
        ctx = client.KH3Context.__new__(client.KH3Context)
        ctx.bridge_publisher = publisher
        try:
            self.submit(publisher, 0, original_slot)
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            self.submit(publisher, 1, original_slot, lambda: completed.append(1))
            self.submit(publisher, 2, original_slot, lambda: completed.append(2))
            with patch.object(client.CommonContext, "reset_server_state"):
                ctx.reset_server_state()
            self.submit(publisher, 3, original_slot, lambda: completed.append(3))
            self.submit(publisher, 4, original_slot, lambda: completed.append(4))
            self.submit(publisher, 5, next_slot, lambda: completed.append(5))
            self.submit(publisher, 6, next_slot, lambda: completed.append(6))
            self.submit(publisher, 7, next_slot, lambda: completed.append(7), Path("other"))
            self.submit(publisher, 8, next_slot, lambda: completed.append(8))
        finally:
            release.set()
            await publisher.flush()
        self.assertEqual(
            writes,
            [(Path("bridge"), sequence) for sequence in (0, 2, 4, 6)]
            + [
                (Path("other"), 7),
                (Path("bridge"), 8),
            ],
        )
        self.assertEqual(completed, list(range(1, 9)))

    async def test_received_item_packet_callbacks_finish_after_the_combined_snapshot_is_published(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            started, release = threading.Event(), threading.Event()
            writes, received = [], []
            service = fixtures.TestBridgeSyncService.build_service()
            ctx = client.KH3Context.__new__(client.KH3Context)
            ctx.bridge_dir, ctx.slot_data = root, {}
            ctx._is_ap_connected = lambda: True
            ctx.kh3_received_items_snapshot_ready = False
            ctx.integrated_bridge_activity_event = threading.Event()
            ctx.build_items_payload = lambda: {"items": list(received)}
            ctx.build_status_payload = lambda _items: {"connected": True}

            def publish(path, **payload):
                if not writes:
                    started.set()
                    self.assertTrue(release.wait(3))
                service.publish(path, **payload)
                writes.append(len(payload["items_payload"]["items"]))

            ctx.bridge_publisher = OrderedBridgePublisher(publish, Mock())
            with patch.object(received_items_trace, "append_runtime_item_latency_trace_event") as trace:
                try:
                    for index in range(4):
                        received.append({"index": index})
                        ctx.on_package(
                            "ReceivedItems",
                            {
                                "index": index,
                                "items": [NetworkItem(1, 10 + index, 1, 0)],
                            },
                        )
                        if index == 0:
                            self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    self.assertFalse(ctx.integrated_bridge_activity_event.is_set())
                    self.assertEqual([call.args[1] for call in trace.call_args_list], ["received_items_packet"] * 4)
                finally:
                    release.set()
                    await ctx.flush_bridge_publications()
                self.assertEqual(writes, [1, 4])
                self.assertTrue(ctx.integrated_bridge_activity_event.is_set())
                published = [call for call in trace.call_args_list if call.args[1] == "ap_items_snapshot_published"]
                self.assertEqual([call.kwargs["packet_start_index"] for call in published], [0, 1, 2, 3])
                final_stat = (root / "ap_items.json").stat()
                self.assertTrue(
                    all(
                        call.kwargs["ap_items_signature"] == [final_stat.st_mtime_ns, final_stat.st_size]
                        for call in published[1:]
                    )
                )

    async def test_failed_combined_snapshot_retries_with_every_success_callback(self):
        publisher, started, release, writes, completed, errors = self.blocked_writer(failures=(2,))
        slot_data = {}
        try:
            first = self.submit(publisher, 0, slot_data, lambda: completed.append(0))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            second = self.submit(publisher, 1, slot_data, lambda: completed.append(1))
            third = self.submit(publisher, 2, slot_data, lambda: completed.append(2))
            release.set()
            await first
            for future in (second, third):
                with self.assertRaises(PermissionError):
                    await future
            self.assertEqual(completed, [0])
        finally:
            release.set()
            await publisher.flush()
        self.assertEqual([sequence for _path, sequence in writes], [0, 2, 2])
        self.assertEqual(completed, [0, 1, 2])
        self.assertEqual(errors.call_count, 1)

    async def test_newer_snapshot_recovers_a_failed_active_write_without_retrying_stale_data(self):
        publisher, started, release, writes, completed, errors = self.blocked_writer(failures=(0,))
        slot_data = {}
        try:
            first = self.submit(publisher, 0, slot_data, lambda: completed.append(0))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            second = self.submit(publisher, 1, slot_data, lambda: completed.append(1))
            third = self.submit(publisher, 2, slot_data, lambda: completed.append(2))
            release.set()
            with self.assertRaises(PermissionError):
                await first
            await asyncio.gather(second, third)
        finally:
            release.set()
            await publisher.flush()
        self.assertEqual([sequence for _path, sequence in writes], [0, 2])
        self.assertEqual(completed, [1, 2])
        self.assertEqual(errors.call_count, 1)

    async def test_one_broken_callback_does_not_hide_other_completions_or_retry_successful_writes(self):
        write, errors, successful = Mock(), Mock(), Mock()
        broken = Mock(side_effect=RuntimeError("callback failed"))
        publisher = OrderedBridgePublisher(write, errors)
        slot_data = {}
        first = self.submit(publisher, 1, slot_data, broken)
        second = self.submit(publisher, 2, slot_data, successful)
        await asyncio.gather(first, second)
        await publisher.flush()
        write.assert_called_once()
        self.assertEqual(write.call_args.kwargs["status_payload"], {"seq": 2})
        broken.assert_called_once_with()
        successful.assert_called_once_with()
        self.assertEqual(errors.call_count, 1)
