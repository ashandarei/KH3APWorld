"""Integrated bridge workers and ordered, event-driven client polling."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..BridgeSession import KH3BridgeSession
from ..ExternalBridge import BridgeRuntimeStatusWriter, SaveFilesBackend
from ..RuntimeProfiler import PythonProfileScope
from ..transport.pipe_transport import register as register_pipe
from ..transport.pipe_transport import unregister as unregister_pipe
from .bridge_publisher import OrderedBridgePublisher
from .recovery_commands import RecoveryCommandService

logger = logging.getLogger("Client")
PollStep = tuple[str, Callable[[], Awaitable[None]]]


@dataclass(frozen=True)
class BridgeWorkerOptions:
    poll_interval: float
    activity_event: threading.Event


def run_integrated_bridge_worker(
    bridge_dir: Path,
    save_dir: Path,
    stop_event: threading.Event,
    *,
    options: BridgeWorkerOptions,
    notify_pipe_activity: Callable[[], None],
) -> int:
    pipe = register_pipe(bridge_dir, options.activity_event, notify_pipe_activity)
    try:
        pipe.start()
        return _run_bridge_backend(bridge_dir, save_dir, stop_event, options)
    finally:
        unregister_pipe(pipe)


def _run_bridge_backend(
    bridge_dir: Path, save_dir: Path, stop_event: threading.Event, options: BridgeWorkerOptions
) -> int:
    session = KH3BridgeSession(bridge_dir)
    session.initialize()
    backend = SaveFilesBackend(
        save_dir,
        options.poll_interval,
        stop_event=stop_event,
        activity_event=options.activity_event,
    )
    status = BridgeRuntimeStatusWriter(
        session,
        "client-integrated-named-pipe",
        details={"configured_save_dir": str(save_dir), "save_dir": str(save_dir), "save_dir_source": "configured"},
    )
    status.mark_starting()
    try:
        status.mark_running()
        return backend.run(session, status)
    except Exception as exc:
        status.mark_error(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if stop_event.is_set():
            status.mark_stopped()


@dataclass(frozen=True)
class BridgePollCallbacks:
    exiting: Callable[[], bool]
    has_bridge: Callable[[], bool]
    interval: Callable[[], float]
    steps: Callable[[], tuple[PollStep, ...]]
    publisher: Callable[[], OrderedBridgePublisher | None]
    recovery: Callable[[], RecoveryCommandService | None]
    set_activity_notify: Callable[[Callable[[], None]], None]
    step_signature: Callable[[str], Any] | None = None


async def watch_bridge(
    callbacks: BridgePollCallbacks,
    *,
    profile_scope: Callable[[str], AbstractContextManager[Any]] = PythonProfileScope,
) -> None:
    pipe_activity = asyncio.Event()
    loop = asyncio.get_running_loop()

    def notify_pipe_activity() -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(pipe_activity.set)

    callbacks.set_activity_notify(notify_pipe_activity)
    observed: dict[str, Any] = {}
    while not callbacks.exiting():
        pipe_activity.clear()
        publisher = callbacks.publisher()
        if publisher is not None:
            publisher.retry_failed()
        recovery = callbacks.recovery()
        if recovery is not None:
            recovery.poll()
        with profile_scope("client:bridge_watcher:loop"):
            if callbacks.has_bridge():
                for name, step in callbacks.steps():
                    signature = callbacks.step_signature(name) if callbacks.step_signature else object()
                    if name not in observed or observed[name] != signature:
                        # Failed steps retain their previous revision so they retry.
                        if await _poll_steps(((name, step),), profile_scope):
                            observed[name] = signature
            else:
                observed.clear()
        try:
            await asyncio.wait_for(pipe_activity.wait(), timeout=callbacks.interval())
        except asyncio.TimeoutError:
            pass


async def _poll_steps(steps: tuple[PollStep, ...], profile_scope: Callable[[str], AbstractContextManager[Any]]) -> bool:
    succeeded = True
    for step_name, step in steps:
        with profile_scope(f"client:bridge_watcher:{step_name}"):
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:
                succeeded = False
                logger.exception("KH3 bridge watcher step %s failed; retrying on the next poll.", step_name)
    return succeeded
