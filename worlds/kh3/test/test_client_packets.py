"""Packet admission, preparation ownership, and connected setup boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..Bridge import BridgeProtocolError
from ..client_support import client_packets
from ..client_support.client_packets import (
    ClientPacketCallbacks,
    ClientPacketService,
    ConnectedSetupCallbacks,
    ConnectedSetupService,
)


def packet_fixture(**overrides):
    events, scheduled = [], []

    def schedule(coroutine, name):
        scheduled.append((coroutine, name))
        coroutine.close()
        events.append(name)

    callbacks = ClientPacketCallbacks(
        observe_room=lambda seed: events.append(("room", seed)),
        reset_auth_recovery=lambda: events.append("reset_auth"),
        apply_connected=lambda args: events.append(("connected", args)),
        disable_checks=lambda: events.append("disable"),
        warn=lambda text: events.append(("warn", text)),
        disconnect=AsyncMock(),
        schedule=schedule,
        bootstrap_pending=lambda: False,
        bootstrap=AsyncMock(),
        companion=lambda: None,
        prepare_companion=AsyncMock(),
        finish_connected=lambda args: events.append(("finish", args)),
        record_checks=lambda event, checks, command: events.append((event, checks, command)),
        bridge=lambda: Path("bridge"),
        publish=lambda: events.append("publish"),
        invalidate_items=lambda: events.append("invalidate"),
        mark_receipts_ready=lambda: events.append("ready"),
        receive_items=Mock(return_value=None),
    )
    callbacks = replace(callbacks, **overrides)
    return ClientPacketService(callbacks), callbacks, events, scheduled


@pytest.mark.parametrize("command", ["RoomInfo", "RoomUpdate", "DataPackage", "unknown"])
def test_packet_routes_only_its_own_effects(command):
    service, _, events, _ = packet_fixture()
    service.handle(command, {"seed_name": "seed", "checked_locations": [17]})
    expected = {
        "RoomInfo": [("room", "seed")],
        "RoomUpdate": [("location_checks_server_confirmed", [17], "RoomUpdate"), "publish"],
        "DataPackage": ["invalidate", "publish"],
        "unknown": [],
    }
    assert events == expected[command]


@pytest.mark.parametrize("command", ["RoomUpdate", "DataPackage"])
def test_packet_without_selected_bridge_does_not_publish(command):
    service, _, events, _ = packet_fixture(bridge=lambda: None)
    service.handle(command, {})
    assert events == (["invalidate"] if command == "DataPackage" else [])


def test_connected_finishes_only_after_applying_state_and_auth_reset():
    service, _, events, scheduled = packet_fixture()
    args = {"slot_data": {"seed": "new"}}
    service.handle("Connected", args)
    assert events == ["reset_auth", ("connected", args), ("finish", args)]
    assert not scheduled


def test_incompatible_connected_packet_disables_checks_and_disconnects():
    service, callbacks, events, scheduled = packet_fixture(
        apply_connected=Mock(side_effect=BridgeProtocolError("incompatible")),
        bootstrap_pending=Mock(side_effect=AssertionError("continued after invalid data")),
    )
    service.handle("Connected", {})
    assert events == ["reset_auth", "disable", ("warn", "incompatible"), "KH3IncompatibleSlotData"]
    callbacks.disconnect.assert_called_once_with()
    assert len(scheduled) == 1


def test_unexpected_connected_failure_propagates_without_scheduling():
    error = RuntimeError("broken state application")
    service, _, events, scheduled = packet_fixture(apply_connected=Mock(side_effect=error))
    with pytest.raises(RuntimeError, match="broken state"):
        service.handle("Connected", {})
    assert events == ["reset_auth"]
    assert not scheduled


@pytest.mark.parametrize("preparation", ["bootstrap", "companion"])
def test_connected_preparation_receives_a_packet_copy_and_owns_completion(preparation):
    service, callbacks, events, scheduled = packet_fixture(
        bootstrap_pending=lambda: preparation == "bootstrap",
        companion=lambda: SimpleNamespace(status="missing"),
    )
    args = {"slot_data": {"seed": "new"}}
    service.handle("Connected", args)
    expected_task = (
        "KH3ConnectedSeedPreparation" if preparation == "bootstrap" else "KH3ConnectedOlympusColiseumPreparation"
    )
    assert events[-1] == expected_task
    assert len(scheduled) == 1
    action = callbacks.bootstrap if preparation == "bootstrap" else callbacks.prepare_companion
    action.assert_called_once()
    assert action.call_args.args[0] == args
    assert action.call_args.args[0] is not args
    assert not any(isinstance(event, tuple) and event[0] == "finish" for event in events)


@pytest.mark.parametrize("index,ready", [(0, True), (5, False), (None, False)])
def test_received_item_snapshot_readiness_and_trace_keep_the_publication_route(monkeypatch, index, ready):
    future, route = object(), Path("original")
    trace = Mock()
    constructor = Mock(return_value=trace)
    monkeypatch.setattr(client_packets, "ReceivedItemsTrace", constructor)
    service, callbacks, events, _ = packet_fixture(bridge=lambda: route, receive_items=Mock(return_value=future))
    args = {"index": index}
    service.handle("ReceivedItems", args)
    assert events == (["ready"] if ready else [])
    constructor.assert_called_once_with(route, args)
    callbacks.receive_items.assert_called_once_with(route)
    trace.follow.assert_called_once_with(future, bridge_dir=route)


def test_received_item_refresh_failure_does_not_register_trace_completion(monkeypatch):
    trace = Mock()
    monkeypatch.setattr(client_packets, "ReceivedItemsTrace", Mock(return_value=trace))
    service, _, events, _ = packet_fixture(receive_items=Mock(side_effect=OSError("failed refresh")))
    with pytest.raises(OSError, match="failed refresh"):
        service.handle("ReceivedItems", {"index": 0})
    assert events == ["ready"]
    trace.follow.assert_not_called()


def setup_fixture(**overrides):
    events = []
    callbacks = ConnectedSetupCallbacks(
        initialize_bridge=lambda: events.append("bridge"),
        initialize_deathlink=lambda: events.append("deathlink"),
        record_checks=lambda checks: events.append(("checks", checks)),
        publish=lambda: events.append("publish"),
        configure_probe=lambda: events.append("probe"),
        warn_pak=lambda: events.append("warn"),
        compatible_pak=lambda: True,
        pak_status=lambda: "wrong pak",
        save_guard=lambda: True,
        disable_checks=lambda: events.append("disable"),
        activation_pending=lambda: False,
        game_running=lambda: False,
        schedule_activation=lambda args: events.append(("activate", args)),
        save_status=lambda: "wrong save",
        disconnect_guard=lambda reason, status: events.append(("disconnect", reason, status)),
        enroll=lambda: events.append("enroll"),
        enable_checks=lambda: events.append("enable"),
        start_bridge=lambda: events.append("start"),
    )
    return ConnectedSetupService(replace(callbacks, **overrides)), events


def test_connected_setup_enables_checks_only_after_pak_and_save_admission():
    service, events = setup_fixture()
    service.complete({"checked_locations": [7]})
    assert events == [
        "bridge",
        "deathlink",
        ("checks", [7]),
        "publish",
        "probe",
        "warn",
        "enroll",
        "enable",
        "start",
    ]


def test_connected_setup_pak_mismatch_never_attempts_save_activation():
    service, events = setup_fixture(
        compatible_pak=lambda: False,
        save_guard=Mock(side_effect=AssertionError("save guard after incompatible pak")),
    )
    service.complete({})
    assert events[-1] == ("disconnect", "installed_pak_mismatch", "wrong pak")
    assert "enable" not in events
    assert "start" not in events


@pytest.mark.parametrize("pending,running", [(False, False), (False, True), (True, False), (True, True)])
def test_connected_save_guard_chooses_activation_or_disconnect(pending, running):
    service, events = setup_fixture(
        save_guard=lambda: False, activation_pending=lambda: pending, game_running=lambda: running
    )
    args = {"checked_locations": []}
    service.complete(args)
    assert events[-2] == "disable"
    if not pending and not running:
        assert events[-1] == ("activate", args)
        assert events[-1][1] is not args
    else:
        assert events[-1] == ("disconnect", "save_seed_mismatch", "wrong save")
    assert "enroll" not in events
    assert "start" not in events


def test_connected_setup_guard_inputs_are_observed_after_configuration():
    ready = False

    def configure():
        nonlocal ready
        ready = True

    service, events = setup_fixture(configure_probe=configure, compatible_pak=lambda: ready)
    service.complete({})
    assert events[-1] == "start"


def test_connected_setup_configuration_failure_stops_later_effects():
    service, events = setup_fixture(configure_probe=Mock(side_effect=OSError("configuration failed")))
    with pytest.raises(OSError, match="configuration failed"):
        service.complete({})
    assert events == ["bridge", "deathlink", ("checks", []), "publish"]


@pytest.mark.parametrize("live_context", [False, True])
def test_context_connection_waits_for_native_context_without_importing_old_log(tmp_path, live_context):
    from ..Client import KH3Context
    from ..transport.pipe_transport import SET, register, unregister

    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = tmp_path
    ctx.bridge_ue4ss_log = tmp_path / "UE4SS.log"
    ctx.bridge_ue4ss_log.write_text(
        "Runtime context refresh hook=old | world=World /Game/Maps/old/old | outer=Package old\n",
        encoding="utf-8",
    )
    for name in (
        "initialize_bridge", "_initialize_connected_kh3_deathlink", "log_location_check_transport_event",
        "write_bridge_files", "configure_probe_for_current_bridge", "warn_if_installed_pak_mismatch",
        "request_auto_enroll_connected_kh3_run", "start_integrated_bridge",
    ):
        setattr(ctx, name, Mock())
    ctx.is_installed_pak_compatible = Mock(return_value=True)
    ctx.refresh_save_seed_guard = Mock(return_value=True)
    transport = register(tmp_path)
    native = {"world": "World /Game/Maps/current/current", "process_id": 123}
    try:
        if live_context:
            transport.accept([(SET, "runtime_context.json", 1, json.dumps(native).encode())])
        # Repeated setup covers reconnects both before and after the DLL publishes.
        for _ in range(2):
            ctx._finish_connected_kh3_setup({"checked_locations": []})
            assert ctx.kh3_check_reporting_enabled
            assert transport.read("runtime_context.json") == (native if live_context else None)
            assert "runtime_context.json" not in transport.pending
            assert not (tmp_path / "runtime_context.json").exists()
        assert ctx.start_integrated_bridge.call_count == 2
    finally:
        unregister(transport)
