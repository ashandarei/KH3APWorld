"""KH3 connection context around the unmodified Archipelago server loop."""
from __future__ import annotations

from urllib.parse import urlparse

from CommonClient import server_loop as common_server_loop
import websockets


async def server_loop(context, address=None):
    target = address or context.server_address
    context.connection_attempt_address = target
    try:
        if target:
            normalized = target.replace("archipelago://", "ws://")
            if "://" not in normalized:
                normalized = "ws://" + normalized
            try:
                urlparse(normalized).port
            except ValueError as error:
                raise websockets.InvalidURI(normalized, str(error)) from error
    except websockets.InvalidURI:
        context.handle_connection_loss("The server address contains an invalid URI or port.")
        context.server_task = None
        return
    await common_server_loop(context, address)
