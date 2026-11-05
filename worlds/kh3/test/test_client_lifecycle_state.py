"""Shared lifecycle state and save-directory restart ownership."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..Client import KH3Context
from ..client_support.lifecycle_service import BridgeLifecycleService, LifecycleState, SaveDirectoryRestartCallbacks


def lifecycle_fixture(**overrides):
    info, warning = Mock(), Mock()
    state = LifecycleState()
    service = BridgeLifecycleService(
        SimpleNamespace(),
        server_loop=AsyncMock(),
        bridge_watcher=AsyncMock(),
        integrated_bridge_worker=Mock(),
        log_info=info,
        log_warning=warning,
        log_exception=Mock(),
        state=state,
    )
    scheduled = []
    callbacks = SaveDirectoryRestartCallbacks(
        enabled=lambda: True,
        bridge=lambda: Path("bridge"),
        game_running=lambda: False,
        wait_shutdown=AsyncMock(),
        start_bridge=Mock(),
        save_dir=lambda: Path("new-save-context"),
        schedule=scheduled.append,
    )
    return service, replace(callbacks, **overrides), scheduled, info, warning


def test_pre_service_public_state_is_adopted_without_losing_tasks_or_paths():
    ctx = KH3Context.__new__(KH3Context)
    task, stop = Mock(spec=asyncio.Task), threading.Event()
    ctx.integrated_bridge_task = task
    ctx.integrated_bridge_stop_event = stop
    ctx.integrated_bridge_dir = Path("bridge")
    ctx.integrated_bridge_save_dir = Path("save")
    initial = ctx._lifecycle_state()
    service = ctx._get_lifecycle_service()
    assert service.state is initial
    assert ctx._lifecycle_state() is initial
    assert service.state.integrated_bridge_task is task
    assert service.state.integrated_bridge_stop_event is stop
    assert service.state.integrated_bridge_dir == Path("bridge")
    assert service.state.integrated_bridge_save_dir == Path("save")
    service.state.integrated_bridge_dir = Path("other")
    assert ctx.integrated_bridge_dir == Path("other")
    ctx.integrated_bridge_task = None
    assert service.state.integrated_bridge_task is None


def test_current_service_state_survives_service_detachment():
    ctx = KH3Context.__new__(KH3Context)
    initial = ctx._lifecycle_state()
    service, _, _, _, _ = lifecycle_fixture()
    service.state.integrated_bridge_dir = Path("current")
    ctx.lifecycle_service = service
    assert ctx._lifecycle_state() is service.state
    del ctx.lifecycle_service
    assert ctx.integrated_bridge_dir == Path("current")
    assert ctx._lifecycle_state() is service.state
    assert ctx._lifecycle_state() is not initial


def test_partial_contexts_do_not_share_lifecycle_state():
    first, second = KH3Context.__new__(KH3Context), KH3Context.__new__(KH3Context)
    first.integrated_bridge_dir = Path("first")
    assert second.integrated_bridge_dir is None
    assert first._lifecycle_state() is not second._lifecycle_state()


@pytest.mark.parametrize(
    "enabled,bridge,running", [(False, Path("bridge"), False), (True, None, False), (True, Path("bridge"), True)]
)
def test_restart_is_not_scheduled_without_a_stopped_game_and_active_bridge(enabled, bridge, running):
    service, callbacks, scheduled, _, _ = lifecycle_fixture(
        enabled=lambda: enabled, bridge=lambda: bridge, game_running=lambda: running
    )
    service.request_save_directory_restart(callbacks)
    assert not scheduled
    assert not service.state.save_directory_restart_pending


def test_restart_without_an_event_loop_leaves_no_pending_request():
    service, callbacks, scheduled, info, _ = lifecycle_fixture()
    service.request_save_directory_restart(callbacks)
    assert not scheduled
    assert not service.state.save_directory_restart_pending
    assert "no asyncio loop" in info.call_args.args[0]


def test_restart_coalesces_changes_and_uses_the_latest_save_context():
    async def run_case():
        release = asyncio.Event()
        context = SimpleNamespace(save_dir=Path("first"))
        events = []

        async def wait():
            events.append("stop")
            await release.wait()

        service, callbacks, scheduled, info, _ = lifecycle_fixture(
            wait_shutdown=wait,
            start_bridge=lambda: events.append(("start", context.save_dir)),
            save_dir=lambda: context.save_dir,
        )
        service.request_save_directory_restart(callbacks)
        task = asyncio.create_task(scheduled[0])
        await asyncio.sleep(0)
        context.save_dir = Path("latest")
        service.request_save_directory_restart(callbacks)
        assert len(scheduled) == 1
        assert service.state.save_directory_restart_pending
        assert events == ["stop"]
        release.set()
        await task
        assert events == ["stop", ("start", Path("latest"))]
        assert info.call_args.args[1] == Path("latest")
        assert not service.state.save_directory_restart_pending

    asyncio.run(run_case())


def test_game_start_during_shutdown_prevents_restart_and_releases_pending_state():
    async def run_case():
        running = False

        async def wait():
            nonlocal running
            running = True

        service, callbacks, scheduled, _, warning = lifecycle_fixture(wait_shutdown=wait, game_running=lambda: running)
        service.request_save_directory_restart(callbacks)
        await scheduled[0]
        callbacks.start_bridge.assert_not_called()
        assert "started while" in warning.call_args.args[0]
        assert not service.state.save_directory_restart_pending

    asyncio.run(run_case())


@pytest.mark.parametrize("error", [RuntimeError("still active"), OSError("failed stop"), asyncio.CancelledError()])
def test_shutdown_failure_or_cancellation_releases_restart_state(error):
    async def run_case():
        service, callbacks, scheduled, _, warning = lifecycle_fixture(wait_shutdown=AsyncMock(side_effect=error))
        service.request_save_directory_restart(callbacks)
        if isinstance(error, RuntimeError):
            await scheduled[0]
            warning.assert_called_once()
        else:
            with pytest.raises(type(error)):
                await scheduled[0]
        callbacks.start_bridge.assert_not_called()
        assert not service.state.save_directory_restart_pending

    asyncio.run(run_case())


def test_failed_bridge_start_logs_and_releases_restart_state():
    async def run_case():
        service, callbacks, scheduled, _, warning = lifecycle_fixture(
            start_bridge=Mock(side_effect=RuntimeError("failed start"))
        )
        service.request_save_directory_restart(callbacks)
        await scheduled[0]
        assert "failed start" in str(warning.call_args.args[1])
        assert not service.state.save_directory_restart_pending

    asyncio.run(run_case())
