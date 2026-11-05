"""Correlate received-item packets with completed bridge publications."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..bridge_support.files import file_signature
from ..bridge_support.observability import append_runtime_item_latency_trace_event


def _snapshot_signature(path: Path | None) -> list[int] | None:
    if path is None:
        return None
    try:
        return list(file_signature(path))
    except OSError:
        return None


class ReceivedItemsTrace:
    """Keep a packet's identity and file observation until its write completes."""

    def __init__(self, bridge_dir: Path | None, packet: dict[str, Any]) -> None:
        received_at = datetime.now(timezone.utc)
        self._started_at = time.perf_counter()
        raw_items = packet.get("items")
        item_count = len(raw_items) if isinstance(raw_items, (list, tuple)) else 0
        try:
            start_index = int(packet.get("index"))
        except (TypeError, ValueError):
            start_index = -1
        last_index = start_index + item_count - 1 if start_index >= 0 and item_count > 0 else None
        self._fields = {
            "packet_trace_id": f"received-items-{start_index}-{item_count}-{received_at.strftime('%Y%m%dT%H%M%S.%fZ')}",
            "packet_start_index": start_index,
            "packet_last_index": last_index,
            "packet_item_count": item_count,
        }
        self._items_path = bridge_dir / "ap_items.json" if bridge_dir is not None else None
        self._signature_before = _snapshot_signature(self._items_path)
        append_runtime_item_latency_trace_event(
            bridge_dir,
            "received_items_packet",
            recorded_at_utc=received_at.isoformat(),
            **self._fields,
        )

    def follow(self, publication: asyncio.Future[None] | None, *, bridge_dir: Path | None) -> None:
        def record_publication(completed: asyncio.Future[None] | None = None) -> None:
            if completed is not None and (completed.cancelled() or completed.exception() is not None):
                return
            signature_after = _snapshot_signature(self._items_path)
            append_runtime_item_latency_trace_event(
                bridge_dir,
                "ap_items_snapshot_published",
                **self._fields,
                ap_items_changed=signature_after != self._signature_before,
                ap_items_signature=signature_after,
                callback_to_publish_ms=round((time.perf_counter() - self._started_at) * 1000.0, 3),
            )

        if publication is None:
            record_publication()
        else:
            publication.add_done_callback(record_publication)
