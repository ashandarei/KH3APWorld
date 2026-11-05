"""Serialize AP-owned snapshot publication without blocking the client loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .json_snapshot import freeze_json


@dataclass(frozen=True)
class BridgePublication:
    bridge_dir: Path
    slot_data: dict[str, Any]
    status_payload: dict[str, Any]
    items_payload: dict[str, Any]


@dataclass
class _PublicationBatch:
    snapshot: BridgePublication
    sequence: int
    generation: int
    callbacks: list[Callable[[], None]]


class OrderedBridgePublisher:
    """Combine adjacent pending snapshots and publish them in order off the loop."""

    def __init__(self, publish: Callable[..., None], log_error: Callable[..., None]) -> None:
        self._publish = publish
        self._log_error = log_error
        self._tail: asyncio.Task[None] | None = None
        self._pending: _PublicationBatch | None = None
        self._sequence = 0
        self._generation = 0
        self._latest: dict[Path, int] = {}
        self._failed: dict[Path, _PublicationBatch] = {}
        self._slot_source: dict[str, Any] | None = None
        self._slot_snapshot: dict[str, Any] = {}

    def invalidate_slot_data(self) -> None:
        self._slot_source = None
        self._generation += 1

    def submit(
        self,
        bridge_dir: Path,
        *,
        slot_data: dict[str, Any],
        status_payload: dict[str, Any],
        items_payload: dict[str, Any],
        on_success: Callable[[], None] | None = None,
    ) -> asyncio.Future[None] | None:
        if slot_data is not self._slot_source:
            self._slot_snapshot = freeze_json(slot_data)
            self._slot_source = slot_data
        snapshot = BridgePublication(
            bridge_dir,
            self._slot_snapshot,
            freeze_json(status_payload),
            freeze_json(items_payload),
        )
        return self._enqueue(snapshot, [on_success] if on_success is not None else [], self._generation)

    def _enqueue(
        self,
        snapshot: BridgePublication,
        callbacks: list[Callable[[], None]],
        generation: int,
    ) -> asyncio.Future[None] | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Standalone CLI/test calls have no event loop to keep responsive.
            self._write(snapshot)
            self._notify_success(callbacks)
            return None
        self._sequence += 1
        sequence = self._sequence
        self._latest[snapshot.bridge_dir] = sequence
        self._failed.pop(snapshot.bridge_dir, None)
        pending = self._pending
        if (
            pending is not None
            and self._tail is not None
            and not self._tail.done()
            and pending.generation == generation
            and pending.snapshot.bridge_dir == snapshot.bridge_dir
            and pending.snapshot.slot_data is snapshot.slot_data
        ):
            # Every waiter observes the newest full snapshot, with all completion
            # callbacks retained. Started writes and intervening run/reset batches
            # cannot be replaced, even when a later run reuses the same path.
            pending.snapshot = snapshot
            pending.sequence = sequence
            pending.callbacks.extend(callbacks)
            return self._completion(self._tail)
        batch = _PublicationBatch(snapshot, sequence, generation, callbacks)
        task = loop.create_task(self._run(batch, self._tail), name="KH3BridgePublication")
        self._pending = batch
        self._tail = task
        task.add_done_callback(self._report_failure)
        return self._completion(task)

    @staticmethod
    def _completion(task: asyncio.Task[None]) -> asyncio.Future[None]:
        # A cancelled caller must not cancel a shared publication or its peers.
        completion = asyncio.shield(task)
        completion.add_done_callback(OrderedBridgePublisher._consume_completion_error)
        return completion

    @staticmethod
    def _consume_completion_error(completion: asyncio.Future[None]) -> None:
        if not completion.cancelled():
            completion.exception()  # The publisher logs errors once per write.

    def _write(self, snapshot: BridgePublication) -> None:
        self._publish(
            snapshot.bridge_dir,
            slot_data=snapshot.slot_data,
            status_payload=snapshot.status_payload,
            items_payload=snapshot.items_payload,
        )

    async def _run(
        self,
        batch: _PublicationBatch,
        previous: asyncio.Task[None] | None,
    ) -> None:
        if previous is not None:
            try:
                await asyncio.shield(previous)
            except Exception:
                pass  # A newer complete snapshot can repair a failed publication.
        if self._pending is batch:
            self._pending = None
        snapshot = batch.snapshot
        try:
            await asyncio.to_thread(self._write, snapshot)
        except Exception:
            if self._latest.get(snapshot.bridge_dir) == batch.sequence:
                self._failed[snapshot.bridge_dir] = batch
            raise
        self._notify_success(batch.callbacks)

    def _notify_success(self, callbacks: list[Callable[[], None]]) -> None:
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                self._log_error("KH3 bridge publication succeeded but its completion callback failed: %s", exc)

    def _report_failure(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            self._log_error("KH3 bridge publication failed; retrying on the next poll: %s", error)

    def retry_failed(self) -> None:
        for batch in tuple(self._failed.values()):
            self._enqueue(batch.snapshot, list(batch.callbacks), batch.generation)

    async def flush(self) -> None:
        """Finish writes before starting a bridge, switching runs, or exiting."""
        self.retry_failed()
        while self._tail is not None:
            pending = self._tail
            await asyncio.shield(pending)
            if pending is self._tail:
                return
