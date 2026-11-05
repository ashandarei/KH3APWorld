"""Minimap changes stay distinct from levels and stale runtime snapshots."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..Client import KH3Context
from ..client_support.poptracker import build_context_payload
from ..MinimapReference import minimap_display_name
from ..transport.pipe_transport import SET, decode, encode

WORLD = "/Game/Levels/ra/ra_01/ra_01"
PAWN = f"p_ex001_Pawn_C {WORLD}.ra_01:PersistentLevel.p_ex001_Pawn_C_0"


def contexts():
    return ({"schema": "kh3ap.runtime_status.v1", "map": {
                "asset_path": WORLD, "world_code": "ra", "level_id": "ra_01", "room_id": "ra_01"},
             "context": {"world": f"World {WORLD}.ra_01", "process_id": 10,
                         "object": PAWN + " @123 real=true"}},
            {"minimap_id": "ra01_03", "texture_id": "ra01_03",
             "texture_path": "/Game/UI/Icon/Map/ra01_03.ra01_03"})


def test_minimap_payload_preserves_level_and_exposes_logical_row():
    context, minimap = contexts()
    minimap["minimap_id"] = "ra01_03_2"
    payload = build_context_payload({**context, "minimap": minimap}, slot=1)
    assert payload["kh3_level_path"] == WORLD
    assert payload["kh3_minimap_id"] == "ra01_03_2"
    assert payload["kh3_minimap_texture_id"] == "ra01_03"
    assert payload["slot"] == 1
    assert payload["kh3_tracker_event"] == "map_changed"


def test_bounce_deduplicates_retries_reconnects_and_shared_textures():
    context, minimap = contexts()
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = Path("synthetic")
    ctx.slot, ctx.auth, ctx.seed_name = 1, "Player", "Seed"
    ctx.player_names = {1: "Player"}
    ctx.server = SimpleNamespace(socket=SimpleNamespace(closed=False))
    context["minimap"] = minimap
    sidecars = {"runtime_status.json": context}
    ctx._load_bridge_sidecar_payload = Mock(side_effect=sidecars.get)
    ctx.send_msgs = AsyncMock(side_effect=OSError("disconnected"))
    with pytest.raises(OSError):
        asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    ctx.send_msgs = AsyncMock()
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    assert ctx.send_msgs.await_count == 1
    minimap["minimap_id"] = "ra01_03_2"  # same texture, different logical area
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    assert ctx.send_msgs.await_count == 2
    context["minimap"] = {}  # temporary HUD loss
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    assert ctx.send_msgs.await_count == 2
    context["minimap"] = minimap
    minimap["minimap_id"] = "ra01_03"  # returning to the previous area
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    assert ctx.send_msgs.await_count == 3
    ctx._get_poptracker_sync_service().reset()  # reconnect publishes current area
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    assert ctx.send_msgs.await_count == 4
    assert ctx.send_msgs.call_args.args[0][0]["slots"] == [1]
    context["map"] = {"asset_path": "/Game/Levels/tt/tt_01/tt_01", "world_code": "tt"}
    context["minimap"] = {}
    asyncio.run(ctx.handle_poptracker_runtime_context_bounce())
    assert ctx.send_msgs.await_count == 5
    assert "kh3_minimap_id" not in ctx.send_msgs.call_args.args[0][0]["data"]


def test_minimap_snapshot_is_a_valid_pipe_channel():
    packet = [(SET, "runtime_minimap_context.json", 1, b'{"minimap_id":"ra01_01"}')]
    assert decode(encode(0, packet)) == (0, packet)


@pytest.mark.parametrize("minimap_id,name", [
    ("ra01_01", "The Forest / Tower"),
    ("ra01_02", "The Forest / Hills"),
    ("ra01_future", "Unknown minimap (ra01_future)"),
])
def test_readable_names_are_additive_and_unknown_ids_survive(minimap_id, name):
    context, minimap = contexts()
    minimap["minimap_id"] = minimap_id
    payload = build_context_payload({**context, "minimap": minimap})
    assert payload["kh3_world_name"] == "Kingdom of Corona"
    assert payload["kh3_minimap_name"] == name
    assert payload["kh3_minimap_id"] == minimap_id
    assert payload["kh3_level_path"] == WORLD


def test_reference_does_not_label_another_worlds_minimap():
    assert minimap_display_name("ra01_01", "tt") == "Unknown minimap (ra01_01)"


def test_level_only_payload_has_readable_world_without_an_invented_area():
    context, _ = contexts()
    payload = build_context_payload(context)
    assert payload["kh3_world_name"] == "Kingdom of Corona"
    assert "kh3_minimap_name" not in payload
