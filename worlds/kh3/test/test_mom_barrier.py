import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from ..Client import KH3Context
from ..client_support import mom_barrier as barrier


class TestMoMBarrier(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = SimpleNamespace(scope={"seed_name": "test", "slot": 1},
            challenges={123: {}}, state={"cleared": []}, save=Mock(),
            publish_clears=Mock(), pending_checks=lambda completed: completed)
        self.state = dict(schema=barrier.SCHEMA, barrier_id=str(uuid4()), process_id=999,
                          seed_name="test", slot=1, ap_location_id=123, active=True)
        self.event = {**self.state, "sequence": 1, "expires_unix_ms": int(time.time()*1000)+15000}
        self.write("state", self.state)
        self.write("interaction", self.event)

    def write(self, suffix, data):
        (self.root / f"runtime_mom_barrier_{suffix}.json").write_text(json.dumps(data))

    def test_one_examine_launches_once_and_later_examine_can_retry(self):
        self.assertEqual(123, barrier.consume_interaction(self.service, self.root))
        self.assertIsNone(barrier.consume_interaction(self.service, self.root))
        self.service.save.assert_called_once()
        self.write("interaction", {**self.event, "sequence": 2})
        self.assertEqual(123, barrier.consume_interaction(self.service, self.root))

    def test_stale_or_mismatched_interactions_do_not_launch(self):
        for change in ({"process_id": 998}, {"barrier_id": str(uuid4())}, {"slot": 2},
                       {"seed_name": "other"}, {"ap_location_id": 124},
                       {"expires_unix_ms": 0}, {"sequence": 0}):
            with self.subTest(change=change):
                self.write("interaction", {**self.event, **change})
                self.assertIsNone(barrier.consume_interaction(self.service, self.root))
        self.service.save.assert_not_called()

    def test_failed_song_or_other_check_does_not_unlock(self):
        barrier.publish_clear(self.service, self.root, {124})
        self.assertFalse((self.root / "runtime_mom_barrier_unlock.json").exists())

    def test_matching_verified_or_acknowledged_clear_unlocks_exact_barrier(self):
        for local in (False, True):
            self.service.state["cleared"] = [123] if local else []
            barrier.publish_clear(self.service, self.root, set() if local else {123})
            result = barrier.read_record(self.root / "runtime_mom_barrier_unlock.json")
            self.assertTrue(result["cleared"])
            for key in barrier.IDENTITY:
                self.assertEqual(self.state[key], result[key])

    def test_inactive_or_other_seed_state_cannot_launch_or_unlock(self):
        for change in ({"active": False}, {"seed_name": "other"}, {"slot": 2},
                       {"ap_location_id": 124}, {"barrier_id": "bad"}):
            with self.subTest(change=change):
                self.write("state", {**self.state, **change})
                self.assertIsNone(barrier.consume_interaction(self.service, self.root))
                barrier.publish_clear(self.service, self.root, {123})
                self.assertFalse((self.root / "runtime_mom_barrier_unlock.json").exists())

    def test_chest_requires_matching_clear_and_open_chest_cannot_relaunch(self):
        self.write("state", {**self.state, "kind": "chest", "chest_open": False})
        self.assertEqual(123, barrier.consume_interaction(self.service, self.root))
        barrier.publish_clear(self.service, self.root, {124})
        self.assertFalse((self.root / "runtime_mom_barrier_unlock.json").exists())
        barrier.publish_clear(self.service, self.root, {123})
        result = barrier.read_record(self.root / "runtime_mom_barrier_unlock.json")
        self.assertTrue(result["cleared"])
        for key in barrier.IDENTITY:
            self.assertEqual(self.state[key], result[key])
        self.write("state", {**self.state, "kind": "chest", "active": False, "chest_open": True})
        self.write("interaction", {**self.event, "sequence": 2})
        self.assertIsNone(barrier.consume_interaction(self.service, self.root))

    def test_client_launches_from_examine_without_fabricating_a_check(self):
        self.service.poll = lambda: set()
        ctx = KH3Context.__new__(KH3Context)
        ctx.__dict__.update(_get_mom_challenge_service=lambda: self.service,
            bridge_dir=self.root, slot=1, seed_name="test",
            checked_locations=set(), launch_mom_challenge=Mock(), _is_ap_connected=lambda: True,
            send_msgs=AsyncMock())
        async def run():
            await KH3Context.handle_mom_challenge_results(ctx)
            await KH3Context.handle_mom_challenge_results(ctx)
        asyncio.run(run())
        ctx.launch_mom_challenge.assert_called_once_with(123)
        ctx.send_msgs.assert_not_awaited()

    def test_other_rooms_checked_locations_do_not_open_this_barrier(self):
        self.service.poll = lambda: set()
        ctx = KH3Context.__new__(KH3Context)
        ctx.__dict__.update(_get_mom_challenge_service=lambda: self.service,
            bridge_dir=self.root, slot=1, seed_name="different-room",
            checked_locations={123}, launch_mom_challenge=Mock(), _is_ap_connected=lambda: True,
            send_msgs=AsyncMock())
        asyncio.run(KH3Context.handle_mom_challenge_results(ctx))
        self.assertFalse((self.root / "runtime_mom_barrier_unlock.json").exists())
