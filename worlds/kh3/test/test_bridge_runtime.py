"""Worker teardown and event-driven polling remain independently testable."""

import asyncio
import threading
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support import bridge_runtime as runtime


@pytest.fixture
def worker(monkeypatch):
    pipe, session, backend, status = Mock(), Mock(), Mock(), Mock()
    backend.run.return_value = 7
    register, unregister = Mock(return_value=pipe), Mock()
    backend_factory, status_factory = Mock(return_value=backend), Mock(return_value=status)
    monkeypatch.setattr(runtime, "register_pipe", register)
    monkeypatch.setattr(runtime, "unregister_pipe", unregister)
    monkeypatch.setattr(runtime, "KH3BridgeSession", Mock(return_value=session))
    monkeypatch.setattr(runtime, "SaveFilesBackend", backend_factory)
    monkeypatch.setattr(runtime, "BridgeRuntimeStatusWriter", status_factory)
    options = runtime.BridgeWorkerOptions(0.125, threading.Event())
    return pipe, session, backend, status, register, unregister, backend_factory, status_factory, options


@pytest.mark.parametrize("stopped", [True, False])
def test_worker_preserves_backend_paths_status_and_pipe_teardown(worker, stopped):
    pipe, session, backend, status, register, unregister, backend_factory, _, options = worker
    stop, notify = threading.Event(), Mock()
    if stopped:
        stop.set()
    result = runtime.run_integrated_bridge_worker(
        Path("bridge"), Path("saves"), stop, options=options, notify_pipe_activity=notify
    )
    assert result == 7
    register.assert_called_once_with(Path("bridge"), options.activity_event, notify)
    pipe.start.assert_called_once()
    session.initialize.assert_called_once_with()
    backend_factory.assert_called_once_with(
        Path("saves"),
        0.125,
        stop_event=stop,
        activity_event=options.activity_event,
    )
    backend.run.assert_called_once_with(session, status)
    assert [call[0] for call in status.method_calls] == ["mark_starting", "mark_running"] + (
        ["mark_stopped"] if stopped else []
    )
    unregister.assert_called_once_with(pipe)


@pytest.mark.parametrize("error", [OSError("backend"), asyncio.CancelledError()])
def test_worker_errors_propagate_and_always_unregister_the_pipe(worker, error):
    pipe, _, backend, status, _, unregister, _, _, options = worker
    backend.run.side_effect = error
    stop = threading.Event()
    stop.set()
    with pytest.raises(type(error)):
        runtime.run_integrated_bridge_worker(
            Path("bridge"), Path("saves"), stop, options=options, notify_pipe_activity=Mock()
        )
    unregister.assert_called_once_with(pipe)
    status.mark_stopped.assert_called_once()
    assert status.mark_error.call_count == (0 if isinstance(error, asyncio.CancelledError) else 1)


def test_pipe_start_failure_unregisters_without_starting_the_backend(worker):
    pipe, _, backend, _, _, unregister, backend_factory, _, options = worker
    pipe.start.side_effect = OSError("pipe")
    with pytest.raises(OSError, match="pipe"):
        runtime.run_integrated_bridge_worker(
            Path("bridge"), Path("saves"), threading.Event(), options=options, notify_pipe_activity=Mock()
        )
    unregister.assert_called_once_with(pipe)
    backend_factory.assert_not_called()
    backend.run.assert_not_called()


def test_invalid_seed_unregisters_pipe_before_starting_backend(worker):
    pipe, session, backend, _, _, unregister, backend_factory, status_factory, options = worker
    session.initialize.side_effect = ValueError("invalid seed")
    with pytest.raises(ValueError, match="invalid seed"):
        runtime.run_integrated_bridge_worker(
            Path("bridge"), Path("saves"), threading.Event(), options=options, notify_pipe_activity=Mock()
        )
    unregister.assert_called_once_with(pipe)
    backend_factory.assert_not_called()
    status_factory.assert_not_called()
    backend.run.assert_not_called()


def test_polling_rechecks_maintenance_steps_and_interval_between_iterations(monkeypatch):
    state = {"exit": False, "publisher": Mock(), "recovery": Mock(), "steps": (), "interval": 0.125}
    previous_publisher, previous_recovery = state["publisher"], state["recovery"]
    next_step = AsyncMock()
    intervals = []

    async def wait(waiter, *, timeout):
        waiter.close()
        intervals.append(timeout)
        if len(intervals) == 1:
            state.update(publisher=Mock(), recovery=Mock(), steps=(("next", next_step),), interval=0.01)
        else:
            state["exit"] = True

    monkeypatch.setattr(runtime.asyncio, "wait_for", wait)
    callbacks = runtime.BridgePollCallbacks(
        exiting=lambda: state["exit"],
        has_bridge=lambda: True,
        interval=lambda: state["interval"],
        steps=lambda: state["steps"],
        publisher=lambda: state["publisher"],
        recovery=lambda: state["recovery"],
        set_activity_notify=Mock(),
    )
    asyncio.run(runtime.watch_bridge(callbacks, profile_scope=lambda name: nullcontext()))
    assert intervals == [0.125, 0.01]
    previous_publisher.retry_failed.assert_called_once()
    previous_recovery.poll.assert_called_once()
    state["publisher"].retry_failed.assert_called_once()
    state["recovery"].poll.assert_called_once()
    next_step.assert_awaited_once()


