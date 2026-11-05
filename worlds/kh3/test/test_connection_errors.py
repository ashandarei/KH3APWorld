"""Expected network failures show the attempted endpoint without a traceback."""

import asyncio
import logging
import queue
import socket
from unittest.mock import AsyncMock, Mock

import pytest
import websockets

from ..client_support.network import server_loop

from ..Client import KH3Context
from ..gui.manager import LogHandler


@pytest.mark.parametrize("address,endpoint", [
    ("127.0.0.1:38499", "127.0.0.1:38499"),
    ("missing.invalid:38282", "missing.invalid:38282"),
    ("missing.invalid", "missing.invalid:38281"),
    ("[::1]:38499", "[::1]:38499"),
    ("archipelago://Sora:secret@missing.invalid:38282", "missing.invalid:38282"),
])
@pytest.mark.parametrize("error,explanation", [
    (socket.gaierror(11001, "getaddrinfo failed"), "Could not resolve the server name"),
    (ConnectionRefusedError(10061, "No connection could be made"), "Connection refused by the server"),
    (TimeoutError("opening handshake timed out"), "Connection timed out"),
    (OSError("Multiple exceptions: connection refused"), "Failed to connect to the multiworld server"),
])
def test_failed_attempt_logs_modal_message_with_attempted_server(monkeypatch, caplog, address, endpoint, error, explanation):
    ctx = KH3Context.__new__(KH3Context)
    ctx.server_address = "previous.example:38290"
    ctx.username = None
    ctx.disconnected_intentionally = True
    ctx.cancel_autoreconnect = Mock()
    ctx.connection_closed = AsyncMock()
    ctx.gui_error = Mock(return_value=Mock())
    connect = AsyncMock(side_effect=error)
    monkeypatch.setattr(websockets, "connect", connect)
    messages = queue.SimpleQueue()
    handler = LogHandler(messages)
    log = logging.getLogger("Client")
    log.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO, logger="Client"):
            asyncio.run(server_loop(ctx, address))
    finally:
        log.removeHandler(handler)

    ctx.gui_error.assert_called_once()
    title, text = ctx.gui_error.call_args.args
    assert f"Server: {endpoint}" in text
    assert explanation in text
    assert "previous.example" not in text and "secret" not in text
    assert "Traceback" not in text and "getaddrinfo" not in text
    failures = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(failures) == 1
    assert failures[0].getMessage() == f"{title}\n{text}"
    assert failures[0].exc_info is None
    rendered = []
    while not messages.empty():
        rendered.append(messages.get())
    assert f"{title}\n{text}" in rendered
    assert not any("Traceback" in line for line in rendered)
    assert ctx._messagebox_connection_loss is ctx.gui_error.return_value
    ctx.connection_closed.assert_awaited_once()


@pytest.mark.parametrize("port", ["bad-port", "99999"])
def test_invalid_uri_keeps_bad_endpoint_visible_without_credentials(monkeypatch, caplog, port):
    ctx = KH3Context.__new__(KH3Context)
    ctx.server_address = "previous.example:38290"
    ctx.disconnected_intentionally = True
    ctx.cancel_autoreconnect = Mock()
    ctx.connection_closed = AsyncMock()
    ctx.gui_error = Mock()
    connect = AsyncMock()
    monkeypatch.setattr(websockets, "connect", connect)
    with caplog.at_level(logging.ERROR, logger="Client"):
        asyncio.run(server_loop(ctx, f"ws://user:secret@missing.invalid:{port}/path"))
    text = ctx.gui_error.call_args.args[1]
    assert f"Server: missing.invalid:{port}" in text
    assert "invalid URI" in text
    assert "secret" not in text and "/path" not in text
    assert all(record.exc_info is None for record in caplog.records)
    connect.assert_not_awaited()


def test_unexpected_connection_error_retains_traceback(caplog):
    ctx = KH3Context.__new__(KH3Context)
    ctx.gui_error = Mock()
    with caplog.at_level(logging.ERROR, logger="Client"):
        try:
            raise RuntimeError("unexpected packet handling bug")
        except RuntimeError:
            ctx.handle_connection_loss("Lost connection to the multiworld server")
    assert caplog.records[-1].exc_info is not None
    assert "Traceback" in caplog.text
    assert str(ctx.gui_error.call_args.args[1]) == "unexpected packet handling bug"
