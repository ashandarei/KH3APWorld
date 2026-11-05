"""Shared state application keeps each run transition's distinct identity rules."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..Bridge import BRIDGE_PROTOCOL_NAME, BRIDGE_PROTOCOL_VERSION, KH3_WORLD_VERSION
from ..Client import KH3Context
from ..client_support import bridge_status_payload as status


@pytest.fixture
def context():
    ctx = KH3Context.__new__(KH3Context)
    ctx.reset_server_state = Mock()
    ctx._keep_kh3_run_selection_disconnected = Mock()
    ctx._apply_kh3_run_server_address = Mock()
    ctx._get_client_transport_service = Mock(return_value=Mock())
    ctx.store_save_seed_associations_for_targets = Mock()
    ctx.reset_bridge_observation_state = Mock()
    ctx.locations_checked, ctx.locations_scouted = {1}, {2}
    ctx.checked_locations, ctx.missing_locations = {3}, {4}
    ctx.items_received = ["old"]
    ctx.location_key_to_id = {"old": 1}
    ctx.location_id_to_bridge_key = {1: "old"}
    ctx.location_id_to_display_name = {1: "Old"}
    ctx.location_name_to_display_name = {"old": "Old"}
    ctx.bridge_dir, ctx.offline_seed_name, ctx.seed_name = Path("old-bridge"), "AP_old", "AP_room"
    ctx.auth, ctx.username, ctx.password = "manual-login", "old-user", "retained-password"
    ctx.server_address = "retained-host:38281"
    ctx.kh3_auth_inferred_from_installed_pak = True
    ctx.save_seed_mismatch_active = True
    ctx.last_save_seed_status_warning, ctx.last_pak_status_warning = "save warning", "pak warning"
    ctx.kh3_restart_required_seed_id = "old"
    return ctx


@pytest.fixture
def run():
    return SimpleNamespace(
        run_id="new-run",
        connection_slot_name=" Custom ",
        effective_slot_name="Custom",
        seed_id="new",
        slot_name="Sora",
        player=1,
    )


@pytest.mark.parametrize("deleted", [True, False])
def test_activation_and_deletion_share_resets_without_erasing_their_distinct_profile_rules(context, run, deleted):
    checked = context.checked_locations
    if deleted:
        context._apply_deleted_active_kh3_run()
        assert context.auth is None
        assert context.kh3_active_connection_profile_run_id is None
        assert context.kh3_run_connection_slot_name == ""
        assert not context.kh3_auth_inferred_from_installed_pak
        context.store_save_seed_associations_for_targets.assert_not_called()
        context._keep_kh3_run_selection_disconnected.assert_called_once()
    else:
        context._apply_kh3_run_activation(SimpleNamespace(run=run), Path("configured-saves"))
        assert context.auth == "Custom"
        assert context.kh3_active_connection_profile_run_id == "new-run"
        assert context.kh3_run_connection_slot_name == "Custom"
        assert context.kh3_auth_inferred_from_installed_pak
        context.store_save_seed_associations_for_targets.assert_called_once_with(
            [Path("configured-saves")], "new", run_id="new-run"
        )
    assert context.checked_locations is checked and not checked
    assert not any(
        (context.locations_checked, context.locations_scouted, context.missing_locations, context.items_received)
    )
    assert context.bridge_dir is None and context.seed_name is None and context.offline_seed_name is None
    assert context.location_key_to_id == context.location_id_to_bridge_key == {}
    assert not context.kh3_check_reporting_enabled
    assert not context.save_seed_mismatch_active
    assert context.last_save_seed_status_warning == context.last_pak_status_warning == ""
    assert context.kh3_restart_required_seed_id is None
    context._get_client_transport_service().clear_payload_cache.assert_called_once()
    context.reset_server_state.assert_called_once()


def test_automatic_activation_preserves_current_auth_and_pak_warning(context, run):
    context._apply_auto_activated_connected_run(run)
    assert context.auth == "manual-login"
    assert context.seed_name == "AP_room"
    assert context.offline_seed_name == "AP_new"
    assert context.kh3_run_connection_slot_name == "Custom"
    assert context.last_pak_status_warning == "pak warning"
    assert not context.save_seed_mismatch_active
    assert context.kh3_restart_required_seed_id is None
    context.reset_bridge_observation_state.assert_called_once_with(reason="connected_run_auto_activation")


def test_prepared_seed_reapplies_its_login_after_offline_initialization(context, run):
    context.try_initialize_offline_bridge_from_installed_pak = Mock(
        side_effect=lambda: setattr(context, "auth", "metadata-login") or True
    )
    prepared = SimpleNamespace(
        installed_run=run,
        activated_save_dir=None,
        source_path=Path("seed.apkh3"),
        archived_source_path=None,
        warnings=(),
    )
    assert context._apply_prepared_seed_pak(prepared)
    assert context.auth == "Custom"
    assert context.kh3_active_connection_profile_run_id == "new-run"


@pytest.fixture
def snapshot():
    return status.BridgeStatusInput(
        reported_checks=(101, 102),
        total_locations=12,
        received_item_count=3,
        seed_name="AP_new",
        slot=None,
        team=None,
        player_name=None,
        previous_status={},
        connected=False,
        server_address="host:38281",
        goal_complete=False,
        last_processed_game_state_seq=5,
        installed_pak="compatible",
        save_seed="matched",
        check_reporting_blocked=False,
    )


@pytest.mark.parametrize(
    "seed,previous_seed,selected_player,expected",
    [
        ("AP_new", "AP_new", None, (7, 2, "Cached")),
        ("AP_new", "AP_new", "Current", (7, 2, "Current")),
        ("AP_new", "AP_other", None, (None, None, None)),
        (None, "AP_new", None, (None, None, None)),
    ],
)
def test_cached_reconnect_identity_requires_the_same_seed(snapshot, seed, previous_seed, selected_player, expected):
    source = replace(
        snapshot,
        seed_name=seed,
        player_name=selected_player,
        previous_status={"seed_name": previous_seed, "slot": 7, "team": 2, "player_name": "Cached"},
    )
    payload = status.build_bridge_status_payload(source)
    assert (payload["slot"], payload["team"], payload["player_name"]) == expected


def test_current_slot_never_uses_cached_identity(snapshot):
    source = replace(
        snapshot,
        slot=8,
        team=3,
        player_name="Current",
        previous_status={"seed_name": "AP_new", "slot": 7, "team": 2, "player_name": "Cached"},
    )
    payload = status.build_bridge_status_payload(source)
    assert (payload["slot"], payload["team"], payload["player_name"]) == (8, 3, "Current")


def test_status_payload_preserves_the_bridge_protocol(snapshot):
    assert status.build_bridge_status_payload(snapshot) == {
        "protocol_name": BRIDGE_PROTOCOL_NAME,
        "protocol_version": BRIDGE_PROTOCOL_VERSION,
        "world_version": KH3_WORLD_VERSION,
        "connected": False,
        "server_address": "host:38281",
        "slot": None,
        "team": None,
        "player_name": None,
        "seed_name": "AP_new",
        "checked_locations": [101, 102],
        "checked_location_count": 2,
        "total_location_count": 12,
        "received_item_count": 3,
        "goal_complete": False,
        "last_processed_game_state_seq": 5,
        "installed_pak": "compatible",
        "save_seed": "matched",
        "check_reporting_blocked": False,
    }
    assert not any(key.startswith("battle_level") for key in status.build_bridge_status_payload(snapshot))
