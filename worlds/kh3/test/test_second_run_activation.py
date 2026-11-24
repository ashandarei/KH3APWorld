"""Save Manager handoffs between a copied seed pak and the previous seed's save folder."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import Utils

from ..client_support.run_actions import RunSelection
from ..client_support.run_manager import KH3RunManagerError
from ..client_support.run_models import RunOwnsSaveDirectory
from ..client_support.run_pruning import RunPruneCallbacks, RunPruningService
from ..client_support.seed_guard import SeedGuardService
from ..client_support.seed_import_service import KH3SeedImportService
from . import test_run_manager as fixtures


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(Utils.user_path, "cached_path", str(tmp_path), raising=False)
    monkeypatch.setattr(Utils.persistent_load, "storage", None, raising=False)
    return fixtures.KH3RunManagerTest()._service(tmp_path)


OLD = "69341036334653361371"
NEW = "04234211236097921587"


def import_run(manager, folder, seed):
    return manager.import_pak(fixtures._write_seed_pak(folder, seed=seed, player=1, slot="Violin"))


def test_seed_only_owner_without_installed_pak_survives_a_round_trip(manager, tmp_path):
    old = import_run(manager, tmp_path / "downloads", OLD)
    new = import_run(manager, tmp_path / "downloads", NEW)
    live = manager.save_dir / "RandomizerLevelUp.sav"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"first run")

    manager.activate(new.run_id, active_save_seed_id=OLD)
    assert not live.exists()
    assert (old.save_snapshot_dir / live.name).read_bytes() == b"first run"
    manager.activate(old.run_id, active_save_seed_id=NEW, active_save_run_id=new.run_id)
    assert live.read_bytes() == b"first run"


def test_seed_only_owner_with_multiple_players_is_not_assigned_to_selected_run(manager, tmp_path):
    target = import_run(manager, tmp_path / "downloads", OLD)
    other = manager.import_pak(fixtures._write_seed_pak(tmp_path / "downloads", seed=OLD, player=2, slot="Other"))
    live = manager.save_dir / "RandomizerLevelUp.sav"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"unknown player")

    with pytest.raises(KH3RunManagerError, match="2 matching managed runs"):
        manager.activate(target.run_id, active_save_seed_id=OLD)
    assert live.read_bytes() == b"unknown player"
    assert all(not (run.save_snapshot_dir / live.name).exists() for run in (target, other))


@pytest.mark.parametrize("interrupt", ["none", "running", "new_save", "owner_write"])
def test_restore_installed_backup_on_empty_folder_and_retry(manager, interrupt):
    run = import_run(manager, manager.mods_dir, OLD)
    backup = run.save_snapshot_dir / "RandomizerLevelUp.sav"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"original")
    manager.save_dir.mkdir(parents=True)
    live = manager.save_dir / backup.name
    settings = manager.save_dir / "settings.ini"
    settings.write_bytes(b"keep")
    owner = Mock()
    copy_files = manager._copy_gameplay_save_files

    def stage(source, target):
        count = copy_files(source, target)
        if interrupt == "new_save":
            live.write_bytes(b"new progress")
        return count

    if interrupt == "owner_write":
        owner.side_effect = OSError("owner write failed")
    if interrupt != "none":
        with patch.object(manager, "_copy_gameplay_save_files", side_effect=stage), \
                patch.object(manager, "process_running", side_effect=[False, interrupt == "running"]), \
                pytest.raises((KH3RunManagerError, OSError)):
            manager.restore_installed_backup_if_empty(owner)
        if interrupt == "new_save":
            owner.assert_not_called()
            assert manager.restore_installed_backup_if_empty(owner) == 0
            assert live.read_bytes() == b"new progress"
            live.unlink()
        else:
            assert not live.exists()
        owner.reset_mock(side_effect=True)
    assert manager.restore_installed_backup_if_empty(owner) == 1
    restored_run, directory = owner.call_args.args
    assert restored_run.run_id == run.run_id and directory == manager.save_dir
    assert manager.restore_installed_backup_if_empty(owner) == 0
    assert owner.call_count == 1
    assert live.read_bytes() == backup.read_bytes() == b"original"
    assert settings.read_bytes() == b"keep"


@pytest.mark.parametrize("automatic", [True, False])
def test_cleanup_skips_save_owner_and_deletes_unowned_runs(manager, tmp_path, automatic):
    target = import_run(manager, manager.mods_dir, NEW)
    old = import_run(manager, tmp_path / "downloads", OLD)
    extra = import_run(manager, tmp_path / "downloads", "33333333333333333333")
    guard = SeedGuardService(SimpleNamespace(kh3_active_connection_profile_run_id=None))
    scope = tmp_path / "original" if automatic else manager.save_dir
    guard.store_save_seed_associations_for_targets([scope], OLD, old.run_id)
    if automatic:
        guard.store_save_seed_associations_for_targets([manager.save_dir], NEW, target.run_id)
    pruning = RunPruningService(lambda: manager, RunPruneCallbacks(
        selection=lambda: RunSelection(None, target.run_id), auto_enabled=lambda: True, set_status=lambda _: None,
    ))
    with patch.object(manager, "_iter_gameplay_save_files", side_effect=AssertionError("live save scan")):
        asyncio.run(pruning.automatic(target.run_id) if automatic else
                    pruning.selected((old.run_id, extra.run_id), target.run_id))
        with pytest.raises(RunOwnsSaveDirectory):
            manager.delete_run(old.run_id)
    assert old.run_dir.is_dir() and target.installed_pak_path.is_file()
    assert not extra.run_dir.exists()


@pytest.mark.parametrize("entry", ["activate", "seed_only", "connect", "suspend_then_install", "prepare_pak"])
def test_missing_owner_gets_separate_archive_on_activation(manager, entry):
    target = import_run(manager, manager.mods_dir, NEW)
    missing = manager.run_id_for_identity(OLD, 1, "Violin")
    options = dict(active_save_seed_id=OLD, active_save_run_id=missing)
    live = manager.save_dir / "RandomizerLevelUp.sav"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"old")
    kept = manager.save_dir / "settings.ini"
    kept.write_bytes(b"settings")
    target.save_snapshot_dir.mkdir(parents=True)
    (target.save_snapshot_dir / live.name).write_bytes(b"target")
    if entry in {"activate", "seed_only"}:
        if entry == "seed_only":
            options.pop("active_save_run_id")
        result = manager.activate(target.run_id, **options)
    elif entry in {"connect", "prepare_pak"}:
        service = KH3SeedImportService(
            run_manager=manager, mods_dir=manager.mods_dir, generated_root=manager.run_root / "generated",
            process_running=lambda: False,
        )
        if entry == "connect":
            result = service.activate_installed_run_for_connection(
                target.installed_pak_path, expected_seed_id=NEW, expected_slot=1, server_address="localhost:38281",
                connection_slot_name="Violin", room_url=None, save_dir_override=manager.save_dir, **options,
            )
        else:
            target.installed_pak_path.unlink()
            guard = SeedGuardService(SimpleNamespace(kh3_active_connection_profile_run_id=None))
            guard.store_save_seed_associations_for_targets([manager.save_dir], OLD, missing)
            result = service.prepare_player_artifact(target.managed_pak_path, expected_slot=1,
                                                     expected_slot_name="Violin", expected_seed_name=NEW)
    else:
        suspended = manager.suspend_installed_run(**options)
        assert suspended.save_run is None and live.read_bytes() == b"old"
        assert manager._read_active_run_id() == missing
        result = manager.activate(target.run_id, **options)
    [archive] = manager.save_dir.parent.glob(f"SaveGames-Recovered-{OLD}-*")
    assert (archive / live.name).read_bytes() == b"old"
    assert any(str(archive) in warning for warning in result.warnings)
    assert live.read_bytes() == b"target" and kept.read_bytes() == b"settings"
    assert manager._read_active_run_id() == target.run_id


def test_failed_recovery_restores_the_live_save_before_retry(manager):
    target = import_run(manager, manager.mods_dir, NEW)
    live = manager.save_dir / "RandomizerLevelUp.sav"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"old")
    options = dict(active_save_seed_id=OLD, active_save_run_id=manager.run_id_for_identity(OLD, 1, "Violin"))
    with patch.object(manager, "_write_active_run_id", side_effect=OSError("write failed")):
        with pytest.raises(KH3RunManagerError, match="rolled back"):
            manager.activate(target.run_id, **options)
    assert live.read_bytes() == b"old"
    assert not list(manager.save_dir.parent.glob("SaveGames-Recovered-*"))
    manager.activate(target.run_id, **options)
    [archive] = manager.save_dir.parent.glob("SaveGames-Recovered-*")
    assert (archive / live.name).read_bytes() == b"old" and not live.exists()


def test_delete_copied_pak_leaves_first_runs_saves(manager):
    target = import_run(manager, manager.mods_dir, NEW)
    live = manager.save_dir / "RandomizerLevelUp.sav"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"old")
    result = manager.delete_run(target.run_id, allow_active=True, active_save_seed_id=OLD,
                                active_save_run_id=manager.run_id_for_identity(OLD, 1, "Violin"))
    assert result.removed_installed_pak and result.removed_active_save_files == 0
    assert manager.list_runs(force_refresh=True) == [] and live.read_bytes() == b"old"


def test_profile_edit_updates_ownership_across_save_folders(manager, tmp_path):
    run = import_run(manager, manager.mods_dir, OLD)
    scopes = [manager.save_dir, tmp_path / "other"]
    guard = SeedGuardService(SimpleNamespace(kh3_active_connection_profile_run_id=None))
    guard.store_save_seed_associations_for_targets(scopes, OLD, run.run_id)
    for host in ("first.example", "second.example"):
        updated = manager.update_connection_profile(run.run_id, slot_name="Violin", server=host, port="1234", room_url="")
    assert run.run_id != updated.run_id
    # Startup can validate the seed before it loads the active connection profile.
    guard.store_save_seed_associations_for_targets(scopes, OLD)
    assert all(guard.save_run_association_for_dir(scope) == updated.run_id for scope in scopes)


def test_failed_profile_edit_rolls_back_owner_and_profile(manager):
    run = import_run(manager, manager.mods_dir, OLD)
    manager.activate(run.run_id)
    manager.update_connection_profile(run.run_id, slot_name="Violin", server="first.example", port="1234", room_url="")
    guard = SeedGuardService(SimpleNamespace(kh3_active_connection_profile_run_id=None))
    guard.store_save_seed_associations_for_targets([manager.save_dir], OLD, run.run_id)
    store = Utils.persistent_store

    def fail_after_owner_changes(category, key, value):
        store(category, key, value)
        if guard.save_run_association_for_dir(manager.save_dir) != run.run_id:
            raise OSError("profile write failed")

    with patch.object(Utils, "persistent_store", side_effect=fail_after_owner_changes):
        with pytest.raises(KH3RunManagerError, match="profile write failed"):
            manager.update_connection_profile(run.run_id, slot_name="Violin", server="second.example", port="1234", room_url="")
    [restored] = manager.list_runs(force_refresh=True)
    assert restored.run_id == run.run_id and restored.connection_host == "first.example"
    assert manager._read_active_run_id() == guard.save_run_association_for_dir(manager.save_dir) == run.run_id
