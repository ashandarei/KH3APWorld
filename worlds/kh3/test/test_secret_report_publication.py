"""Hint publication readiness, retry, and successful-send bookkeeping."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support.secret_report_hint_service import SecretReportHintCallbacks, SecretReportHintService


def hint_fixture(**overrides):
    service = SecretReportHintService()
    inputs = {"runtime_secret_report_hints.json": {
        "schema": "kh3ap.secret_report_hints.v1", "owner": "dll",
        "reports": {"1": [{"player": 2, "location": 20}], "13": [{"player": 2, "location": 21}]},
        "missing_report_numbers": [],
    }}
    callbacks = SecretReportHintCallbacks(
        ready=lambda: True,
        payload=Mock(side_effect=inputs.get),
        send=AsyncMock(),
    )
    return service, replace(callbacks, **overrides), inputs


def test_disconnected_hint_poll_does_not_read_or_send():
    service, callbacks, _ = hint_fixture(ready=lambda: False)
    asyncio.run(service.publish_pending(callbacks))
    callbacks.payload.assert_not_called()
    callbacks.send.assert_not_awaited()


def test_no_applied_reports_skips_metadata_and_network_io():
    service, callbacks, inputs = hint_fixture()
    inputs["runtime_secret_report_hints.json"]["reports"] = {}
    asyncio.run(service.publish_pending(callbacks))
    callbacks.send.assert_not_awaited()
    assert not service.sent_report_numbers


def test_applied_report_targets_are_sent_once_per_connection():
    async def run_case():
        service, callbacks, _ = hint_fixture()
        await service.publish_pending(callbacks)
        await service.publish_pending(callbacks)
        callbacks.send.assert_awaited_once_with([{"cmd": "CreateHints", "locations": [20, 21], "player": 2}])
        assert service.sent_report_numbers == {1, 13}
        service.reset_for_connection()
        await service.publish_pending(callbacks)
        assert callbacks.send.await_count == 2

    asyncio.run(run_case())


def test_hint_reports_are_marked_only_after_awaited_send_completion():
    async def run_case():
        started, release = asyncio.Event(), asyncio.Event()
        service, callbacks, _ = hint_fixture()

        async def send(messages):
            assert messages
            started.set()
            await release.wait()

        task = asyncio.create_task(service.publish_pending(replace(callbacks, send=send)))
        await started.wait()
        assert not service.sent_report_numbers
        assert not task.done()
        release.set()
        await task
        assert service.sent_report_numbers == {1, 13}

    asyncio.run(run_case())


@pytest.mark.parametrize("error", [OSError("network unavailable"), asyncio.CancelledError()])
def test_failed_or_cancelled_send_keeps_all_reports_retryable(error):
    async def run_case():
        service, callbacks, _ = hint_fixture(send=AsyncMock(side_effect=error))
        with pytest.raises(type(error)):
            await service.publish_pending(callbacks)
        assert not service.sent_report_numbers
        retry = replace(callbacks, send=AsyncMock())
        await service.publish_pending(retry)
        retry.send.assert_awaited_once()
        assert service.sent_report_numbers == {1, 13}

    asyncio.run(run_case())


def test_only_resolved_reports_are_marked_and_missing_targets_warn_once(caplog):
    async def run_case():
        service, callbacks, inputs = hint_fixture()
        snapshot = inputs["runtime_secret_report_hints.json"]
        snapshot["reports"].pop("13")
        snapshot["missing_report_numbers"] = [13]
        with caplog.at_level(logging.WARNING, logger="Client"):
            await service.publish_pending(callbacks)
            await service.publish_pending(callbacks)
        assert service.sent_report_numbers == {1}
        callbacks.send.assert_awaited_once_with([{"cmd": "CreateHints", "locations": [20], "player": 2}])
        warnings = [record for record in caplog.records if "does not contain AP hint targets" in record.message]
        assert len(warnings) == 1
        assert "13" in warnings[0].message
        snapshot["reports"]["13"] = [{"player": 3, "location": 30}]
        snapshot["missing_report_numbers"] = []
        await service.publish_pending(callbacks)
        assert service.sent_report_numbers == {1, 13}
        assert callbacks.send.await_args.args[0] == [{"cmd": "CreateHints", "locations": [30], "player": 3}]

    asyncio.run(run_case())


def test_resolved_empty_report_targets_complete_without_an_empty_send():
    service, callbacks, inputs = hint_fixture()
    inputs["runtime_secret_report_hints.json"]["reports"] = {"1": [], "13": []}
    asyncio.run(service.publish_pending(callbacks))
    callbacks.send.assert_not_awaited()
    assert service.sent_report_numbers == {1, 13}


def test_failed_hint_read_does_not_send_or_mark_reports():
    service, callbacks, _ = hint_fixture(payload=Mock(side_effect=OSError("read unavailable")))
    with pytest.raises(OSError, match="read unavailable"):
        asyncio.run(service.publish_pending(callbacks))
    callbacks.send.assert_not_awaited()
    assert not service.sent_report_numbers


def test_only_native_snapshot_is_read():
    service, callbacks, _ = hint_fixture()
    asyncio.run(service.publish_pending(callbacks))
    callbacks.payload.assert_called_once_with("runtime_secret_report_hints.json")


@pytest.mark.parametrize("snapshot", [None, {}, {"applied_item_indices": [0]},
    {"schema": "kh3ap.secret_report_hints.v1", "owner": "python", "reports": {"1": []}, "missing_report_numbers": []}])
def test_legacy_or_unavailable_snapshot_does_not_publish(snapshot):
    service, callbacks, _ = hint_fixture(payload=Mock(return_value=snapshot))
    asyncio.run(service.publish_pending(callbacks))
    callbacks.send.assert_not_awaited()
    assert not service.sent_report_numbers
