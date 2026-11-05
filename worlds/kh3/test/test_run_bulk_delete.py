"""Bulk deletion must preserve the active run and only use confirmed targets."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ..Client import KH3Context
from ..client_support.run_manager import KH3RunManagerError, KH3RunManagerService
from . import test_run_manager as fixtures


@pytest.fixture
def runs(tmp_path):
    service = fixtures.KH3RunManagerTest()._service(tmp_path)
    pak = fixtures._write_seed_pak(service.mods_dir, seed="22222222222222222222", player=1, slot="Active")
    active = service.import_pak(pak)
    inactive = service.import_pak(fixtures._write_seed_pak(
        tmp_path / "downloads", seed="11111111111111111111", player=1, slot="Old"))
    return service, active, inactive


def test_deletes_stored_snapshot_and_bridge_without_touching_active_saves(runs):
    service, active, inactive = runs
    bridge = service.bridge_root / inactive.bridge_dir_name
    bridge.mkdir(parents=True)
    (bridge / "state.json").write_text("{}")
    inactive.save_snapshot_dir.mkdir(parents=True)
    (inactive.save_snapshot_dir / "KHIII_slot1.bin").write_bytes(b"synthetic stored AP save")
    service.save_dir.mkdir(parents=True)
    live = service.save_dir / "KHIII_slot1.bin"
    live.write_bytes(b"synthetic active save")
    with patch.object(service, "_iter_gameplay_save_files", side_effect=AssertionError("live save inspection")):
        result = service.delete_inactive_run(inactive.run_id, expected_active_run_id=active.run_id)
    assert result.removed_run and result.removed_bridge_dirs == 1
    assert not inactive.run_dir.exists() and not bridge.exists()
    assert active.run_dir.exists() and active.installed_pak_path.exists()
    assert live.read_bytes() == b"synthetic active save"


@pytest.mark.parametrize("reason", ["active_target", "active_changed", "no_active"])
@pytest.mark.parametrize("current_platform_only", [True, False])
def test_refuses_unsafe_deletion(runs, reason, current_platform_only):
    service, active, inactive = runs
    expected = active.run_id
    target = inactive.run_id
    if reason == "active_target":
        target = active.run_id
    elif reason == "active_changed":
        expected = "previous-active"
    elif reason == "no_active":
        active.installed_pak_path.unlink()
    with pytest.raises(KH3RunManagerError):
        service.delete_inactive_run(
            target, expected_active_run_id=expected, current_platform_only=current_platform_only,
        )
    assert inactive.run_dir.exists() and active.run_dir.exists()


def test_shared_active_pak_and_bridge_survive_other_profile_deletion(runs):
    service, active, _ = runs
    other = service.import_pak(active.installed_pak_path, server_address="localhost:38283", connection_slot_name="Active")
    service._write_active_run_id(active.run_id)
    bridge = service.bridge_root / active.bridge_dir_name
    bridge.mkdir(parents=True)
    (bridge / "state.json").write_text("{}")
    result = service.delete_inactive_run(other.run_id, expected_active_run_id=active.run_id, active_bridge_dir=bridge)
    assert result.removed_run and result.removed_bridge_dirs == 0
    assert active.installed_pak_path.exists() and active.run_dir.exists() and bridge.exists()


@pytest.mark.parametrize("action", ["single", "inactive", "selected"])
def test_connected_client_can_delete_inactive_runs_while_game_is_open(runs, action):
    service, active, inactive = runs
    service.process_running = lambda: True
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = service.bridge_root / active.bridge_dir_name
    ctx.kh3_active_connection_profile_run_id = active.run_id
    ctx.kh3_run_action_running = False
    ctx._get_run_manager_service = Mock(return_value=service)
    ctx._is_ap_connected = Mock(return_value=True)
    ctx.disconnect = Mock(side_effect=AssertionError("unexpected disconnect"))
    ctx.stop_integrated_bridge = Mock(side_effect=AssertionError("unexpected watcher stop"))
    with patch("worlds.kh3.Client.is_process_running", return_value=True):
        if action == "inactive":
            asyncio.run(ctx._delete_inactive_kh3_runs_action((inactive.run_id,), active.run_id))
        elif action == "selected":
            second = service.import_pak(fixtures._write_seed_pak(
                inactive.run_dir.parent / "downloads", seed="33333333333333333333", player=1, slot="Another"))
            asyncio.run(ctx._delete_kh3_runs_action((inactive.run_id, second.run_id)))
            assert not second.run_dir.exists()
        else:
            asyncio.run(ctx._delete_kh3_run_action(inactive.run_id))
    assert not inactive.run_dir.exists()
    assert active.run_dir.exists() and active.installed_pak_path.exists()
    assert not ctx.kh3_run_action_running


@pytest.mark.parametrize("failure", [None, "partial", "cancel"])
def test_selected_deletion_holds_one_lock_and_refreshes_selection_between_runs(failure):
    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_run_action_running = False
    ctx._is_ap_connected = Mock(return_value=False)
    selections = [object(), object(), object()]
    ctx._kh3_run_selection = Mock(side_effect=selections)
    service = SimpleNamespace(delete=AsyncMock())
    ctx._create_run_action_service = Mock(return_value=service)

    async def delete(run_id, selection):
        assert ctx.kh3_run_action_running
        if run_id == "second":
            if failure == "partial":
                return False
            if failure == "cancel":
                raise asyncio.CancelledError
        return True

    service.delete.side_effect = delete
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ctx._delete_kh3_runs_action(("first", "second", "first", "third")))
    else:
        asyncio.run(ctx._delete_kh3_runs_action(("first", "second", "first", "third")))
    expected_ids = ("first", "second", "third") if failure is None else ("first", "second")
    assert [call.args for call in service.delete.await_args_list] == list(zip(expected_ids, selections))
    ctx._create_run_action_service.assert_called_once()
    assert not ctx.kh3_run_action_running
    assert ctx.kh3_run_action_label == ""


def test_connected_selected_deletion_refuses_entire_batch_when_active_run_is_selected(runs):
    service, active, inactive = runs
    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_run_action_running = False
    ctx._is_ap_connected = Mock(return_value=True)
    ctx._get_run_manager_service = Mock(return_value=service)
    ctx._delete_inactive_kh3_runs_action = AsyncMock()
    asyncio.run(ctx._delete_kh3_runs_action((inactive.run_id, active.run_id)))
    ctx._delete_inactive_kh3_runs_action.assert_not_awaited()
    assert inactive.run_dir.exists() and active.run_dir.exists()


def test_switch_while_game_open_shows_warning_without_disconnecting():
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = None
    ctx.kh3_run_action_running = False
    ctx.ui = SimpleNamespace(show_run_switch_warning=Mock())
    ctx.disconnect = Mock(side_effect=AssertionError("unexpected disconnect"))
    with patch("worlds.kh3.Client.is_process_running", return_value=True):
        asyncio.run(ctx._activate_kh3_run_action("other"))
    ctx.ui.show_run_switch_warning.assert_called_once_with()
    assert not ctx.kh3_run_action_running


def test_offline_auto_storage_reuses_managed_connected_profile_without_duplicate(tmp_path):
    service = fixtures.KH3RunManagerTest()._service(tmp_path)
    pak = fixtures._write_seed_pak(service.mods_dir, seed="22222222222222222222", player=1, slot="Active")
    active = service.ensure_pak_managed(pak, server_address="localhost:38281", connection_slot_name="Active").run
    before = {run.run_id for run in service.list_runs()}
    service.process_running = lambda: True
    with patch.object(service, "_iter_gameplay_save_files", side_effect=AssertionError("save inspection")):
        stored = service.ensure_pak_managed(pak, expected_seed_id=active.seed_id)
    assert stored.run.run_id == active.run_id
    assert {run.run_id for run in service.list_runs()} == before


def test_context_stops_after_error_and_releases_busy_state():
    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_run_action_running = False
    ctx.bridge_dir = None
    ctx._is_ap_connected = Mock(return_value=False)
    service = Mock()
    service.delete_inactive_run.side_effect = [SimpleNamespace(errors=()), KH3RunManagerError("changed")]
    ctx._get_run_manager_service = Mock(return_value=service)
    with patch("worlds.kh3.Client.is_process_running", return_value=False):
        asyncio.run(ctx._delete_inactive_kh3_runs_action(("first", "second", "third"), "active"))
    assert [call.args[0] for call in service.delete_inactive_run.call_args_list] == ["first", "second"]
    assert all(call.kwargs["expected_active_run_id"] == "active" for call in service.delete_inactive_run.call_args_list)
    assert not ctx.kh3_run_action_running
    ctx._is_ap_connected.return_value = True
    service.reset_mock()
    service.delete_inactive_run.side_effect = None
    service.delete_inactive_run.return_value = SimpleNamespace(errors=())
    asyncio.run(ctx._delete_inactive_kh3_runs_action(("first",), "active"))
    service.delete_inactive_run.assert_called_once()


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("bulk", [False, True])
def test_stale_active_marker_does_not_block_stored_run_deletion_or_clear_current_client(tmp_path, connected, bulk):
    service = KH3RunManagerService(
        run_root=tmp_path / "runs", bridge_root=tmp_path / "bridge",
        mods_dir=tmp_path / "game" / "mods", save_dir=tmp_path / "saves",
        process_running=lambda: True, platform="egs",
    )
    active = service.import_pak(fixtures._write_seed_pak(
        service.mods_dir, seed="22222222222222222222", player=1, slot="Current"))
    inactive = service.import_pak(fixtures._write_seed_pak(
        tmp_path / "downloads", seed="11111111111111111111", player=1, slot="Old"))
    another = service.import_pak(fixtures._write_seed_pak(
        tmp_path / "downloads", seed="33333333333333333333", player=1, slot="Another"))
    service._write_active_run_id(inactive.run_id)
    assert not service.get_run(inactive.run_id).active
    assert service.get_run(active.run_id).active
    active_bridge = service.bridge_root / active.bridge_dir_name
    old_bridge = service.bridge_root / inactive.bridge_dir_name
    for bridge in (active_bridge, old_bridge):
        bridge.mkdir(parents=True)
        (bridge / "state.json").write_text("{}")
    service.save_dir.mkdir(parents=True)
    live_save = service.save_dir / "KHIII_slot1.bin"
    live_save.write_bytes(b"synthetic current AP save")

    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = active_bridge
    ctx.kh3_active_connection_profile_run_id = active.run_id
    ctx.kh3_run_action_running = False
    ctx._get_run_manager_service = Mock(return_value=service)
    ctx._is_ap_connected = Mock(return_value=connected)
    ctx.stop_integrated_bridge = Mock(side_effect=AssertionError("stopped current watcher"))
    ctx._apply_deleted_active_kh3_run = Mock(side_effect=AssertionError("cleared current client state"))
    with patch("worlds.kh3.Client.is_process_running", return_value=True), patch.object(
        service, "_iter_gameplay_save_files", side_effect=AssertionError("inspected live saves"),
    ):
        if bulk:
            asyncio.run(ctx._delete_kh3_runs_action((inactive.run_id, another.run_id)))
        else:
            asyncio.run(ctx._delete_kh3_run_action(inactive.run_id))

    assert not inactive.run_dir.exists() and not old_bridge.exists()
    assert another.run_dir.exists() is not bulk
    assert active.run_dir.exists() and active.installed_pak_path.exists() and active_bridge.exists()
    assert live_save.read_bytes() == b"synthetic current AP save"
    assert service.get_run(active.run_id).active
    assert service._read_active_run_id() is None
    assert ctx.bridge_dir == active_bridge
    assert ctx.kh3_active_connection_profile_run_id == active.run_id
    assert not ctx.kh3_run_action_running
    ctx.stop_integrated_bridge.assert_not_called()
    ctx._apply_deleted_active_kh3_run.assert_not_called()


def test_dormant_marker_without_installed_seed_does_not_require_active_deletion_permission(runs):
    service, active, inactive = runs
    active.installed_pak_path.unlink()
    service._write_active_run_id(inactive.run_id)
    service.process_running = lambda: True
    with patch.object(service, "_iter_gameplay_save_files", side_effect=AssertionError("live save inspection")):
        result = service.delete_run(inactive.run_id)
    assert result.removed_run
    assert not result.cleared_active_profile
    assert not result.removed_installed_pak and result.removed_active_save_files == 0
    assert service._read_active_run_id() is None
    assert active.run_dir.exists()


def test_stale_marker_does_not_bypass_live_bridge_protection(runs):
    service, _, inactive = runs
    service._write_active_run_id(inactive.run_id)
    bridge = service.bridge_root / inactive.bridge_dir_name
    bridge.mkdir(parents=True)
    with pytest.raises(KH3RunManagerError, match="client is still using this run's bridge"):
        service.delete_run(inactive.run_id, active_bridge_dir=bridge)
    assert inactive.run_dir.exists() and bridge.exists()
