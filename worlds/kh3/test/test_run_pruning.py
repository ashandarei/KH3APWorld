"""Pruning rechecks selection and preferences between stored-run deletions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..client_support.run_actions import RunSelection
from ..client_support.run_pruning import RunPruneCallbacks, RunPruningService


@pytest.fixture
def pruning():
    manager = Mock()
    manager.list_runs.return_value = [
        SimpleNamespace(run_id="active", active=True, managed=True),
        SimpleNamespace(run_id="old", active=False, managed=True),
        SimpleNamespace(run_id="other", active=False, managed=True),
        SimpleNamespace(run_id="unmanaged", active=False, managed=False),
    ]
    manager.matches_platform.side_effect = lambda run: run.run_id != "other"
    manager.delete_inactive_run.return_value = SimpleNamespace(errors=())
    state = SimpleNamespace(selection=RunSelection(None, "active"), enabled=True)
    status = Mock()
    service = RunPruningService(
        lambda: manager,
        RunPruneCallbacks(
            selection=lambda: state.selection,
            set_status=status,
            auto_enabled=lambda: state.enabled,
        ),
    )
    return service, manager, state, status


def test_automatic_only_prunes_managed_runs_for_the_current_platform(pruning):
    service, manager, _, status = pruning
    asyncio.run(service.automatic("active"))
    manager.list_runs.assert_called_once_with(force_refresh=True)
    manager.delete_inactive_run.assert_called_once_with(
        "old",
        expected_active_run_id="active",
        active_bridge_dir=None,
        current_platform_only=True,
    )
    status.assert_called_once_with("Deleting old run 1 of 1...")


@pytest.mark.parametrize("change", ["disabled", "selection"])
def test_automatic_stops_when_preference_or_selection_changes_after_a_delete(pruning, change):
    service, manager, state, _ = pruning
    manager.matches_platform.return_value = True
    manager.matches_platform.side_effect = None

    def delete(*args, **kwargs):
        if change == "disabled":
            state.enabled = False
        else:
            state.selection = RunSelection(None, "replacement")
        return SimpleNamespace(errors=())

    manager.delete_inactive_run.side_effect = delete
    asyncio.run(service.automatic("active"))
    assert manager.delete_inactive_run.call_count == 1


def test_selected_targets_are_deduplicated_in_original_order(pruning):
    service, manager, _, _ = pruning
    asyncio.run(service.selected(("first", "second", "first"), "active"))
    assert [call.args[0] for call in manager.delete_inactive_run.call_args_list] == ["first", "second"]


@pytest.mark.parametrize("method", ["selected", "automatic"])
def test_partial_deletion_stops_without_attempting_another_run(pruning, method):
    service, manager, _, _ = pruning
    manager.matches_platform.side_effect = None
    manager.matches_platform.return_value = True
    manager.delete_inactive_run.return_value = SimpleNamespace(errors=("locked file",))
    task = service.automatic("active") if method == "automatic" else service.selected(("first", "second"), "active")
    asyncio.run(task)
    assert manager.delete_inactive_run.call_count == 1


def test_bulk_action_releases_lock_on_cancellation(pruning):
    from ..Client import KH3Context

    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_run_action_running = False
    ctx._create_run_pruning_service = Mock(return_value=pruning[0])
    pruning[1].delete_inactive_run.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ctx._delete_inactive_kh3_runs_action(("old",), "active"))
    assert not ctx.kh3_run_action_running
    assert ctx.kh3_run_action_label == ""
