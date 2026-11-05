"""Prevent AP receipts from crossing seed/slot boundaries on reconnect or restart."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION, BridgeProtocolError, validate_protocol_payload
from ..bridge_support.files import atomic_write_json, load_optional_json_file
from ..Client import KH3Context
from ..client_support.item_sync_service import ItemSyncInput, ItemSyncService
from ..client_support.offline_bridge_service import OfflineBridgeInitializationInput
from . import test_client_status as status_tests

SEED = {"seed_name": "22222222222222222222", "slot": 1, "slot_name": "Sora"}
OLD_SEED = {**SEED, "seed_name": "11111111111111111111"}


@pytest.fixture
def items():
    return ItemSyncService(
        protocol_name=BRIDGE_PROTOCOL_NAME, protocol_version=BRIDGE_PROTOCOL_VERSION,
        load_optional_json=load_optional_json_file, validate_protocol=validate_protocol_payload,
        protocol_error=BridgeProtocolError, resolve_item_display_name=lambda name: name,
        log_warning=lambda *_: None,
    )


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("owner", [OLD_SEED, {**SEED, "slot": 2}, {}])
def test_cached_receipts_require_current_seed_and_slot(tmp_path, items, connected, legacy, owner):
    cached = {**items.empty_payload(), "items": [{"index": 0, "item_name": "Old reward"}]}
    if not legacy:
        cached["kh3_seed"] = owner
    atomic_write_json(tmp_path / "ap_items.json", cached)
    atomic_write_json(tmp_path / "ap_status.json", owner)
    built = {**items.empty_payload(), "kh3_seed": SEED}

    result = items.payload_for_refresh(built, bridge_dir=tmp_path, ap_connected=connected)

    assert result["items"] == []
    assert items.received_item_count([], bridge_dir=tmp_path, ap_connected=connected,
                                     expected_seed=SEED) == 0


@pytest.mark.parametrize("connected", [False, True])
def test_same_seed_reconnect_preserves_receipts_with_explicit_owner(tmp_path, items, connected):
    cached = {**items.empty_payload(), "kh3_seed": SEED,
              "items": [{"index": 0, "item_name": "Current reward"}]}
    atomic_write_json(tmp_path / "ap_items.json", cached)
    atomic_write_json(tmp_path / "ap_status.json", {**SEED, "seed_name": "AP_" + SEED["seed_name"]})
    built = {**items.empty_payload(), "kh3_seed": SEED}

    result = items.payload_for_refresh(built, bridge_dir=tmp_path, ap_connected=connected)

    assert result["items"] == cached["items"]
    assert result["kh3_seed"] == SEED
    assert items.payload_for_refresh(built, bridge_dir=tmp_path, ap_connected=True,
                                     received_items_snapshot_ready=True)["items"] == []


def test_ownerless_receipts_are_not_adopted_from_status(tmp_path, items):
    atomic_write_json(tmp_path / "ap_items.json", {
        **items.empty_payload(), "items": [{"index": 0, "item_name": "Ownerless reward"}],
    })
    atomic_write_json(tmp_path / "ap_status.json", SEED)
    assert items.load_cached_payload(tmp_path, expected_seed=SEED)["items"] == []


def test_receipt_owner_cannot_be_relabelled_by_new_status(tmp_path, items):
    atomic_write_json(tmp_path / "ap_items.json", {
        **items.empty_payload(), "kh3_seed": OLD_SEED, "items": [{"index": 0}],
    })
    atomic_write_json(tmp_path / "ap_status.json", SEED)
    assert items.load_cached_payload(tmp_path, expected_seed=SEED)["items"] == []


def test_normalized_receipts_carry_owner_even_when_empty(items):
    for receipts in ([], [SimpleNamespace(item=1, location=10, player=1, flags=0)]):
        source = ItemSyncInput(
            received_items=receipts, game="Kingdom Hearts III", player_names={1: "Sora"},
            location_id_to_bridge_key={}, slot_data={"kh3_seed": SEED}, bridge_save_dir=None,
            lookup_item_name=lambda *_: "Potion", lookup_location_name=lambda *_: "Chest",
        )
        for method in (items.build_items_payload, items.cached_items_payload):
            payload = method(source)
            assert payload["kh3_seed"] == SEED
            assert len(payload["items"]) == len(receipts)


@pytest.mark.parametrize("owner", [OLD_SEED, {**SEED, "slot": 2}, {}])
def test_restart_drops_mismatched_cached_items_before_relabelling_bridge(tmp_path, items, owner):
    atomic_write_json(tmp_path / "ap_status.json", {
        **owner, "player_name": "Sora", "checked_locations": [10], "goal_complete": True,
    })
    atomic_write_json(tmp_path / "ap_items.json", {**items.empty_payload(), "items": [{"index": 0}]})
    metadata = {"seed": SEED["seed_name"], "slot": 1, "slot_name": "Sora"}
    source = OfflineBridgeInitializationInput(
        current_bridge_dir=None, bridge_root=tmp_path.parent, mods_dir=tmp_path / "mods",
        expected_seed=SEED["seed_name"], auth="Sora", server_address=None, team=None,
    )
    # Recreate services on each pass, as when closing and reopening the client.
    for _ in range(2):
        service = status_tests.TestOfflineBridgeService.build_service(
            load_optional_json=load_optional_json_file, load_cached_items=items.load_cached_payload,
            atomic_write_json=atomic_write_json,
        )
        preserve = service.offline_bridge_matches(metadata, tmp_path)
        service.write_offline_files(
            source=source, metadata=metadata, pak_path=tmp_path / "seed.pak", bridge_dir=tmp_path,
            seed_name=SEED["seed_name"], slot_data={"kh3_seed": SEED}, location_count=1,
            preserve_previous_status=preserve,
        )
        assert load_optional_json_file(tmp_path / "ap_items.json")["items"] == []
        status = load_optional_json_file(tmp_path / "ap_status.json")
        assert status["received_item_count"] == 0
        assert not status["goal_complete"]
        assert status["checked_locations"] == []


@pytest.mark.parametrize("stage", ["room_info", "connected"])
def test_new_room_cannot_publish_into_previous_bridge(stage):
    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = Path("old-run")
    ctx._kh3_bridge_seed_scope = OLD_SEED
    ctx.seed_name = SEED["seed_name"]
    ctx.slot_data = {"kh3_seed": OLD_SEED if stage == "room_info" else SEED}
    ctx.build_items_payload = Mock(side_effect=AssertionError("Cross-seed publication"))
    ctx.write_bridge_files()
    ctx.build_items_payload.assert_not_called()


@pytest.mark.parametrize("seed,slot,expected", [
    (SEED["seed_name"], 1, "managed-run"),
    (OLD_SEED["seed_name"], 1, ""),
    (SEED["seed_name"], 2, ""),
])
def test_stale_selected_profile_cannot_select_previous_bridge(seed, slot, expected):
    ctx = KH3Context.__new__(KH3Context)
    ctx.kh3_active_connection_profile_run_id = "run-id"
    ctx.seed_name, ctx.slot = seed, slot
    run = SimpleNamespace(seed_id=SEED["seed_name"], player=1, bridge_dir_name="managed-run")
    ctx._get_run_manager_service = Mock(return_value=SimpleNamespace(get_run=lambda _: run))
    assert ctx._active_kh3_bridge_dir_name() == expected
