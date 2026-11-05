"""Run actions recover according to whether the file transaction completed."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ..client_support.run_actions import ActiveRunSaves, RunActionCallbacks, RunActionService, RunSelection
from ..client_support.run_manager import KH3RunActivationResult, KH3RunDeleteResult, KH3RunManagerError
from . import test_run_manager as run_manager_tests


@pytest.fixture
def action(tmp_path):
    run = run_manager_tests.KH3RunManagerContextTest._run(tmp_path)
    saves = ActiveRunSaves(tmp_path / "saves", "previous-seed", "previous-run")
    activation = KH3RunActivationResult(run, saves.run_id, True, 3, 4, saves.directory)
    deletion = KH3RunDeleteResult(run.run_id, True, 1, True, 2, True)
    client = RunActionCallbacks(
        set_status=Mock(),
        active_saves=Mock(return_value=saves),
        stop_bridge=AsyncMock(),
        restore_bridge=Mock(),
        select_run=Mock(),
        initialize_run=Mock(return_value=True),
        retain_activated_run=Mock(),
        cleanup_old_runs=AsyncMock(),
        show_switch_warning=Mock(),
        is_connected=Mock(return_value=False),
        delete_run=Mock(return_value=deletion),
        clear_deleted_run=Mock(),
    )
    manager = Mock()
    manager.activate.return_value = activation
    manager.get_run.return_value = run
    game_running = Mock(return_value=False)
    service = RunActionService(run_manager=lambda: manager, client=client, game_running=game_running)
    return SimpleNamespace(
        service=service,
        client=client,
        manager=manager,
        run=run,
        saves=saves,
        selection=RunSelection(tmp_path / "bridge" / "previous", "previous-run"),
        activation=activation,
        deletion=deletion,
        game_running=game_running,
    )


def test_activation_orders_watcher_files_client_and_cleanup(action):
    timeline = Mock()
    for name, operation in (
        ("stop", action.client.stop_bridge),
        ("files", action.manager.activate),
        ("select", action.client.select_run),
        ("initialize", action.client.initialize_run),
        ("cleanup", action.client.cleanup_old_runs),
    ):
        timeline.attach_mock(operation, name)

    asyncio.run(action.service.activate(action.run.run_id, action.selection))

    assert [call[0] for call in timeline.mock_calls] == ["stop", "files", "select", "initialize", "cleanup"]
    action.client.stop_bridge.assert_awaited_once_with(disconnect=True)
    action.manager.activate.assert_called_once_with(
        action.run.run_id,
        save_dir_override=action.saves.directory,
        active_save_seed_id=action.saves.seed_id,
        active_save_run_id=action.saves.run_id,
    )


@pytest.mark.parametrize("stage", ["stop_bridge", "activate", "select_run", "initialize_run", "cleanup_old_runs"])
@pytest.mark.parametrize("error_type", [KH3RunManagerError, RuntimeError])
def test_activation_recovery_never_restarts_old_bridge_after_files_changed(action, stage, error_type):
    operation = action.manager.activate if stage == "activate" else getattr(action.client, stage)
    operation.side_effect = error_type("injected failure")

    asyncio.run(action.service.activate(action.run.run_id, action.selection))

    if stage == "stop_bridge":
        action.manager.activate.assert_not_called()
        action.client.restore_bridge.assert_not_called()
        action.client.retain_activated_run.assert_not_called()
    elif stage == "activate":
        action.client.restore_bridge.assert_called_once_with(action.selection.bridge_dir)
        action.client.retain_activated_run.assert_not_called()
    else:
        action.client.restore_bridge.assert_not_called()
        action.client.retain_activated_run.assert_called_once_with(action.activation)


def test_activation_failure_without_a_previous_bridge_does_not_start_one(action):
    action.manager.activate.side_effect = KH3RunManagerError("failed")
    asyncio.run(action.service.activate(action.run.run_id, RunSelection(None, None)))
    action.client.restore_bridge.assert_not_called()


@pytest.mark.parametrize("stage", ["stop_bridge", "delete_run", "clear_deleted_run"])
@pytest.mark.parametrize("error_type", [KH3RunManagerError, RuntimeError])
def test_deletion_restarts_watcher_only_before_file_deletion_completed(action, stage, error_type):
    getattr(action.client, stage).side_effect = error_type("injected failure")

    assert asyncio.run(action.service.delete(action.run.run_id, action.selection)) is False

    action.client.stop_bridge.assert_awaited_once_with(disconnect=False)
    if stage == "delete_run":
        action.client.restore_bridge.assert_called_once_with(action.selection.bridge_dir)
        action.client.clear_deleted_run.assert_not_called()
    else:
        action.client.restore_bridge.assert_not_called()
    if stage == "stop_bridge":
        action.client.delete_run.assert_not_called()


def test_connection_during_watcher_shutdown_blocks_file_deletion(action):
    async def connect_while_stopping(*, disconnect):
        action.client.is_connected.return_value = True

    action.client.stop_bridge.side_effect = connect_while_stopping
    assert asyncio.run(action.service.delete(action.run.run_id, action.selection)) is False

    action.client.delete_run.assert_not_called()
    action.client.restore_bridge.assert_called_once_with(action.selection.bridge_dir)


def test_partial_deletion_report_clears_committed_profile_without_restarting_old_watcher(action, caplog):
    action.client.delete_run.return_value = replace(action.deletion, errors=("stored bridge is locked",))

    assert asyncio.run(action.service.delete(action.run.run_id, action.selection)) is False

    action.client.clear_deleted_run.assert_called_once_with(action.run.run_id, action.saves.directory)
    action.client.restore_bridge.assert_not_called()
    assert "stored bridge is locked" in caplog.text


def test_delete_selected_unmanaged_run_uses_bridge_identity_without_reading_active_saves(action):
    run = replace(action.run, managed=False, active=False, installed=False)
    action.manager.get_run.return_value = run
    action.client.delete_run.return_value = replace(action.deletion, cleared_active_profile=False)

    assert asyncio.run(action.service.delete(run.run_id, RunSelection(run.bridge_dirs[0], None))) is True

    action.client.active_saves.assert_not_called()
    action.client.stop_bridge.assert_awaited_once_with(disconnect=False)
    action.client.delete_run.assert_called_once_with(run.run_id, allow_active=True)


@pytest.mark.parametrize("stage", ["files", "client"])
def test_context_switch_releases_action_lock_after_failure(tmp_path, stage):
    fixture = run_manager_tests.KH3RunManagerContextTest()
    run = fixture._run(tmp_path)
    result = KH3RunActivationResult(run, "old", True, 0, 0, tmp_path / "saves")
    ctx, manager = fixture._context(tmp_path, result)
    previous_bridge = ctx.bridge_dir
    ctx._retain_activated_run_identity_after_error = Mock()
    if stage == "files":
        manager.activate.side_effect = KH3RunManagerError("failed file operation")
    else:
        ctx._apply_kh3_run_activation.side_effect = RuntimeError("failed client reinitialization")
    with patch("worlds.kh3.Client.is_process_running", return_value=False):
        asyncio.run(ctx._activate_kh3_run_action(run.run_id))

    assert not ctx.kh3_run_action_running
    assert ctx.kh3_run_action_label == ""
    if stage == "files":
        assert ctx.bridge_dir == previous_bridge
        ctx.start_integrated_bridge.assert_called_once()
        ctx._retain_activated_run_identity_after_error.assert_not_called()
    else:
        ctx.start_integrated_bridge.assert_not_called()
        ctx._retain_activated_run_identity_after_error.assert_called_once_with(result)


@pytest.mark.parametrize("stage", ["files", "client"])
def test_context_delete_releases_action_lock_after_failure(tmp_path, stage):
    fixture = run_manager_tests.KH3RunManagerContextTest()
    run = fixture._run(tmp_path)
    activation = KH3RunActivationResult(run, "old", True, 0, 0, tmp_path / "saves")
    ctx, manager = fixture._context(tmp_path, activation)
    previous_bridge = ctx.bridge_dir
    ctx._is_ap_connected = Mock(return_value=False)
    ctx.stop_integrated_bridge = Mock()
    ctx.clear_save_seed_associations_for_targets = Mock()
    ctx._apply_deleted_active_kh3_run = Mock()
    manager.get_run.return_value = run
    manager.delete_run.return_value = KH3RunDeleteResult(run.run_id, True, 1, cleared_active_profile=True)
    if stage == "files":
        manager.delete_run.side_effect = KH3RunManagerError("failed file operation")
    else:
        ctx._apply_deleted_active_kh3_run.side_effect = RuntimeError("failed client cleanup")
    with patch("worlds.kh3.Client.is_process_running", return_value=False):
        asyncio.run(ctx._delete_kh3_run_action(run.run_id))

    assert not ctx.kh3_run_action_running
    assert ctx.kh3_run_action_label == ""
    if stage == "files":
        assert ctx.bridge_dir == previous_bridge
        ctx.start_integrated_bridge.assert_called_once()
        ctx._apply_deleted_active_kh3_run.assert_not_called()
    else:
        ctx.start_integrated_bridge.assert_not_called()
        ctx._apply_deleted_active_kh3_run.assert_called_once()


def test_failed_association_cleanup_still_clears_deleted_client_state(tmp_path):
    ctx = run_manager_tests.KH3Context.__new__(run_manager_tests.KH3Context)
    ctx.clear_save_seed_associations_for_targets = Mock(side_effect=OSError("settings unavailable"))
    ctx._apply_deleted_active_kh3_run = Mock()

    ctx._clear_deleted_kh3_run("removed-run", tmp_path)

    ctx.clear_save_seed_associations_for_targets.assert_called_once_with([tmp_path], expected_run_id="removed-run")
    ctx._apply_deleted_active_kh3_run.assert_called_once()
