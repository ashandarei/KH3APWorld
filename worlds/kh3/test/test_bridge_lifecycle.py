from __future__ import annotations

import asyncio
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ..client_support.lifecycle_service import BridgeLifecycleService


class BridgeWorkerHandoffTest(unittest.IsolatedAsyncioTestCase):
    def service(self, worker) -> BridgeLifecycleService:
        return BridgeLifecycleService(
            SimpleNamespace(server_address=None, shutdown=AsyncMock()),
            server_loop=AsyncMock(),
            bridge_watcher=AsyncMock(),
            integrated_bridge_worker=worker,
            log_info=Mock(),
            log_warning=Mock(),
            log_exception=Mock(),
            shutdown_timeout_seconds=0.1,
        )

    async def test_rapid_switches_wait_for_old_worker_and_start_only_latest_pair(self) -> None:
        loop = asyncio.get_running_loop()
        first_started = asyncio.Event()
        last_started = asyncio.Event()
        release = threading.Event()
        calls: list[str] = []

        def worker(bridge: Path, _save: Path, stop: threading.Event) -> int:
            calls.append(bridge.name)
            if bridge.name == "first":
                loop.call_soon_threadsafe(first_started.set)
                release.wait(3)
            else:
                loop.call_soon_threadsafe(last_started.set)
                stop.wait(3)
            return 0

        service = self.service(worker)
        try:
            service.start_integrated_bridge(enabled=True, bridge_dir=Path("first"), save_dir=Path("save"))
            await asyncio.wait_for(first_started.wait(), 1)
            service.start_integrated_bridge(enabled=True, bridge_dir=Path("second"), save_dir=Path("save"))
            await asyncio.sleep(0.03)
            service.start_integrated_bridge(enabled=True, bridge_dir=Path("third"), save_dir=Path("save"))
            await asyncio.sleep(0.03)
            self.assertEqual(calls, ["first"], "A successor must not touch files until its predecessor finishes")
            release.set()
            await asyncio.wait_for(last_started.wait(), 1)
            self.assertEqual(calls, ["first", "third"])
        finally:
            release.set()
            await service.shutdown()

    async def test_shutdown_timeout_blocks_file_changes_and_preserves_worker_ownership(self) -> None:
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def worker(_bridge: Path, _save: Path, _stop: threading.Event) -> int:
            loop.call_soon_threadsafe(started.set)
            release.wait(3)
            return 0

        service = self.service(worker)
        try:
            service.start_integrated_bridge(enabled=True, bridge_dir=Path("first"), save_dir=Path("save"))
            await asyncio.wait_for(started.wait(), 1)
            thread = service.state.integrated_bridge_thread
            with self.assertRaisesRegex(RuntimeError, "watcher.*still running"):
                await service.wait_for_integrated_bridge_shutdown()
            self.assertIs(service.state.integrated_bridge_thread, thread)
            self.assertTrue(thread.is_alive())
            release.set()
            await service.wait_for_integrated_bridge_shutdown()
            self.assertIsNone(service.state.integrated_bridge_thread)
        finally:
            release.set()
            await service.shutdown()

    async def test_cancelling_async_wrapper_does_not_release_live_worker(self) -> None:
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def worker(_bridge: Path, _save: Path, _stop: threading.Event) -> int:
            loop.call_soon_threadsafe(started.set)
            release.wait(3)
            return 0

        service = self.service(worker)
        try:
            service.start_integrated_bridge(enabled=True, bridge_dir=Path("first"), save_dir=Path("save"))
            await asyncio.wait_for(started.wait(), 1)
            task = service.state.integrated_bridge_task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            with self.assertRaisesRegex(RuntimeError, "watcher.*still running"):
                await service.wait_for_integrated_bridge_shutdown()
        finally:
            release.set()
            await service.shutdown()

    async def test_worker_error_still_allows_ordered_client_shutdown(self) -> None:
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()

        def worker(_bridge: Path, _save: Path, _stop: threading.Event) -> int:
            loop.call_soon_threadsafe(started.set)
            release.wait(3)
            raise OSError("simulated bridge read error")

        service = self.service(worker)
        service.context.flush_bridge_publications = AsyncMock()
        try:
            service.start_integrated_bridge(enabled=True, bridge_dir=Path("first"), save_dir=Path("save"))
            await asyncio.wait_for(started.wait(), 1)
            release.set()
            await service.shutdown()
            self.assertIsNone(service.state.integrated_bridge_thread)
            self.assertEqual(service.context.flush_bridge_publications.await_count, 2)
        finally:
            release.set()
