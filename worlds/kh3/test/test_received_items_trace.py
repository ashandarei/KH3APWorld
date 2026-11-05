"""Packet diagnostics follow successful writes and retain their original file."""

import asyncio
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from ..bridge_support.files import file_signature
from ..client_support import received_items_trace as trace_module
from ..client_support.received_items_trace import ReceivedItemsTrace


@pytest.mark.parametrize(
    "packet, expected",
    [
        ({"index": 3, "items": [1, 2]}, (3, 4, 2)),
        ({"index": "4", "items": (1,)}, (4, 4, 1)),
        ({"index": 0, "items": []}, (0, None, 0)),
        ({"index": -1, "items": [1]}, (-1, None, 1)),
        ({"index": "invalid", "items": [1]}, (-1, None, 1)),
        ({"items": [1]}, (-1, None, 1)),
        ({"index": 0, "items": "invalid"}, (0, None, 0)),
    ],
)
def test_packet_metadata_is_correlated_across_both_events(packet, expected):
    with patch.object(trace_module, "append_runtime_item_latency_trace_event") as record:
        trace = ReceivedItemsTrace(None, packet)
        trace.follow(None, bridge_dir=None)
    received, published = record.call_args_list
    assert [call.args[1] for call in record.call_args_list] == ["received_items_packet", "ap_items_snapshot_published"]
    for call in (received, published):
        assert (
            tuple(call.kwargs[key] for key in ("packet_start_index", "packet_last_index", "packet_item_count"))
            == expected
        )
    assert received.kwargs["packet_trace_id"] == published.kwargs["packet_trace_id"]
    assert published.kwargs["ap_items_signature"] is None
    assert not published.kwargs["ap_items_changed"]


def test_publication_uses_the_captured_bridge_and_measures_the_completed_disk_write(tmp_path):
    bridge = tmp_path / "old-bridge"
    bridge.mkdir()
    path = bridge / "ap_items.json"
    path.write_text("old", encoding="utf-8")
    packet = {"index": 3, "items": [1]}

    async def run_case(record):
        publication = asyncio.get_running_loop().create_future()
        trace = ReceivedItemsTrace(bridge, packet)
        trace.follow(publication, bridge_dir=bridge)
        packet.update(index=99, items=[])
        assert record.call_count == 1
        path.write_text("new payload", encoding="utf-8")
        publication.set_result(None)
        await asyncio.sleep(0)

    with (
        patch.object(trace_module, "append_runtime_item_latency_trace_event") as record,
        patch.object(trace_module.time, "perf_counter", side_effect=[1.0, 1.025]),
    ):
        asyncio.run(run_case(record))
    published = record.call_args
    assert published.args == (bridge, "ap_items_snapshot_published")
    assert published.kwargs["packet_start_index"] == 3
    assert published.kwargs["packet_item_count"] == 1
    assert published.kwargs["ap_items_signature"] == list(file_signature(path))
    assert published.kwargs["ap_items_changed"]
    assert published.kwargs["callback_to_publish_ms"] == 25.0


@pytest.mark.parametrize("cancelled", [True, False])
def test_failed_or_cancelled_publication_does_not_claim_a_published_snapshot(cancelled):
    async def run_case():
        publication = asyncio.get_running_loop().create_future()
        trace = ReceivedItemsTrace(None, {"index": 0, "items": []})
        trace.follow(publication, bridge_dir=None)
        if cancelled:
            publication.cancel()
        else:
            publication.set_exception(OSError("disk"))
        await asyncio.sleep(0)

    with patch.object(trace_module, "append_runtime_item_latency_trace_event") as record:
        asyncio.run(run_case())
    assert record.call_count == 1
    assert record.call_args.args[1] == "received_items_packet"


def test_unreadable_snapshot_does_not_prevent_diagnostics():
    record = Mock()
    with (
        patch.object(trace_module, "append_runtime_item_latency_trace_event", record),
        patch.object(trace_module, "file_signature", side_effect=PermissionError("locked")),
    ):
        ReceivedItemsTrace(Path("synthetic-bridge"), {}).follow(None, bridge_dir=Path("synthetic-bridge"))
    assert record.call_count == 2
    assert record.call_args.kwargs["ap_items_signature"] is None
    assert not record.call_args.kwargs["ap_items_changed"]
