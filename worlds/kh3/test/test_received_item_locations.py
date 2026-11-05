"""Received locations belong to the sender's slot, including other KH3 seeds."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..Client import KH3Context
from ..client_support.item_sync_service import ItemSyncInput
from .test_client_status import TestItemSyncService as ItemSyncFixture


@pytest.mark.parametrize("cached", [False, True])
def test_sender_slot_lookup_preserves_remote_names_and_local_descriptions(cached):
    lookup = Mock(side_effect=lambda location, slot: f"Slot {slot} location {location}")
    source = ItemSyncInput(
        received_items=[SimpleNamespace(item=1, location=10, player=p, flags=0) for p in (1, 2, 3)],
        game="Kingdom Hearts III", player_names={1: "Sora", 2: "Other game", 3: "Other Sora"},
        location_id_to_bridge_key={10: "TreasureHE:HE_SBOX_001"},
        slot_data={"kh3_seed": {"seed_name": "lookup-test", "slot": 1}, "bridge": {"locations": [
            {"bridge_key": "TreasureHE:HE_SBOX_001", "location_description": "Local randomized chest"},
        ]}},
        bridge_save_dir=None, lookup_item_name=lambda *_: "Potion", lookup_location_name=lookup,
    )
    service = ItemSyncFixture.build_service()
    payload = (service.cached_items_payload if cached else service.build_items_payload)(source)
    assert [i["location_name"] for i in payload["items"]] == [
        "Local randomized chest", "Slot 2 location 10", "Slot 3 location 10",
    ]
    assert "bridge_key" in payload["items"][0]
    assert all("bridge_key" not in i for i in payload["items"][1:])
    assert [call.args for call in lookup.call_args_list] == [(10, 1), (10, 2), (10, 3)]


def test_context_uses_common_client_sender_slot_lookup():
    ctx = KH3Context.__new__(KH3Context)
    ctx.items_received = [SimpleNamespace(item=1, location=81000, player=2, flags=0)]
    ctx.player_names = {2: "ChecksFinder"}
    ctx.slot_data = {"kh3_seed": {"seed_name": "lookup-test", "slot": 1}}
    ctx.item_names = SimpleNamespace(lookup_in_game=Mock(return_value="Potion"))
    ctx.location_names = SimpleNamespace(lookup_in_slot=Mock(return_value="Tile 1"))
    ctx.item_sync_service = ItemSyncFixture.build_service()
    assert ctx.build_items_payload()["items"][0]["location_name"] == "Tile 1"
    ctx.location_names.lookup_in_slot.assert_called_once_with(81000, 2)
