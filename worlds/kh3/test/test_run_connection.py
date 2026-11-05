"""Profile updates retain action ownership and use connection state after I/O."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..client_support.run_connection import RunConnectionCallbacks, RunConnectionService, normalize_server_address
from ..client_support.run_manager import KH3RunManagerError


@pytest.fixture
def connection():
    manager = Mock()
    manager.update_connection_profile.return_value = SimpleNamespace(
        active=True,
        slot_name="pak-slot",
        effective_slot_name="edited-slot",
        server_address="host:38281",
    )
    callbacks = RunConnectionCallbacks(
        begin=Mock(return_value=True),
        finish=Mock(),
        is_busy=Mock(return_value=False),
        busy_label=lambda: "busy",
        apply_active=Mock(),
    )
    service = RunConnectionService(lambda: manager, callbacks)
    return service, manager, callbacks


def update(service, **options):
    return service.update("run", slot_name="slot", server="host", port="38281", room_url="", **options)


@pytest.mark.parametrize("error", [KH3RunManagerError("bad profile"), OSError("disk"), asyncio.CancelledError()])
def test_profile_update_releases_its_action_on_errors(connection, error):
    service, manager, callbacks = connection
    manager.update_connection_profile.side_effect = error
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(update(service))
    else:
        asyncio.run(update(service))
    callbacks.finish.assert_called_once()
    callbacks.apply_active.assert_not_called()


@pytest.mark.parametrize("background", [True, False])
def test_busy_profile_update_does_not_mutate_or_release_someone_elses_action(connection, background):
    service, manager, callbacks = connection
    callbacks.begin.return_value = False
    callbacks.is_busy.return_value = True
    asyncio.run(update(service, show_activity=not background))
    manager.update_connection_profile.assert_not_called()
    callbacks.finish.assert_not_called()


def test_background_edit_of_inactive_profile_keeps_selection_and_action_state(connection):
    service, manager, callbacks = connection
    manager.update_connection_profile.return_value.active = False
    asyncio.run(update(service, show_activity=False))
    callbacks.begin.assert_not_called()
    callbacks.finish.assert_not_called()
    callbacks.apply_active.assert_not_called()


def test_connection_that_opens_during_profile_io_defers_the_edited_authentication():
    from ..Client import KH3Context

    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_run_action_running = False
    ctx.server = None
    ctx.auth = "connected-slot"
    ctx.server_address = "connected-server"
    ctx.ui = Mock()
    updated = SimpleNamespace(
        active=True,
        run_id="run",
        connection_slot_name="edited-slot",
        effective_slot_name="edited-slot",
        slot_name="pak-slot",
        server_address="edited-server",
    )
    manager = Mock()

    def edit(*args, **kwargs):
        ctx.server = object()
        return updated

    manager.update_connection_profile.side_effect = edit
    ctx._get_run_manager_service = Mock(return_value=manager)
    asyncio.run(
        ctx._update_kh3_run_connection_profile_action(
            "run",
            slot_name="slot",
            server="host",
            port="38281",
            room_url="",
        )
    )
    assert ctx.auth == "connected-slot" and ctx.server_address == "connected-server"
    assert ctx.kh3_run_connection_slot_name == "edited-slot"
    ctx.ui.update_address_bar.assert_called_once_with("edited-server")
    assert not ctx.kh3_run_action_running


@pytest.mark.parametrize(
    "address,expected",
    [
        (" HOST ", "host:38281"),
        ("wss://HOST", "wss://host:38281"),
        ("ws://[::1]:1234", "[::1]:1234"),
        ("host:bad", None),
        ("ws://[broken", None),
        ("", None),
    ],
)
def test_server_normalization_preserves_the_existing_connection_identity(address, expected):
    assert normalize_server_address(address) == expected
