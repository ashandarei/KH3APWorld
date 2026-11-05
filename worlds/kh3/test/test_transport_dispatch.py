"""Transport dispatch observes actual sends and preserves diagnostic event order."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from pathlib import Path

import pytest

from ..client_support import client_transport_service as transport
from ..bridge_support.diagnostic_history import history_for


@pytest.fixture
def service():
    return transport.ClientTransportService(
        protocol_name="kh3-ap-bridge",
        protocol_version=1,
        payload_reader=Mock(),
        validate_protocol=Mock(),
        protocol_error=ValueError,
    )


def outgoing(*messages, enabled=True):
    return transport.OutgoingMessageInput(tuple(messages), enabled, 30)


def test_send_completion_waits_for_the_network_send(service):
    async def run_case():
        entered, release = asyncio.Event(), asyncio.Event()
        checks = {"cmd": "LocationChecks", "locations": [101]}
        events = []

        async def send(messages):
            assert messages == [checks]
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            service.send_messages(
                outgoing(checks), sender=send, record_event=lambda event, **kwargs: events.append(event)
            )
        )
        await entered.wait()
        assert events == ["location_checks_send_attempt"]
        release.set()
        await task
        assert events == ["location_checks_send_attempt", "location_checks_send_complete"]

    asyncio.run(run_case())


@pytest.mark.parametrize("error", [OSError("offline"), asyncio.CancelledError()])
def test_failed_or_cancelled_sends_propagate_without_recording_completion(service, error):
    checks = {"cmd": "LocationChecks", "locations": [101]}
    events = Mock()
    with pytest.raises(type(error)):
        asyncio.run(service.send_messages(outgoing(checks), sender=AsyncMock(side_effect=error), record_event=events))
    assert [call.args[0] for call in events.call_args_list] == ["location_checks_send_attempt"] + (
        [] if isinstance(error, asyncio.CancelledError) else ["location_checks_send_failed"]
    )
    if isinstance(error, OSError):
        assert events.call_args.kwargs["error"] == "OSError: offline"


@pytest.mark.parametrize("include_chat", [True, False])
def test_seed_guard_suppresses_checks_and_goal_before_sending_other_messages(service, include_chat):
    checks, goal, chat = (
        {"cmd": "LocationChecks", "locations": [101]},
        {"cmd": "StatusUpdate", "status": 30},
        {"cmd": "Say", "text": "hello"},
    )
    events = []

    async def send(messages):
        assert events == ["location_checks_send_suppressed"]
        assert messages == [chat]

    sender = AsyncMock(side_effect=send)
    messages = (checks, goal, chat) if include_chat else (checks, goal)
    asyncio.run(
        service.send_messages(
            outgoing(*messages, enabled=False), sender=sender, record_event=lambda event, **kwargs: events.append(event)
        )
    )
    assert sender.await_count == int(include_chat)
    assert events == ["location_checks_send_suppressed"]


def diagnostic(event, locations):
    return transport.LocationTransportDiagnosticInput(
        event=event,
        recorded_at_utc=datetime(2026, 10, 3, tzinfo=timezone.utc),
        location_id_to_bridge_key={101: "chest:001"},
        last_processed_game_state_seq=5,
        check_reporting_enabled=True,
        connected=True,
        server_address="host:38281",
        team=0,
        slot=1,
        locations=locations,
    )


def test_diagnostics_keep_order_and_latest_state_in_memory(service, tmp_path):
    service.write_location_transport_diagnostic(tmp_path, diagnostic("attempt", [101]))
    service.write_location_transport_diagnostic(tmp_path, diagnostic("complete", [101, 102]))
    history = [
        json.loads(line)
        for line in history_for(tmp_path).lines(transport.KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE)
    ]
    state = history[-1]
    assert [entry["event"] for entry in history] == ["attempt", "complete"]
    assert state == history[-1]
    assert state["location_ids"] == [101, 102]
    assert state["bridge_keys"] == ["chest:001"]
    assert state["last_processed_game_state_seq"] == 5
    assert state["recorded_at_utc"] == "2026-10-03T00:00:00+00:00"
    assert list(tmp_path.iterdir()) == []


def test_diagnostics_continue_without_storage_access(service, tmp_path, monkeypatch):
    root = tmp_path / "missing-directory"
    with monkeypatch.context() as guards:
        guards.setattr(Path, "open", Mock(side_effect=AssertionError("diagnostic disk open")))
        guards.setattr(Path, "stat", Mock(side_effect=AssertionError("diagnostic disk stat")))
        service.write_location_transport_diagnostic(root, diagnostic("attempt", [101]))
    assert json.loads(history_for(root).lines(transport.KH3_LOCATION_CHECK_TRANSPORT_LOG_FILE)[-1])["event"] == "attempt"
    assert not root.exists()
