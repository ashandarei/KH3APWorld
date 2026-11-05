"""Tracker publication state follows successful sends and explicit resets."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ..client_support.json_snapshot import freeze_json
from ..client_support.poptracker import PopTrackerSyncService, TrackerCallbacks, TrackerRoute

ROUTE = TrackerRoute(team=2, slot=3, slot_name="Player", seed_name="Seed")
MAP = {"schema": "kh3ap.runtime_status.v1", "map": {"asset_path": "/Game/Levels/tt/tt_01/tt_01"}}


def emblem_state():
    return {"sequence": 1, "captured_global_ids": [13], "latest_capture_global_id": 13}


def sync(service, channel, snapshot, sender, route=ROUTE):
    if channel == "map":
        operation = service.sync_map(
            snapshot,
            route=route,
            send_messages=sender,
        )
    else:
        operation = service.sync_emblems(snapshot, route=route, send_messages=sender)
    asyncio.run(operation)


def test_mutable_emblem_snapshot_republishes_after_in_place_edit():
    service = PopTrackerSyncService()
    sender = AsyncMock()
    state = emblem_state()
    sync(service, "emblems", state, sender)

    state["captured_global_ids"].append(14)
    state["latest_capture_global_id"] = 14
    state["sequence"] = 2
    sync(service, "emblems", state, sender)
    sync(service, "emblems", state, sender)

    assert sender.await_count == 2
    messages = sender.await_args.args[0]
    assert [message["cmd"] for message in messages] == ["Set", "Bounce"]
    assert messages[0]["operations"] == [{"operation": "update", "value": {"13": True, "14": True}}]
    assert messages[1]["data"]["kh3_lucky_emblem_global_ids"] == [13, 14]


def test_emblem_reset_preserves_map_deduplication_and_full_reset_republishes_both():
    service = PopTrackerSyncService()
    sender = AsyncMock()
    state = freeze_json(emblem_state())
    sync(service, "map", MAP, sender)
    sync(service, "emblems", state, sender)

    service.reset_emblems()
    sync(service, "map", MAP, sender)
    sync(service, "emblems", state, sender)
    assert sender.await_count == 3
    assert sender.await_args.args[0][-1]["data"]["kh3_tracker_event"] == "lucky_emblem_state"

    service.reset()
    sync(service, "map", MAP, sender)
    sync(service, "emblems", state, sender)
    assert sender.await_count == 5


@pytest.mark.parametrize("channel", ["map", "emblems"])
def test_failed_send_retries_the_same_snapshot(channel):
    service = PopTrackerSyncService()
    sender = AsyncMock(side_effect=OSError("connection lost"))
    snapshot = MAP if channel == "map" else freeze_json(emblem_state())

    with pytest.raises(OSError, match="connection lost"):
        sync(service, channel, snapshot, sender)
    failed_messages = sender.await_args.args[0]

    sender.side_effect = None
    sync(service, channel, snapshot, sender)
    sync(service, channel, snapshot, sender)
    assert sender.await_count == 2
    assert sender.await_args.args[0] == failed_messages


@pytest.mark.parametrize("channel", ["map", "emblems"])
def test_invalid_snapshot_does_not_discard_last_successful_publication(channel):
    service = PopTrackerSyncService()
    sender = AsyncMock()
    snapshot = MAP if channel == "map" else freeze_json(emblem_state())
    sync(service, channel, snapshot, sender)

    sync(service, channel, {}, sender)
    sync(service, channel, None, sender)
    sync(service, channel, snapshot, sender)
    sender.assert_awaited_once()


def test_display_identity_changes_preserve_deduplication_until_reset():
    service = PopTrackerSyncService()
    sender = AsyncMock()
    state = freeze_json(emblem_state())
    sync(service, "map", MAP, sender)
    sync(service, "emblems", state, sender)

    renamed = replace(ROUTE, slot_name="Renamed Player", seed_name="Renamed Seed")
    sync(service, "map", MAP, sender, renamed)
    sync(service, "emblems", state, sender, renamed)
    assert sender.await_count == 2

    service.reset()
    sync(service, "map", MAP, sender, renamed)
    sync(service, "emblems", state, sender, renamed)
    assert sender.await_count == 4
    for sent in sender.await_args_list[-2:]:
        payload = sent.args[0][-1]["data"]
        assert payload["slot_name"] == "Renamed Player"
        assert payload["seed_name"] == "Renamed Seed"


@pytest.mark.parametrize("route", [replace(ROUTE, team=4), replace(ROUTE, slot=5)])
def test_emblem_publication_follows_team_and_slot(route):
    service = PopTrackerSyncService()
    sender = AsyncMock()
    state = freeze_json(emblem_state())
    sync(service, "emblems", state, sender)
    sync(service, "emblems", state, sender, route)
    sync(service, "emblems", state, sender, route)

    assert sender.await_count == 2
    messages = sender.await_args.args[0]
    assert messages[0]["key"] == f"kh3_lucky_emblems_{route.team}_{route.slot}"
    assert messages[1]["slots"] == [route.slot]


def test_unchanged_immutable_snapshot_skips_payload_reads():
    service = PopTrackerSyncService()
    sender = AsyncMock()
    state = freeze_json(emblem_state())
    sync(service, "emblems", state, sender)

    with patch.object(state, "get", side_effect=AssertionError("unchanged snapshot was rebuilt")):
        sync(service, "emblems", state, sender)
    sender.assert_awaited_once()


@pytest.mark.parametrize("channel", ["map", "emblems"])
def test_polling_does_not_read_or_publish_without_a_ready_connection(channel):
    payload, route, sender = Mock(), Mock(), AsyncMock()
    callbacks = TrackerCallbacks(ready=lambda: False, payload=payload, route=route, send=sender)
    asyncio.run(getattr(PopTrackerSyncService(), "publish_" + channel)(callbacks))
    payload.assert_not_called()
    route.assert_not_called()
    sender.assert_not_awaited()


@pytest.mark.parametrize("snapshot", [None, [], "invalid"])
def test_map_polling_skips_minimap_and_route_reads_without_runtime_context(snapshot):
    payload, route, sender = Mock(return_value=snapshot), Mock(), AsyncMock()
    callbacks = TrackerCallbacks(ready=lambda: True, payload=payload, route=route, send=sender)
    asyncio.run(PopTrackerSyncService().publish_map(callbacks))
    payload.assert_called_once_with("runtime_status.json")
    route.assert_not_called()
    sender.assert_not_awaited()


@pytest.mark.parametrize("channel", ["map", "emblems"])
def test_polling_retries_failed_publication_and_suppresses_only_successes(channel):
    service = PopTrackerSyncService()
    snapshot = MAP if channel == "map" else emblem_state()
    payload = Mock(side_effect=lambda name: None if name == "runtime_minimap_context.json" else snapshot)
    sender = AsyncMock(side_effect=OSError("disconnected"))
    callbacks = TrackerCallbacks(ready=lambda: True, payload=payload, route=lambda: ROUTE, send=sender)
    publish = getattr(service, "publish_" + channel)
    with pytest.raises(OSError, match="disconnected"):
        asyncio.run(publish(callbacks))
    sender.side_effect = None
    asyncio.run(publish(callbacks))
    asyncio.run(publish(callbacks))
    assert sender.await_count == 2
    messages = sender.await_args.args[0]
    assert messages[-1]["slots"] == [ROUTE.slot]
    assert messages[-1]["data"]["seed_name"] == ROUTE.seed_name
    assert [message["cmd"] for message in messages] == (["Bounce"] if channel == "map" else ["Set", "Bounce"])
