"""Automatic connection activation using temporary saves and fake connections."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ..Client import KH3Context
from ..client_support.run_manager import KH3RunManagerService
from ..client_support.seed_import_service import KH3SeedImportError, KH3SeedImportService, KH3SeedPreparationResult
from ..client_support.seed_policy_service import KH3_MINIMUM_SEED_PAK_SIZE_BYTES

OLD = "11111111111111111111"
NEW = "22222222222222222222"


def test_offline_seed_switch_does_not_import_previous_runs_observations(tmp_path):
    ctx = KH3Context.__new__(KH3Context)
    for name in ("_reconcile_olympus_coliseum_reward_companion", "rebuild_location_display_indexes",
                 "write_offline_bridge_files", "ensure_bridge_observation_state",
                 "mark_existing_bridge_notifications_processed",
                 "mark_existing_runtime_game_over_processed", "configure_probe_for_current_bridge"):
        setattr(ctx, name, Mock())
    ctx._get_client_transport_service = Mock(return_value=Mock())
    ctx.offline_bridge_matches_installed_metadata = Mock(return_value=True)
    ctx.refresh_save_seed_guard = Mock(return_value=False)
    imported = []
    ctx.start_integrated_bridge = Mock(side_effect=lambda: imported.append("old_chest"))
    result = SimpleNamespace(offline_seed_name=NEW, auth="Sora", slot_data={"bridge": {"locations": []}},
        bridge_dir=tmp_path / "new", location_key_to_id={}, location_id_to_bridge_key={},
        metadata={"seed": NEW}, pak_path=tmp_path / "new.pak")

    assert ctx._apply_offline_bridge_initialization(result)
    assert imported == []
    assert not ctx.kh3_check_reporting_enabled
    ctx.refresh_save_seed_guard.assert_called_once()

    # A matching activated directory still supports the normal offline path.
    ctx.refresh_save_seed_guard.return_value = True
    assert ctx._apply_offline_bridge_initialization(result)
    assert imported == ["old_chest"]


def write_pak(directory, seed):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pakchunk9-Sora-randomizer-AP_{seed}-1_99_P.pak"
    path.write_bytes(b"P" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
    return path


@pytest.mark.parametrize("known_owner", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_copied_pak_automatically_preserves_previous_run_and_activates_target(tmp_path, known_owner, resume):
    def running():
        return False
    manager = KH3RunManagerService(
        run_root=tmp_path / "runs", bridge_root=tmp_path / "bridge",
        mods_dir=tmp_path / "mods", save_dir=tmp_path / "saves", process_running=running,
    )
    old = manager.import_pak(write_pak(tmp_path / "downloads", OLD)) if known_owner else None
    pak = write_pak(manager.mods_dir, NEW)
    target = manager.ensure_pak_managed(
        pak, expected_seed_id=NEW, server_address="localhost:38281", connection_slot_name="Sora",
    ).run
    relative = Path("kh3sv2/data/KHIII_slot1.bin")
    live = manager.save_dir / relative
    live.parent.mkdir(parents=True)
    live.write_bytes(b"old-progress")
    if resume:
        stored = target.save_snapshot_dir / relative
        stored.parent.mkdir(parents=True)
        stored.write_bytes(b"target-progress")
    archive = tmp_path / "protected-old-save.bin"

    def preserve(save_dir):
        assert save_dir == manager.save_dir
        live.replace(archive)
        return {"preserved_files": 1, "preserved_dir": str(archive.parent), "mode": "unmanaged_archive"}

    backup = Mock(side_effect=preserve)
    service = KH3SeedImportService(
        run_manager=manager, mods_dir=manager.mods_dir, generated_root=tmp_path / "generated",
        process_running=running, unidentified_save_preparer=backup,
    )
    result = service.activate_installed_run_for_connection(
        pak, expected_seed_id=NEW, expected_slot=1, server_address="localhost:38281",
        connection_slot_name="Sora", room_url=None, save_dir_override=manager.save_dir,
        active_save_seed_id=OLD, active_save_run_id=old.run_id if old else None,
    )
    assert result.installed_run.run_id == target.run_id
    assert result.activated_save_dir == manager.save_dir
    assert (old.save_snapshot_dir / relative if old else archive).read_bytes() == b"old-progress"
    if resume:
        assert live.read_bytes() == b"target-progress"
    else:
        assert not live.exists()
    assert backup.call_count == (0 if known_owner else 1)


@pytest.mark.parametrize("failure", ["running", "wrong_player", "backup"])
def test_auto_activation_stops_before_switch_on_failure(tmp_path, failure):
    manager = Mock()
    manager.pak_identity.return_value = SimpleNamespace(seed_id=NEW, player=2 if failure == "wrong_player" else 1)
    manager.installed_pak_count.return_value = 1
    manager.list_runs.return_value = []
    backup = Mock(return_value={"errors": ["backup failed"]})
    service = KH3SeedImportService(
        run_manager=manager, mods_dir=tmp_path, generated_root=tmp_path / "generated",
        process_running=lambda: failure == "running", unidentified_save_preparer=backup,
    )
    with pytest.raises(KH3SeedImportError):
        service.activate_installed_run_for_connection(
            tmp_path / "seed.pak", expected_seed_id=NEW, expected_slot=1,
            server_address="localhost:38281", connection_slot_name="Sora", room_url=None,
            save_dir_override=tmp_path / "saves", active_save_seed_id=OLD, active_save_run_id=None,
        )
    manager.activate.assert_not_called()
    if failure != "backup":
        backup.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "failure", "disconnect_before", "disconnect_during"])
def test_connected_activation_keeps_checks_blocked_until_handoff_finishes(tmp_path, outcome):
    ctx = KH3Context.__new__(KH3Context)
    ctx.server = object()
    ctx.seed_name, ctx.slot, ctx.auth = f"AP_{NEW}", 1, "Sora"
    ctx.server_address = "localhost:38281"
    ctx.bridge_save_dir = tmp_path / "Steam" / "SaveGames"
    ctx.kh3_check_reporting_enabled = False
    ctx.kh3_auto_activation_pending = True
    ctx._begin_kh3_run_action = Mock(return_value=True)
    ctx._finish_kh3_run_action = Mock()
    ctx.wait_for_integrated_bridge_shutdown = AsyncMock()
    ctx.connected_kh3_seed_pak_path = Mock(return_value=(tmp_path / "seed.pak", NEW))
    ctx.save_seed_association_for_dir = Mock(return_value=(OLD, 2))
    ctx.save_run_association_for_dir = Mock(return_value="old")
    ctx.kh3_room_page_url = Mock(return_value=None)
    ctx.store_save_seed_associations_for_targets = Mock()
    ctx.reset_bridge_observation_state = Mock()
    ctx.disconnect_for_seed_guard = Mock()
    run = SimpleNamespace(run_id="target", seed_id=NEW, slot_name="Sora", connection_slot_name="Sora")

    def activate(*args, **kwargs):
        ctx.wait_for_integrated_bridge_shutdown.assert_awaited_once()
        assert not ctx.kh3_check_reporting_enabled
        assert kwargs["save_dir_override"] == ctx.bridge_save_dir.resolve()
        if outcome == "failure":
            raise KH3SeedImportError("backup failed")
        if outcome == "disconnect_during":
            ctx.server = None
        return KH3SeedPreparationResult("activated", installed_run=run, activated_save_dir=ctx.bridge_save_dir)

    def finish(args):
        ctx.store_save_seed_associations_for_targets.assert_called_once_with(
            [ctx.bridge_save_dir.resolve()], NEW, run_id="target",
        )
        assert not ctx.kh3_check_reporting_enabled
        assert ctx.kh3_auto_activation_pending

    ctx._finish_connected_kh3_setup = Mock(side_effect=finish)
    service = SimpleNamespace(activate_installed_run_for_connection=Mock(side_effect=activate))
    ctx._get_seed_import_service = Mock(return_value=service)
    if outcome == "disconnect_before":
        ctx.wait_for_integrated_bridge_shutdown.side_effect = lambda: setattr(ctx, "server", None)
    asyncio.run(ctx._auto_activate_connected_kh3_run_action({"checked_locations": []}))
    assert not ctx.kh3_auto_activation_pending
    if outcome == "success":
        ctx._finish_connected_kh3_setup.assert_called_once()
        ctx.disconnect_for_seed_guard.assert_not_called()
    else:
        ctx._finish_connected_kh3_setup.assert_not_called()
    if outcome == "failure":
        ctx.store_save_seed_associations_for_targets.assert_not_called()
        ctx.disconnect_for_seed_guard.assert_called_once()
    if outcome == "disconnect_before":
        service.activate_installed_run_for_connection.assert_not_called()
    if outcome == "disconnect_during":
        ctx.store_save_seed_associations_for_targets.assert_called_once()


@pytest.mark.parametrize("running,pending,scheduled", [(False, False, True), (True, False, False), (False, True, False)])
def test_connection_guard_schedules_one_auto_activation(tmp_path, monkeypatch, running, pending, scheduled):
    from .. import Client as client_module

    ctx = KH3Context.__new__(KH3Context)
    ctx.bridge_dir = tmp_path
    ctx.slot_data = {}
    ctx.kh3_check_reporting_enabled = True
    ctx.kh3_auto_activation_pending = pending
    for name in ("initialize_bridge", "log_location_check_transport_event", "write_bridge_files",
                 "configure_probe_for_current_bridge", "warn_if_installed_pak_mismatch",
                 "disconnect_for_seed_guard", "request_auto_enroll_connected_kh3_run", "start_integrated_bridge"):
        setattr(ctx, name, Mock())
    ctx.update_death_link = AsyncMock()
    ctx.is_installed_pak_compatible = Mock(return_value=True)
    ctx.refresh_save_seed_guard = Mock(return_value=False)
    ctx.build_save_seed_status = Mock(return_value="Mismatch")
    ctx._auto_activate_connected_kh3_run_action = AsyncMock()
    monkeypatch.setattr(client_module, "is_process_running", lambda _: running)
    tasks = []
    monkeypatch.setattr(client_module.Utils, "async_start", lambda coro, **kw: (tasks.append(kw["name"]), coro.close()))
    ctx._finish_connected_kh3_setup({})
    assert ("KH3ConnectedRunActivation" in tasks) == scheduled
    assert ctx.disconnect_for_seed_guard.call_count == (0 if scheduled else 1)
    assert not ctx.kh3_check_reporting_enabled
    ctx.start_integrated_bridge.assert_not_called()