def test_pipe_notification_wakes_polling_from_another_thread():
    async def run_case():
        entered = asyncio.Event()
        state = {"exit": False, "notify": None, "polls": 0}

        async def step():
            state["polls"] += 1
            if state["polls"] == 1:
                entered.set()
            else:
                state["exit"] = True
                state["notify"]()

        callbacks = runtime.BridgePollCallbacks(
            exiting=lambda: state["exit"],
            has_bridge=lambda: True,
            interval=lambda: 30.0,
            steps=lambda: (("step", step),),
            publisher=lambda: None,
            recovery=lambda: None,
            set_activity_notify=lambda notify: state.update(notify=notify),
        )
        task = asyncio.create_task(runtime.watch_bridge(callbacks, profile_scope=lambda name: nullcontext()))
        await entered.wait()
        try:
            await asyncio.to_thread(state["notify"])
            await asyncio.wait_for(task, timeout=2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert state["polls"] == 2

    asyncio.run(run_case())


def test_unchanged_channels_skip_handlers_and_failed_dispatch_retries(monkeypatch):
    state = {"exit": False, "revision": 1, "waits": 0}
    stable, changed = AsyncMock(), AsyncMock(side_effect=[OSError("busy"), None, None])

    async def wait(waiter, *, timeout):
        waiter.close()
        state["waits"] += 1
        if state["waits"] == 3:
            state["revision"] = 2
        if state["waits"] == 4:
            state["exit"] = True

    monkeypatch.setattr(runtime.asyncio, "wait_for", wait)
    callbacks = runtime.BridgePollCallbacks(
        exiting=lambda: state["exit"], has_bridge=lambda: True, interval=lambda: 1,
        steps=lambda: (("stable", stable), ("changed", changed)),
        publisher=lambda: None, recovery=lambda: None, set_activity_notify=Mock(),
        step_signature=lambda name: 1 if name == "stable" else state["revision"],
    )
    asyncio.run(runtime.watch_bridge(callbacks, profile_scope=lambda _: nullcontext()))
    stable.assert_awaited_once()
    assert changed.await_count == 3  # first failure, retry, then the new revision


def test_backend_dispatches_only_changed_channels_and_new_binding(monkeypatch, tmp_path):
    from .. import ExternalBridge as backend_module

    state = {"cycles": 0, "pid": 1, "checks": 1, "items": 1}
    monkeypatch.setattr(backend_module, "configure_python_profiler", Mock())
    monkeypatch.setattr(backend_module, "record_python_profile_sample", Mock())
    monkeypatch.setattr(backend_module, "transport_for", lambda _: object())
    monkeypatch.setattr(backend_module, "load_optional_json_view", lambda _: {"kh3_process_pid": state["pid"]})
    monkeypatch.setattr(backend_module, "file_signature", lambda p: (
        state["checks"] if p.name == "runtime_check_state.json" else
        state["items"] if p.name == "ap_items.json" else 1))
    backend = backend_module.SaveFilesBackend(tmp_path, reporter=Mock())
    handlers = (
        "_apply_runtime_item_apply_event_if_present", "_consume_runtime_ap_message_result_if_present",
        "_apply_runtime_loot_event_if_present", "_apply_native_notification_if_present", "_sync_native_checks",
    )
    for name in handlers:
        monkeypatch.setattr(backend, name, Mock())
    monkeypatch.setattr(backend, "_should_stop", lambda: state["cycles"] == 4)

    def wait(_):
        state["cycles"] += 1
        if state["cycles"] == 2:
            state.update(checks=2, items=2)
        if state["cycles"] == 3:
            state["pid"] = 2

    monkeypatch.setattr(backend, "_wait_for_next_poll", wait)
    session = Mock(bridge_dir=tmp_path)
    assert backend.run(session) == 0
    assert backend._sync_native_checks.call_count == 3
    assert session.sync_native_receipt_catalog.call_count == 3
    assert backend._apply_runtime_item_apply_event_if_present.call_count == 2
