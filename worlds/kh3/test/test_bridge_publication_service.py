"""Seed admission and snapshot selection before ordered bridge publication."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from ..Client import KH3Context
from ..client_support.bridge_publication import BridgePublicationService, BridgeRefreshCallbacks, BridgeRefreshInput
from ..client_support.bridge_publisher import OrderedBridgePublisher

SEED = {"seed_name": "22222222222222222222", "slot": 1, "slot_name": "Sora"}
OTHER = {**SEED, "seed_name": "11111111111111111111"}


def publication_fixture(**overrides):
    items, selected = {"items": []}, {"items": [{"index": 0, "item_name": "Potion"}]}
    publisher = Mock()
    callbacks = BridgeRefreshCallbacks(
        build_items=Mock(return_value=items),
        select_items=Mock(return_value=selected),
        build_status=Mock(return_value={"received_item_count": 1}),
        success_callback=Mock(return_value=None),
        publisher=Mock(return_value=publisher),
        profile_scope=Mock(side_effect=lambda _: nullcontext()),
    )
    return BridgePublicationService(replace(callbacks, **overrides)), callbacks, publisher, items, selected


@pytest.mark.parametrize(
    "bridge,owner,room,allowed",
    [
        (Path("run"), SEED, SEED["seed_name"], True),
        (Path("run"), SEED, "AP_" + SEED["seed_name"], True),
        (Path("run"), None, SEED["seed_name"], True),
        (Path("run"), SEED, None, True),
        (Path("run"), OTHER, SEED["seed_name"], False),
        (Path("run"), {**SEED, "slot": 2}, SEED["seed_name"], False),
        (Path("run"), SEED, OTHER["seed_name"], False),
        (None, SEED, SEED["seed_name"], False),
    ],
)
def test_publication_admission_precedes_payload_reads(bridge, owner, room, allowed):
    service, callbacks, publisher, _, _ = publication_fixture()
    service.refresh(BridgeRefreshInput(bridge, {"kh3_seed": SEED}, owner, room))
    assert callbacks.build_items.call_count == int(allowed)
    assert callbacks.profile_scope.call_count == int(allowed)
    assert publisher.submit.call_count == int(allowed)


def test_status_and_items_share_the_selected_receipts_and_completion_future():
    service, callbacks, publisher, items, selected = publication_fixture()
    completed, notification = object(), Mock()
    publisher.submit.return_value = completed
    callbacks.success_callback.return_value = notification
    slot_data = {"kh3_seed": SEED}
    result = service.refresh(BridgeRefreshInput(Path("run"), slot_data, SEED, SEED["seed_name"]))
    callbacks.select_items.assert_called_once_with(items, Path("run"))
    callbacks.build_status.assert_called_once_with(selected)
    assert publisher.submit.call_args.kwargs["slot_data"] is slot_data
    assert publisher.submit.call_args.kwargs["items_payload"] is selected
    assert publisher.submit.call_args.kwargs["on_success"] is notification
    notification.assert_not_called()
    assert result is completed


@pytest.mark.parametrize("stage", ["build", "select", "status"])
def test_snapshot_failure_does_not_submit_or_notify(stage):
    service, callbacks, publisher, _, _ = publication_fixture()
    target = {"build": callbacks.build_items, "select": callbacks.select_items, "status": callbacks.build_status}[stage]
    target.side_effect = OSError("locked payload")
    with pytest.raises(OSError, match="locked payload"):
        service.refresh(BridgeRefreshInput(Path("run"), {"kh3_seed": SEED}, SEED, SEED["seed_name"]))
    publisher.submit.assert_not_called()


def test_profiling_brackets_payload_selection_and_submission():
    events = []

    @contextmanager
    def scope(name):
        events.append(name)
        try:
            yield
        finally:
            events.append("finished")

    service, _, _, _, _ = publication_fixture(
        profile_scope=scope,
        build_items=lambda: events.append("items") or {},
        select_items=lambda items, _: events.append("select") or items,
        build_status=lambda _: events.append("status") or {},
        publisher=lambda: Mock(submit=lambda *args, **kwargs: events.append("submit")),
    )
    service.refresh(BridgeRefreshInput(Path("run"), {}, None, None))
    assert events == ["client:write_bridge_files", "items", "select", "status", "submit", "finished"]


def test_publication_notifies_the_worker_only_after_the_write_completes():
    async def run_case():
        started, release, notified = threading.Event(), threading.Event(), threading.Event()
        writes = []

        def write(path, **payload):
            started.set()
            assert release.wait(2)
            writes.append((path, payload))

        publisher = OrderedBridgePublisher(write, Mock())
        service, _, _, _, _ = publication_fixture(publisher=lambda: publisher, success_callback=lambda: notified.set)
        try:
            completion = service.refresh(BridgeRefreshInput(Path("run"), {}, None, None))
            assert await asyncio.to_thread(started.wait, 2)
            assert not notified.is_set()
            assert not completion.done()
            release.set()
            await completion
        finally:
            release.set()
            await publisher.flush()
        assert notified.is_set()
        assert len(writes) == 1
        assert writes[0][1]["status_payload"]["received_item_count"] == 1
        assert len(writes[0][1]["items_payload"]["items"]) == 1

    asyncio.run(run_case())


def test_context_observes_connection_readiness_after_building_items():
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir, ctx.slot_data = Path("run"), {}
    ctx.kh3_received_items_snapshot_ready = False
    ctx._is_ap_connected = lambda: True

    def build_items():
        ctx.kh3_received_items_snapshot_ready = True
        return {"items": []}

    ctx.build_items_payload = build_items
    ctx.build_status_payload = lambda _: {}
    item_service = Mock(payload_for_refresh=Mock(side_effect=lambda items, **kwargs: items))
    ctx._get_item_sync_service = lambda: item_service
    publisher = Mock()
    ctx._get_bridge_publisher = lambda: publisher
    ctx.write_bridge_files()
    assert item_service.payload_for_refresh.call_args.kwargs["received_items_snapshot_ready"] is True
    assert item_service.payload_for_refresh.call_args.kwargs["ap_connected"] is True
