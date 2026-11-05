"""Connected-run actions preserve watcher and action ownership."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support.connected_run import ConnectedRunCallbacks, ConnectedRunService, SuspensionCallbacks
from ..client_support.run_actions import ActiveRunSaves
from ..client_support.run_manager import KH3RunManagerError
from ..client_support.seed_import_service import KH3SeedImportError, KH3SeedPreparationResult


@pytest.fixture
def actions():
    manager = Mock()
    run = SimpleNamespace(run_id="run", seed_id="seed", slot_name="slot", player=1)
    manager.suspend_installed_run.return_value = SimpleNamespace(
        run=run, save_run=None, saved_current_files=2, warnings=()
    )
    callbacks = ConnectedRunCallbacks(
        begin=Mock(return_value=True),
        finish=Mock(),
        warn=Mock(),
        connected=Mock(return_value=True),
        disconnect=AsyncMock(),
        ensure_companion=Mock(),
        cleanup_old_runs=AsyncMock(),
        finish_setup=Mock(),
    )
    suspension = SuspensionCallbacks(
        bridge_dir=Mock(return_value=Path("old-bridge")),
        saves=Mock(return_value=ActiveRunSaves(Path("selected-saves"), "old-seed", "old-run")),
        game_running=Mock(return_value=False),
        stop_bridge=Mock(),
        wait_bridge=AsyncMock(),
        restore_bridge=Mock(),
        apply=Mock(),
    )
    return ConnectedRunService(lambda: manager, callbacks), manager, callbacks, suspension, run


def test_suspension_waits_for_the_watcher_before_mutating_files(actions):
    service, manager, callbacks, suspension, _ = actions

    async def run_case():
        waiting, release = asyncio.Event(), asyncio.Event()

        async def wait():
            waiting.set()
            await release.wait()

        suspension.wait_bridge.side_effect = wait
        task = asyncio.create_task(service.suspend(reason="new room", suspension=suspension))
        await waiting.wait()
        suspension.stop_bridge.assert_called_once()
        manager.suspend_installed_run.assert_not_called()
        suspension.apply.assert_not_called()
        release.set()
        assert await task

    asyncio.run(run_case())
    manager.suspend_installed_run.assert_called_once_with(
        save_dir_override=Path("selected-saves"), active_save_seed_id="old-seed", active_save_run_id="old-run"
    )
    suspension.apply.assert_called_once_with(manager.suspend_installed_run.return_value, Path("selected-saves"))
    suspension.restore_bridge.assert_not_called()
    callbacks.finish.assert_called_once()


@pytest.mark.parametrize("error", [KH3RunManagerError("cannot preserve"), OSError("locked")])
@pytest.mark.parametrize("previous_bridge", [Path("old-bridge"), None])
def test_failed_suspension_restores_only_the_previous_watcher(actions, error, previous_bridge):
    service, manager, callbacks, suspension, _ = actions
    suspension.bridge_dir.return_value = previous_bridge
    manager.suspend_installed_run.side_effect = error
    assert not asyncio.run(service.suspend(reason="new room", suspension=suspension))
    suspension.apply.assert_not_called()
    if previous_bridge is None:
        suspension.restore_bridge.assert_not_called()
    else:
        suspension.restore_bridge.assert_called_once_with(previous_bridge)
    callbacks.warn.assert_called_once()
    callbacks.finish.assert_called_once()


def test_running_game_rejects_suspension_before_stopping_the_watcher(actions):
    service, manager, callbacks, suspension, _ = actions
    suspension.game_running.return_value = True
    assert not asyncio.run(service.suspend(reason="new room", suspension=suspension))
    manager.suspend_installed_run.assert_not_called()
    suspension.stop_bridge.assert_not_called()
    suspension.restore_bridge.assert_not_called()
    callbacks.finish.assert_called_once()


@pytest.mark.parametrize("action", ["suspend", "bootstrap", "companion"])
def test_busy_actions_do_not_release_another_actions_lock(actions, action):
    service, manager, callbacks, suspension, _ = actions
    callbacks.begin.return_value = False
    prepare, apply = Mock(), Mock()
    if action == "suspend":
        asyncio.run(service.suspend(reason="new room", suspension=suspension))
    elif action == "bootstrap":
        asyncio.run(service.bootstrap({}, prepare=prepare, apply=apply))
        callbacks.disconnect.assert_awaited_once()
    else:
        asyncio.run(service.prepare_companion({}))
        callbacks.finish_setup.assert_called_once_with({})
    callbacks.finish.assert_not_called()
    callbacks.ensure_companion.assert_not_called()
    manager.suspend_installed_run.assert_not_called()
    prepare.assert_not_called()


@pytest.mark.parametrize("action", ["suspend", "bootstrap", "companion"])
def test_cancelled_actions_release_their_own_lock_and_propagate(actions, action):
    service, manager, callbacks, suspension, _ = actions
    cancellation = asyncio.CancelledError()
    if action == "suspend":
        suspension.wait_bridge.side_effect = cancellation
        task = service.suspend(reason="new room", suspension=suspension)
    elif action == "bootstrap":
        task = service.bootstrap({}, prepare=Mock(side_effect=cancellation), apply=Mock())
    else:
        callbacks.ensure_companion.side_effect = cancellation
        task = service.prepare_companion({})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(task)
    callbacks.finish.assert_called_once()
    callbacks.finish_setup.assert_not_called()
    callbacks.disconnect.assert_not_called()
    manager.suspend_installed_run.assert_not_called()


@pytest.mark.parametrize("connected", [True, False])
def test_bootstrap_applies_preparation_before_finishing_and_checks_connection_after_io(actions, connected):
    service, _, callbacks, _, run = actions
    events = []
    prepared = KH3SeedPreparationResult("installed", installed_run=run)

    def prepare():
        events.append("prepare")
        callbacks.connected.return_value = connected
        return prepared

    callbacks.ensure_companion.side_effect = lambda: events.append("companion")
    callbacks.finish.side_effect = lambda: events.append("finish")
    callbacks.finish_setup.side_effect = lambda args: events.append("setup")
    apply = Mock(side_effect=lambda result: events.append("apply"))
    asyncio.run(service.bootstrap({"slot": 1}, prepare=prepare, apply=apply))
    assert events == ["prepare", "companion", "apply", "finish"] + (["setup"] if connected else [])
    apply.assert_called_once_with(prepared)
    if connected:
        callbacks.cleanup_old_runs.assert_awaited_once_with("run")
    else:
        callbacks.cleanup_old_runs.assert_not_called()
    callbacks.disconnect.assert_not_called()


@pytest.mark.parametrize("error", [KH3SeedImportError("bad seed"), KH3RunManagerError("bad run"), OSError("disk")])
def test_failed_connected_seed_preparation_finishes_then_disconnects(actions, error):
    service, _, callbacks, _, _ = actions
    events = []
    callbacks.finish.side_effect = lambda: events.append("finish")
    callbacks.disconnect.side_effect = lambda: events.append("disconnect")
    apply = Mock()
    asyncio.run(service.bootstrap({}, prepare=Mock(side_effect=error), apply=apply))
    assert events == ["finish", "disconnect"]
    apply.assert_not_called()
    callbacks.finish_setup.assert_not_called()
    callbacks.warn.assert_called_once()


def test_companion_preparation_that_loses_connection_does_not_finish_setup(actions):
    service, _, callbacks, _, _ = actions
    callbacks.ensure_companion.side_effect = lambda: setattr(callbacks.connected, "return_value", False)
    asyncio.run(service.prepare_companion({}))
    callbacks.finish.assert_called_once()
    callbacks.finish_setup.assert_not_called()
