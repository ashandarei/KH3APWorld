"""Connection coordination preserves admission, prompting, and await boundaries."""

import asyncio
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..client_support.connection_flow import ConnectionFlowCallbacks, ConnectionFlowService, ConnectionState


@pytest.fixture
def flow():
    state = SimpleNamespace(
        auth=None,
        username="Previous",
        password=None,
        managed_slot_name="",
        bootstrap_pending=True,
        pre_auth_blocked=False,
    )
    effects = {field.name: Mock() for field in fields(ConnectionFlowCallbacks)}
    effects.update(
        state=lambda: ConnectionState(**vars(state)),
        confirm_cloud=AsyncMock(return_value=True),
        disconnect=AsyncMock(),
        prepare_seed=AsyncMock(return_value=SimpleNamespace(status="ready")),
        has_installed_pak=Mock(return_value=True),
        game_running=Mock(return_value=False),
        prompt_slot=AsyncMock(return_value=True),
        guard_pak=Mock(return_value=True),
        text_prompt=Mock(return_value=None),
        get_username=AsyncMock(),
        send_connect=AsyncMock(),
        suspend_run=AsyncMock(return_value=True),
        close_socket=AsyncMock(),
    )
    callbacks = ConnectionFlowCallbacks(**effects)
    callbacks.set_bootstrap.side_effect = lambda value: setattr(state, "bootstrap_pending", value)
    callbacks.set_auth.side_effect = lambda auth, inferred: setattr(state, "auth", auth)
    callbacks.clear_prompt_profile.side_effect = lambda: (
        setattr(state, "auth", None),
        setattr(state, "managed_slot_name", ""),
    )
    return ConnectionFlowService(callbacks), callbacks, state


def test_cloud_admission_failure_stops_before_disconnect_and_preparation(flow):
    service, callbacks, _ = flow
    callbacks.confirm_cloud.return_value = False
    asyncio.run(service.connect("host:38281"))
    callbacks.disconnect.assert_not_awaited()
    callbacks.reset_attempt.assert_not_called()
    callbacks.prepare_seed.assert_not_awaited()
    callbacks.start_server.assert_not_called()


@pytest.mark.parametrize("outcome", ["running", "cancelled", "accepted"])
def test_connecting_without_a_pak_requires_a_stopped_game_and_an_accepted_slot(flow, outcome):
    service, callbacks, _ = flow
    callbacks.prepare_seed.return_value = SimpleNamespace(status="no_source")
    callbacks.has_installed_pak.return_value = False
    callbacks.game_running.return_value = outcome == "running"
    callbacks.prompt_slot.return_value = outcome == "accepted"
    asyncio.run(service.connect("host:38281"))
    assert callbacks.prompt_slot.await_count == int(outcome != "running")
    assert callbacks.prepare_connection.call_count == int(outcome == "accepted")
    assert callbacks.start_server.call_count == int(outcome == "accepted")
    callbacks.guard_pak.assert_not_called()
    if outcome == "accepted":
        callbacks.set_bootstrap.assert_called_once_with(True)
        callbacks.start_server.assert_called_once_with("host:38281")


def test_authentication_rechecks_login_after_the_parent_password_prompt(flow):
    service, callbacks, state = flow
    parent = AsyncMock(side_effect=lambda requested: setattr(state, "auth", "Provided"))
    asyncio.run(service.authenticate(True, parent_auth=parent))
    parent.assert_awaited_once_with(True)
    callbacks.prompt_slot.assert_not_awaited()
    callbacks.infer_slot.assert_not_called()
    callbacks.get_username.assert_awaited_once()
    callbacks.send_connect.assert_awaited_once()


@pytest.mark.parametrize("failure", ["preparation", "guard"])
def test_authentication_preparation_failure_disconnects_before_username_or_send(flow, failure):
    service, callbacks, state = flow
    state.bootstrap_pending = False
    if failure == "preparation":
        callbacks.prepare_seed.return_value = None
    else:
        callbacks.guard_pak.return_value = False
    asyncio.run(service.authenticate(False, parent_auth=AsyncMock()))
    callbacks.disconnect.assert_awaited_once()
    callbacks.get_username.assert_not_awaited()
    callbacks.send_connect.assert_not_awaited()
    assert callbacks.guard_pak.call_count == int(failure == "guard")


@pytest.mark.parametrize("managed", [True, False])
def test_authentication_distinguishes_managed_and_inferred_logins(flow, managed):
    service, callbacks, state = flow
    state.bootstrap_pending = False
    state.managed_slot_name = "Remembered" if managed else ""
    callbacks.infer_slot.return_value = "FromPak"
    asyncio.run(service.authenticate(False, parent_auth=AsyncMock()))
    callbacks.set_auth.assert_called_once_with("Remembered" if managed else "FromPak", not managed)
    assert callbacks.infer_slot.call_count == int(not managed)
    callbacks.send_connect.assert_awaited_once()


@pytest.mark.parametrize("answer,accepted", [(" Custom ", True), (None, False), (" ", False)])
def test_slot_prompt_captures_the_previous_login_before_clearing_the_profile(flow, answer, accepted):
    service, callbacks, state = flow
    state.auth = "PreviousLogin"

    async def prompt(**kwargs):
        assert state.auth is None
        assert kwargs["initial_text"] == "PreviousLogin"
        return answer

    callbacks.text_prompt.return_value = AsyncMock(side_effect=prompt)
    assert asyncio.run(service.prompt_slot()) is accepted
    callbacks.clear_prompt_profile.assert_called_once()
    if accepted:
        callbacks.set_auth.assert_called_once_with("Custom", False)
    else:
        callbacks.set_auth.assert_not_called()


def test_console_slot_prompt_normalizes_the_username_result(flow):
    service, callbacks, state = flow
    callbacks.get_username.side_effect = lambda: setattr(state, "auth", " ConsoleLogin ")
    assert asyncio.run(service.prompt_slot())
    callbacks.clear_username.assert_called_once()
    callbacks.set_auth.assert_called_once_with("ConsoleLogin", False)


@pytest.mark.parametrize("seed_mismatch", [True, False])
@pytest.mark.parametrize("preserved", [True, False])
def test_room_mismatch_preserves_the_run_or_blocks_authentication(flow, seed_mismatch, preserved):
    service, callbacks, _ = flow
    callbacks.suspend_run.return_value = preserved
    asyncio.run(
        service.guard_room(seed_mismatch=seed_mismatch, affinity_mismatch=not seed_mismatch, seed_name="AP_new")
    )
    callbacks.suspend_run.assert_awaited_once()
    assert callbacks.block_pre_auth.call_count == int(not preserved)
    assert callbacks.close_socket.await_count == int(not preserved)
    assert callbacks.set_bootstrap.call_count == int(preserved)


@pytest.mark.parametrize("stage", ["preservation", "prompt", "send", "cancellation"])
def test_rejected_slot_recovery_clears_pending_on_every_exit(flow, stage):
    service, callbacks, _ = flow
    callbacks.suspend_run.return_value = stage != "preservation"
    callbacks.prompt_slot.return_value = stage != "prompt"
    error = asyncio.CancelledError() if stage == "cancellation" else OSError("send")
    if stage in {"send", "cancellation"}:
        callbacks.send_connect.side_effect = error
        with pytest.raises(type(error)):
            asyncio.run(service.recover_rejected_slot())
    else:
        asyncio.run(service.recover_rejected_slot())
        callbacks.send_connect.assert_not_awaited()
        callbacks.allow_reconnect.assert_not_called()
        callbacks.close_socket.assert_awaited_once()
    callbacks.clear_recovery_pending.assert_called_once()
