"""Explicit task ownership and shutdown ordering for the KH3 client."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

KH3_SHUTDOWN_TIMEOUT_SECONDS = max(
    0.1,
    float(os.environ.get("KH3_SHUTDOWN_TIMEOUT_SECONDS", "5.0")),
)


class LifecycleContext(Protocol):
    """The CommonContext lifecycle surface used by the KH3 coordinator."""

    server_address: str | None
    server_task: asyncio.Task[None] | None
    ui_task: asyncio.Task[None] | None

    def run_gui(self) -> None: ...

    def run_cli(self) -> None: ...

    async def shutdown(self) -> None: ...


@dataclass
class LifecycleState:
    """Tasks and stop state exclusively coordinated by one lifecycle service."""

    server_task: asyncio.Task[None] | None = None
    bridge_watcher_task: asyncio.Task[None] | None = None
    integrated_bridge_task: asyncio.Task[None] | None = None
    integrated_bridge_stop_event: threading.Event | None = None
    integrated_bridge_dir: Path | None = None
    integrated_bridge_save_dir: Path | None = None
    integrated_bridge_thread: threading.Thread | None = None
    integrated_bridge_completion: asyncio.Future[int] | None = None
    gui_task: asyncio.Task[None] | None = None
    save_directory_restart_pending: bool = False


@dataclass(frozen=True)
class SaveDirectoryRestartCallbacks:
    enabled: Callable[[], bool]
    bridge: Callable[[], Path | None]
    game_running: Callable[[], bool]
    wait_shutdown: Callable[[], Awaitable[None]]
    start_bridge: Callable[[], None]
    save_dir: Callable[[], Path]
    schedule: Callable[[Awaitable[None]], None]


class BridgeLifecycleService:
    """Own KH3 client task creation, stop coordination, and ordered shutdown."""

    def __init__(
        self,
        context: LifecycleContext,
        *,
        server_loop: Callable[[LifecycleContext, str | None], Awaitable[None]],
        bridge_watcher: Callable[[LifecycleContext], Awaitable[None]],
        integrated_bridge_worker: Callable[[Path, Path, threading.Event], int],
        log_info: Callable[..., None],
        log_warning: Callable[..., None],
        log_exception: Callable[..., None],
        shutdown_timeout_seconds: float = KH3_SHUTDOWN_TIMEOUT_SECONDS,
        state: LifecycleState | None = None,
    ) -> None:
        self.context = context
        self.state = state if state is not None else LifecycleState()
        self._server_loop = server_loop
        self._bridge_watcher = bridge_watcher
        self._integrated_bridge_worker = integrated_bridge_worker
        self._log_info = log_info
        self._log_warning = log_warning
        self._log_exception = log_exception
        self._shutdown_timeout_seconds = max(0.1, shutdown_timeout_seconds)
        self._pending_bridge_pair: tuple[Path, Path] | None = None
        self._exiting = False

    def start_server(self, address: str | None = None, *, task_name: str) -> asyncio.Task[None]:
        """Start the AP server loop once and retain CommonContext compatibility."""
        current = self.context.server_task
        if current is not None and not current.done():
            self.state.server_task = current
            return current
        task = asyncio.create_task(self._server_loop(self.context, address), name=task_name)
        self.context.server_task = task
        self.state.server_task = task
        return task

    def start_bridge_watcher(self) -> asyncio.Task[None]:
        """Start the single KH3 bridge watcher task."""
        task = self.state.bridge_watcher_task
        if task is not None and not task.done():
            return task
        task = asyncio.create_task(self._bridge_watcher(self.context), name="KH3BridgeWatcher")
        self.state.bridge_watcher_task = task
        return task

    def start_gui(self) -> asyncio.Task[None] | None:
        """Start the existing GUI seam once and record its task ownership."""
        task = self.context.ui_task
        if task is not None and not task.done():
            self.state.gui_task = task
            return task
        self.context.run_gui()
        self.state.gui_task = self.context.ui_task
        return self.state.gui_task

    def start_cli(self) -> None:
        """Start the CommonContext CLI seam without changing its task policy."""
        self.context.run_cli()

    def start_integrated_bridge(
        self,
        *,
        enabled: bool,
        bridge_dir: Path | None,
        save_dir: Path,
    ) -> None:
        """Start one integrated worker for the selected bridge/save pair."""
        if not enabled or bridge_dir is None or self._exiting:
            return
        task = self.state.integrated_bridge_task
        completion = self.state.integrated_bridge_completion
        if (task is not None and not task.done()) or (completion is not None and not completion.done()):
            stop_event = self.state.integrated_bridge_stop_event
            if (
                self.state.integrated_bridge_dir == bridge_dir
                and self.state.integrated_bridge_save_dir == save_dir
                and stop_event is not None
                and not stop_event.is_set()
            ):
                return
            # Stop requests do not interrupt a synchronous file operation. Keep
            # ownership until it returns, coalescing rapid changes to one target.
            self._pending_bridge_pair = (bridge_dir, save_dir)
            if stop_event is not None:
                stop_event.set()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log_info("KH3 integrated bridge start deferred because no asyncio loop is running.")
            return

        stop_event = threading.Event()
        self.state.integrated_bridge_stop_event = stop_event
        self.state.integrated_bridge_dir = bridge_dir
        self.state.integrated_bridge_save_dir = save_dir
        self.state.integrated_bridge_task = loop.create_task(
            self.run_integrated_bridge(bridge_dir, save_dir, stop_event),
            name="KH3IntegratedBridge",
        )

    def stop_integrated_bridge(self) -> None:
        """Request worker shutdown; repeated requests are harmless."""
        self._pending_bridge_pair = None
        stop_event = self.state.integrated_bridge_stop_event
        if stop_event is not None:
            stop_event.set()

    def request_save_directory_restart(self, callbacks: SaveDirectoryRestartCallbacks) -> None:
        """Coalesce save-context changes and restart only after a stopped game/worker."""
        if not callbacks.enabled() or callbacks.bridge() is None:
            return
        if callbacks.game_running():
            self._log_warning(
                "KH3 save directory changed while KINGDOM HEARTS III is running; "
                "the bridge will keep its current save context until KH3 is closed and the directory is selected again."
            )
            return
        if self.state.save_directory_restart_pending:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._log_info("KH3 save-directory bridge restart deferred because no asyncio loop is running.")
            return
        self.state.save_directory_restart_pending = True
        callbacks.schedule(self.restart_save_directory_bridge(callbacks))

    async def restart_save_directory_bridge(self, callbacks: SaveDirectoryRestartCallbacks) -> None:
        try:
            await callbacks.wait_shutdown()
            if callbacks.game_running():
                self._log_warning(
                    "KINGDOM HEARTS III started while the save-directory bridge restart was in progress; "
                    "the bridge was not restarted against the new directory."
                )
                return
            callbacks.start_bridge()
            self._log_info("Restarted the KH3 save bridge for: %s", callbacks.save_dir())
        except RuntimeError as exc:
            self._log_warning("Could not restart the KH3 save watcher: %s", exc)
        finally:
            self.state.save_directory_restart_pending = False

    async def wait_for_integrated_bridge_shutdown(self, *, for_exit: bool = False) -> None:
        """Require quiescence before file changes; only process exit may abandon it."""
        self.stop_integrated_bridge()
        task = self.state.integrated_bridge_task
        completion = self.state.integrated_bridge_completion
        target = completion if completion is not None else task
        if target is None:
            return
        stopped = await self._await_shutdown_step(
            asyncio.shield(asyncio.gather(target, return_exceptions=True)),
            "integrated bridge",
            diagnose_bridge=True,
            exiting=for_exit,
        )
        if stopped:
            return
        if not for_exit:
            raise RuntimeError(
                "The KH3 save watcher is still running. No run files were changed; "
                "wait for it to stop and retry, or close the KH3 client."
            )
        if task is not None:
            task.cancel()
            await asyncio.sleep(0)
            if task.done():
                await asyncio.gather(task, return_exceptions=True)

    def _finish_integrated_bridge(
        self,
        stop_event: threading.Event,
        completion: asyncio.Future[int],
    ) -> None:
        # Observe errors even if the asyncio wrapper was cancelled while its
        # daemon thread was finishing. Cancellation never releases file ownership.
        if not completion.cancelled():
            error = completion.exception()
            task = self.state.integrated_bridge_task
            if error is not None and (task is None or task.cancelled()):
                self._log_warning("KH3 integrated bridge stopped with error: %s", error)
        if self.state.integrated_bridge_stop_event is not stop_event:
            return
        self.state.integrated_bridge_task = None
        self.state.integrated_bridge_stop_event = None
        self.state.integrated_bridge_dir = None
        self.state.integrated_bridge_save_dir = None
        self.state.integrated_bridge_thread = None
        self.state.integrated_bridge_completion = None
        pending = self._pending_bridge_pair
        self._pending_bridge_pair = None
        if pending is not None:
            self.start_integrated_bridge(enabled=True, bridge_dir=pending[0], save_dir=pending[1])

    def _publish_worker_completion(
        self,
        completion: asyncio.Future[int],
        *,
        result: int | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if completion.done():
            return
        if exception is not None:
            completion.set_exception(exception)
        else:
            completion.set_result(0 if result is None else result)

    def _run_worker_thread(
        self,
        loop: asyncio.AbstractEventLoop,
        completion: asyncio.Future[int],
        bridge_dir: Path,
        save_dir: Path,
        stop_event: threading.Event,
    ) -> None:
        try:
            result = self._integrated_bridge_worker(bridge_dir, save_dir, stop_event)
        except BaseException as exc:

            def callback(error: BaseException = exc) -> None:
                self._publish_worker_completion(completion, exception=error)
        else:

            def callback(worker_result: int = result) -> None:
                self._publish_worker_completion(completion, result=worker_result)

        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError:
            # The event loop can already be closed after a timed-out daemon worker
            # finally returns. There is no remaining waiter in that case.
            pass

    def _integrated_bridge_stack(self) -> str:
        thread = self.state.integrated_bridge_thread
        if thread is None or thread.ident is None:
            return "worker thread is not available"
        frame = sys._current_frames().get(thread.ident)
        if frame is None:
            return f"worker thread {thread.name} has no Python frame"
        return "".join(traceback.format_stack(frame)).rstrip()

    async def _await_shutdown_step(
        self,
        awaitable: Awaitable[object],
        label: str,
        *,
        diagnose_bridge: bool = False,
        exiting: bool = True,
    ) -> bool:
        task = asyncio.ensure_future(awaitable)
        done, _pending = await asyncio.wait({task}, timeout=self._shutdown_timeout_seconds)
        if done:
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True

        diagnostic = f"\n{self._integrated_bridge_stack()}" if diagnose_bridge else ""
        self._log_warning(
            "KH3 %s shutdown timed out after %.1f seconds.%s%s",
            label,
            self._shutdown_timeout_seconds,
            diagnostic,
            " Continuing client exit." if exiting else " The save watcher must stop before changing run files.",
        )
        task.cancel()
        # Give cooperative tasks one loop turn to observe cancellation, but do
        # not replace the original timeout with an unbounded cancellation wait.
        await asyncio.sleep(0)
        if task.done():
            await asyncio.gather(task, return_exceptions=True)
        return False

    async def run_integrated_bridge(
        self,
        bridge_dir: Path,
        save_dir: Path,
        stop_event: threading.Event,
    ) -> None:
        """Run the synchronous bridge worker without letting it pin process exit."""
        loop = asyncio.get_running_loop()
        completion: asyncio.Future[int] = loop.create_future()
        thread = threading.Thread(
            target=self._run_worker_thread,
            args=(loop, completion, bridge_dir, save_dir, stop_event),
            name="KH3IntegratedBridgeWorker",
            daemon=True,
        )
        if self.state.integrated_bridge_task is asyncio.current_task():
            self.state.integrated_bridge_thread = thread
            self.state.integrated_bridge_completion = completion
        completion.add_done_callback(lambda done: self._finish_integrated_bridge(stop_event, done))
        started = False
        try:
            flush = getattr(self.context, "flush_bridge_publications", None)
            if flush is not None:
                await flush()
            if stop_event.is_set():
                return
            thread.start()
            started = True
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            stop_event.set()
            raise
        except Exception as exc:
            self._log_exception("KH3 integrated bridge stopped with error: %s", exc)
        finally:
            if not started:
                completion.cancel()

    async def shutdown(self) -> None:
        """Stop bridge work after exit, then await base, watcher, and worker tasks."""
        self._exiting = True
        recovery = self.context.__dict__.get("recovery_command_service")
        if recovery is not None:
            recovery.cancel()
        self.context.server_address = None
        self.stop_integrated_bridge()
        await self._await_shutdown_step(self.context.shutdown(), "base client")
        watcher_task = self.state.bridge_watcher_task
        if watcher_task is not None:
            await self._await_shutdown_step(watcher_task, "bridge watcher")
        await self.wait_for_integrated_bridge_shutdown(for_exit=True)
        flush = getattr(self.context, "flush_bridge_publications", None)
        if flush is not None:
            await self._await_shutdown_step(flush(), "bridge publications")
