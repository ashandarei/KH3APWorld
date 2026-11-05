"""Automatic activation preserves ownership across connection and action changes."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..Client import KH3Context
from ..client_support.connected_run import (
    AutoActivationCallbacks,
    AutoActivationRequest,
    ConnectedRunCallbacks,
    ConnectedRunService,
)
from ..client_support.run_actions import ActiveRunSaves
from ..client_support.run_manager import KH3RunManagerError
from ..client_support.seed_import_service import KH3SeedPreparationResult


@pytest.fixture
def activation():
    state = SimpleNamespace(connected=True, pending=True)
    client = ConnectedRunCallbacks(
        begin=Mock(return_value=True),
        finish=Mock(),
        warn=Mock(),
        connected=lambda: state.connected,
        disconnect=AsyncMock(),
        ensure_companion=Mock(),
        cleanup_old_runs=AsyncMock(),
        finish_setup=Mock(),
    )
    request = AutoActivationRequest(
        Path("seed.pak"),
        "seed",
        1,
        "host:38281",
        "Sora",
        None,
        ActiveRunSaves(Path("configured-saves"), "old-seed", "old-run"),
    )
    callbacks = AutoActivationCallbacks(
        still_connected=lambda: state.connected,
        disable_checks=Mock(),
        wait_bridge=AsyncMock(),
        request=Mock(return_value=request),
        remember_save_owner=Mock(),
        apply=Mock(),
        disconnect_guard=Mock(),
        clear_pending=lambda: setattr(state, "pending", False),
    )
    importer = Mock()
    run = SimpleNamespace(run_id="run", seed_id="seed", slot_name="Sora")
    importer.activate_installed_run_for_connection.return_value = KH3SeedPreparationResult(
        "activated", installed_run=run
    )
    service = ConnectedRunService(Mock(), client)
    return service, importer, client, callbacks, state, run, request


def activate(fixture):
    service, importer, _, callbacks, _, _, _ = fixture
    return service.auto_activate({"slot": 1}, importer=lambda: importer, activation=callbacks)


def test_action_releases_before_setup_but_pending_stays_set_through_the_guard(activation):
    _, importer, client, callbacks, state, run, request = activation

    def finish_setup(args):
        client.finish.assert_called_once()
        callbacks.apply.assert_called_once_with(run)
        callbacks.remember_save_owner.assert_called_once_with(run, request.saves.directory)
        assert state.pending

    client.finish_setup.side_effect = finish_setup
    asyncio.run(activate(activation))
    assert not state.pending
    importer.activate_installed_run_for_connection.assert_called_once_with(
        Path("seed.pak"),
        expected_seed_id="seed",
        expected_slot=1,
        server_address="host:38281",
        connection_slot_name="Sora",
        room_url=None,
        save_dir_override=Path("configured-saves"),
        active_save_seed_id="old-seed",
        active_save_run_id="old-run",
    )


@pytest.mark.parametrize("stage", ["wait", "worker"])
def test_connection_changes_stop_setup_but_completed_transactions_keep_the_save_owner(activation, stage):
    _, importer, client, callbacks, state, run, request = activation
    if stage == "wait":
        callbacks.wait_bridge.side_effect = lambda: setattr(state, "connected", False)
    else:
        importer.activate_installed_run_for_connection.side_effect = lambda *args, **kwargs: (
            setattr(state, "connected", False) or importer.activate_installed_run_for_connection.return_value
        )
    asyncio.run(activate(activation))
    client.finish_setup.assert_not_called()
    callbacks.apply.assert_not_called()
    if stage == "wait":
        importer.activate_installed_run_for_connection.assert_not_called()
        callbacks.remember_save_owner.assert_not_called()
    else:
        callbacks.remember_save_owner.assert_called_once_with(run, request.saves.directory)
    client.finish.assert_called_once()
    assert not state.pending


def test_busy_activation_does_not_release_another_actions_lock(activation):
    _, importer, client, callbacks, state, _, _ = activation
    client.begin.return_value = False
    asyncio.run(activate(activation))
    client.finish.assert_not_called()
    callbacks.disable_checks.assert_not_called()
    importer.activate_installed_run_for_connection.assert_not_called()
    callbacks.disconnect_guard.assert_called_once()
    assert not state.pending


@pytest.mark.parametrize("stage", ["request", "worker", "apply", "setup"])
def test_activation_errors_release_only_once_and_clear_pending(activation, stage):
    _, importer, client, callbacks, state, _, _ = activation
    target = {
        "request": callbacks.request,
        "worker": importer.activate_installed_run_for_connection,
        "apply": callbacks.apply,
        "setup": client.finish_setup,
    }[stage]
    target.side_effect = KH3RunManagerError("failed")
    asyncio.run(activate(activation))
    client.finish.assert_called_once()
    callbacks.disconnect_guard.assert_called_once_with("failed")
    assert not state.pending


@pytest.mark.parametrize("stage", ["wait", "worker"])
def test_cancelled_activation_propagates_and_releases_its_action(activation, stage):
    _, importer, client, callbacks, state, _, _ = activation
    target = callbacks.wait_bridge if stage == "wait" else importer.activate_installed_run_for_connection
    target.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(activate(activation))
    client.finish.assert_called_once()
    callbacks.disconnect_guard.assert_not_called()
    client.finish_setup.assert_not_called()
    assert not state.pending


def test_automatic_activation_uses_configured_saves_instead_of_runtime_inspection(tmp_path):
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_save_dir = tmp_path / "configured"
    ctx.slot, ctx.auth, ctx.server_address = 1, "Sora", "host:38281"
    ctx.connected_kh3_seed_pak_path = Mock(return_value=(tmp_path / "seed.pak", "seed"))
    ctx.save_seed_association_for_dir = Mock(return_value=("old-seed", 2))
    ctx.save_run_association_for_dir = Mock(return_value="old-run")
    ctx.kh3_room_page_url = Mock(return_value=None)
    ctx._get_client_status_presenter = Mock(side_effect=AssertionError("runtime selection is a different workflow"))
    request = ctx._connected_auto_activation_request()
    assert request.saves == ActiveRunSaves(ctx.bridge_save_dir.resolve(), "old-seed", "old-run")
    ctx._get_client_status_presenter.assert_not_called()
