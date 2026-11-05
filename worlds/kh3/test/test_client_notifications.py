"""Server messages reach the base client through the real KH3 notification path."""

import asyncio
import copy
import logging
from unittest.mock import Mock

import pytest

from CommonClient import process_server_cmd
from NetUtils import JSONtoTextParser, RawJSONtoTextParser

from ..Client import KH3Context


@pytest.mark.parametrize("with_ui", [False, True])
@pytest.mark.parametrize("location", [False, True])
def test_server_message_is_translated_and_presented_once(caplog, with_ui, location):
    ctx = KH3Context.__new__(KH3Context)
    ctx.slot = 1
    ctx.location_id_to_display_name = {100: "Olympus / Courtyard Chest"}
    ctx.location_name_to_display_name = {}
    ctx.ui = Mock() if with_ui else None
    ctx.jsontotextparser = JSONtoTextParser(ctx)
    ctx.rawjsontotextparser = RawJSONtoTextParser(ctx)
    node = {"type": "location_id", "text": "100", "player": 1} if location else {"text": "Welcome to Archipelago"}
    packet = {"cmd": "PrintJSON", "data": [node]}
    original = copy.deepcopy(packet)
    expected_text = "Olympus / Courtyard Chest" if location else "Welcome to Archipelago"

    with caplog.at_level(logging.INFO):
        asyncio.run(process_server_cmd(ctx, packet))

    assert packet == original
    for logger_name in ("FileLog", "StreamLog"):
        records = [record for record in caplog.records if record.name == logger_name]
        assert len(records) == 1
        assert expected_text in records[0].getMessage()
    if with_ui:
        expected_node = {"type": "location_name", "text": expected_text, "player": 1} if location else node
        ctx.ui.print_json.assert_called_once_with([expected_node])
