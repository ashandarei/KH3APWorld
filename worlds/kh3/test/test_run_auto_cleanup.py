"""Opt-in cleanup must preserve the active run and follow successful starts only."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ..Client import KH3Context
from ..client_support.run_manager import KH3RunActivationResult, KH3RunManagerError
from . import test_run_bulk_delete
from . import test_run_manager as fixtures

runs = test_run_bulk_delete.runs


@pytest.mark.parametrize("enabled", [False, True])
def test_auto_cleanup_only_deletes_stored_runs_when_enabled(runs, enabled):
    service, active, inactive = runs
    ctx = KH3Context.__new__(KH3Context)
    ctx.auto_delete_old_kh3_runs = enabled
    ctx.kh3_run_action_running = True
    ctx.kh3_active_connection_profile_run_id = active.run_id
    ctx.bridge_dir = service.bridge_root / active.bridge_dir_name
    ctx._get_run_manager_service = Mock(return_value=service)
    service.process_running = lambda: True
    with patch.object(service, "_iter_gameplay_save_files", side_effect=AssertionError("live save inspection")):
        asyncio.run(ctx._auto_delete_old_kh3_runs_after_start(active.run_id))
    assert inactive.run_dir.exists() is not enabled
    assert active.run_dir.exists() and active.installed_pak_path.exists()
    assert ctx.kh3_run_action_running  # The enclosing start action still owns the lock.


@pytest.mark.parametrize("reason", ["active_changed", "profile_changed", "not_locked"])
def test_auto_cleanup_refuses_unprotected_run(runs, reason):
    service, active, inactive = runs
    ctx = KH3Context.__new__(KH3Context)
    ctx.auto_delete_old_kh3_runs = True
    ctx.kh3_run_action_running = reason != "not_locked"
    ctx.kh3_active_connection_profile_run_id = "changed" if reason == "profile_changed" else active.run_id
    ctx.bridge_dir = None
    ctx._get_run_manager_service = Mock(return_value=service)
    expected = "previous" if reason == "active_changed" else active.run_id
    asyncio.run(ctx._auto_delete_old_kh3_runs_after_start(expected))
    assert inactive.run_dir.exists() and active.run_dir.exists()


def test_setting_persists_without_deleting_anything():
    ctx = KH3Context.__new__(KH3Context)
    ctx._get_run_manager_service = Mock(side_effect=AssertionError("unexpected cleanup"))
    with patch("worlds.kh3.Client.Utils.persistent_store") as persist:
        ctx.set_auto_delete_old_kh3_runs(True)
        persist.assert_called_once_with("kh3_client", "auto_delete_old_kh3_runs", True)
        ctx.set_auto_delete_old_kh3_runs(False)
        assert persist.call_args.args == ("kh3_client", "auto_delete_old_kh3_runs", False)
    assert not ctx.auto_delete_old_kh3_runs


@pytest.mark.parametrize("outcome", ["success", "failed", "uninitialized", "unchanged"])
def test_switch_cleanup_waits_for_successful_new_run(tmp_path, outcome):
    fixture = fixtures.KH3RunManagerContextTest()
    run = fixture._run(tmp_path)
    result = KH3RunActivationResult(run, "old", outcome != "unchanged", 1, 0, tmp_path / "saves")
    ctx, service = fixture._context(tmp_path, result)
    ctx.auto_delete_old_kh3_runs = True
    service.list_runs.return_value = [run, SimpleNamespace(run_id="old", managed=True, active=False)]
    service.delete_inactive_run.return_value = SimpleNamespace(errors=())
    if outcome == "failed":
        service.activate.side_effect = KH3RunManagerError("activation failed")
    ctx.try_initialize_offline_bridge_from_installed_pak.return_value = outcome != "uninitialized"
    with patch("worlds.kh3.Client.is_process_running", return_value=False):
        asyncio.run(ctx._activate_kh3_run_action(run.run_id))
    if outcome == "success":
        service.delete_inactive_run.assert_called_once_with(
            "old", expected_active_run_id=run.run_id, active_bridge_dir=ctx.bridge_dir,
            current_platform_only=True,
        )
    else:
        service.delete_inactive_run.assert_not_called()
    assert not ctx.kh3_run_action_running


def test_auto_cleanup_stops_on_error_without_failing_new_run():
    ctx = KH3Context.__new__(KH3Context)
    ctx.auto_delete_old_kh3_runs = True
    ctx.kh3_run_action_running = True
    ctx.bridge_dir = None
    service = Mock()
    service.list_runs.return_value = [
        SimpleNamespace(run_id="active", active=True, managed=True),
        SimpleNamespace(run_id="old", active=False, managed=True),
        SimpleNamespace(run_id="other", active=False, managed=True),
    ]
    service.delete_inactive_run.side_effect = KH3RunManagerError("locked file")
    ctx._get_run_manager_service = Mock(return_value=service)
    asyncio.run(ctx._auto_delete_old_kh3_runs_after_start("active"))
    service.delete_inactive_run.assert_called_once()
    assert ctx.kh3_run_action_running


@pytest.mark.parametrize("outcome", ["success", "failed", "uninitialized", "already_installed"])
def test_seed_preparation_only_cleans_after_success(outcome):
    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_run_action_running = False
    ctx.has_installed_kh3_ap_seed_pak = Mock(return_value=outcome == "already_installed")
    service = Mock()
    service.has_container_source.return_value = True
    service.prepare_missing_installed_pak.return_value = SimpleNamespace(
        installed_run=SimpleNamespace(run_id="new"),
    )
    if outcome == "failed":
        service.prepare_missing_installed_pak.side_effect = RuntimeError("generation failed")
    ctx._get_seed_import_service = Mock(return_value=service)
    ctx._apply_prepared_seed_pak = Mock(return_value=outcome != "uninitialized")
    ctx._ensure_olympus_coliseum_reward_companion = Mock()
    ctx._auto_delete_old_kh3_runs_after_start = AsyncMock()
    asyncio.run(ctx._prepare_missing_seed_pak(trigger="launching KH3"))
    if outcome == "success":
        ctx._auto_delete_old_kh3_runs_after_start.assert_awaited_once_with("new")
    else:
        ctx._auto_delete_old_kh3_runs_after_start.assert_not_awaited()
    assert not ctx.kh3_run_action_running
