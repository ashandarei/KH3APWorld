"""Fault-injection scenarios using synthetic saves, never the live save directory."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from CommonClient import process_server_cmd

from ..Client import KH3Context
from ..client_support.run_manager import KH3RunManagerError, KH3RunManagerService
from .test_run_manager import _write_seed_pak


class SeedSwitchRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_to_reseeded_server_reaches_kh3_preservation_before_auth(self):
        ctx = KH3Context.__new__(KH3Context)
        ctx.seed_name = "AP_11111111111111111111"
        ctx.offline_seed_name = ctx.seed_name
        ctx.bridge_dir = None
        ctx.server = None
        ctx.kh3_check_reporting_enabled = True
        ctx.kh3_room_pre_auth_blocked = True
        ctx.kh3_seed_bootstrap_pending = False
        ctx.kh3_restart_required_seed_id = None
        ctx.gui_error = Mock()
        ctx.update_permissions = Mock()
        ctx.prepare_data_package = AsyncMock()
        ctx.installed_seed_pak_mismatches_room = Mock(return_value=True)
        ctx.installed_run_mismatches_connection_affinity = Mock(return_value=False)
        events = []

        async def preserve(**kwargs):
            events.append("preserve")
            return True

        async def authenticate(_service, password_requested, **kwargs):
            self.assertFalse(ctx.kh3_room_pre_auth_blocked)
            self.assertTrue(ctx.kh3_seed_bootstrap_pending)
            events.append("authenticate")

        ctx._suspend_installed_kh3_run_for_server = AsyncMock(side_effect=preserve)
        from ..client_support.connection_flow import ConnectionFlowService
        authentication_patch = patch.object(ConnectionFlowService, "authenticate", authenticate)
        authentication_patch.start()
        self.addCleanup(authentication_patch.stop)

        # Automatic reconnect goes straight to server_loop, bypassing connect().
        await ctx.connection_closed()
        await process_server_cmd(ctx, {
            "cmd": "RoomInfo", "seed_name": "AP_22222222222222222222",
            "version": [0, 6, 7], "tags": [], "password": False,
            "hint_cost": 10, "location_check_points": 1, "games": [],
        })

        ctx.gui_error.assert_not_called()
        self.assertEqual(events, ["preserve", "authenticate"])
        self.assertEqual(ctx.seed_name, "AP_22222222222222222222")
        self.assertEqual(ctx.offline_seed_name, "AP_11111111111111111111")
        self.assertFalse(ctx.kh3_check_reporting_enabled)

    def test_active_marker_failure_restores_old_saves_and_allows_retry(self):
        for same_slot in (True, False):
            with self.subTest(same_slot=same_slot), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                service = KH3RunManagerService(
                    run_root=root / "runs", bridge_root=root / "bridge",
                    mods_dir=root / "mods", save_dir=root / "SaveGames",
                    process_running=lambda: False,
                )
                current = service.import_pak(_write_seed_pak(
                    service.mods_dir, seed="11111111111111111111", player=1, slot="Current",
                ))
                target = service.import_pak(_write_seed_pak(
                    root / "downloads", seed="22222222222222222222", player=1, slot="Target",
                ))
                service._write_active_run_id(current.run_id)
                relative = Path("kh3sv2/data/KHIII_slot1.bin")
                target_relative = relative if same_slot else Path("kh3sv2/data/KHIII_slot2.bin")
                live = service.save_dir / relative
                live.parent.mkdir(parents=True)
                live.write_bytes(b"current progress")
                snapshot = target.save_snapshot_dir / target_relative
                snapshot.parent.mkdir(parents=True)
                snapshot.write_bytes(b"target progress")

                with patch.object(service, "_write_active_run_id", side_effect=OSError("injected marker failure")):
                    with self.assertRaisesRegex(KH3RunManagerError, "was rolled back"):
                        service.activate(target.run_id, active_save_run_id=current.run_id)

                self.assertEqual(live.read_bytes(), b"current progress")
                self.assertEqual(service._iter_gameplay_save_files(service.save_dir), [live])
                self.assertEqual(service._read_active_run_id(), current.run_id)
                self.assertEqual([p.name for p in service.mods_dir.glob("*.pak")], [current.pak_name])
                self.assertEqual(snapshot.read_bytes(), b"target progress")

                result = service.activate(target.run_id, active_save_run_id=current.run_id)
                self.assertTrue(result.switched)
                self.assertEqual((service.save_dir / target_relative).read_bytes(), b"target progress")
                self.assertEqual((current.save_snapshot_dir / relative).read_bytes(), b"current progress")
