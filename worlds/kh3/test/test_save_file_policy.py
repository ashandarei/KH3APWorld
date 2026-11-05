"""Exercise client save actions against synthetic files and real filesystem links."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ..Client import KH3Context
from ..client_support import run_manager, save_management
from ..client_support.save_file_policy import SaveFileError, is_gameplay_save
from ..client_support.save_service import SaveManagementService
from ..gui.config import ConfigView
from . import test_run_manager as run_fixtures


def write(root, relative, data=b"synthetic save"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def directory_link():
    links = []

    def create(link, target):
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
        links.append(link)

    yield create
    for link in reversed(links):
        if os.name == "nt":
            link.rmdir()  # Remove only the test junction, never its target.
        else:
            link.unlink()


@pytest.mark.parametrize("relative", [
    "KHIII_slot0.bin", "KHIII_slot99.sav", "kh3sv2/data/KHIII_slot50.bin",
    "kh3sv2/data/KHIII_system.bin", "ChoicesMade.sav", "RandomizerChests.sav",
    "RandomizerEvents.sav", "RandomizerLevelUp.sav", "RandomizerVBonus.sav",
    "RandomizerRetry.sav", "PlayerStart.sav", "WorldSaveDone.sav", "FlagDataDW.sav",
    "SavePointKGRE.sav", "SavePointMIDiscoveredTankYard.sav", "ColiseumMod_Slot12.sav",
])
def test_recognized_save_names_and_locations(relative):
    assert is_gameplay_save(Path(relative))


@pytest.mark.parametrize("relative", [
    "slot.bin", "other.sav", "unrelated.bin", "ModLoaderSaves.sav", "settings.json",
    "KHIII_slot1.bin.bak", "KHIII_slot.bin", "KHIII_slot-1.bin",
    "backup/KHIII_slot0.bin", "kh3sv2/KHIII_slot0.bin",
    "kh3sv2/data/RandomizerEvents.sav", "nested/PlayerStart.sav",
    "FlagDataUnrelated.sav", "ColiseumMod_Slot.sav", "../KHIII_slot0.bin",
])
def test_other_files_are_not_gameplay_saves(relative):
    assert not is_gameplay_save(Path(relative))


def test_real_client_delete_action_preserves_unknown_files_and_subtrees(tmp_path, directory_link):
    root = tmp_path / "SaveGames"
    saves = [write(root, "kh3sv2/data/KHIII_slot0.bin"), write(root, "RandomizerEvents.sav")]
    kept = [write(root, name) for name in (
        "unrelated.bin", "other.sav", "ModLoaderSaves.sav", "settings.json",
        "backup/KHIII_slot0.bin", "nested/PlayerStart.sav",
    )]
    outside = tmp_path / "outside"
    external = write(outside, "KHIII_slot0.bin", b"outside")
    directory_link(root / "linked-backup", outside)
    view = ConfigView.__new__(ConfigView)
    view.ctx = SimpleNamespace(bridge_save_dir=root, kh3_run_action_running=False)
    view.app = SimpleNamespace(action_running=False, form=AsyncMock(return_value={}))
    with patch("worlds.kh3.platform_support.process_status.is_process_running", return_value=False):
        asyncio.run(view.delete_save_files())
    assert all(not path.exists() for path in saves)
    assert all(path.read_bytes() == b"synthetic save" for path in kept)
    assert external.read_bytes() == b"outside"
    assert view.app.form.await_args_list[0].args[0] == "Delete Save Files"
    assert "Unrecognized files and folders" in view.app.form.await_args_list[0].args[1]
    assert "Deleted 2 save files" in view.app.form.await_args_list[1].args[1]


def test_client_backup_restore_preserves_unknown_files_in_both_roots(tmp_path):
    root = tmp_path / "SaveGames"
    slot = write(root, "kh3sv2/data/KHIII_slot0.bin", b"vanilla")
    kept = write(root, "unrelated.bin", b"live unrelated")
    result = save_management.backup_vanilla_save_files(root)
    assert result["moved_files"] == 1
    backup = Path(result["backup_dir"])
    unknown_backup = write(backup, "other.sav", b"backup unrelated")
    write(root, "kh3sv2/data/KHIII_slot0.bin", b"AP")
    result = save_management.restore_vanilla_save_files(root)
    assert result["status"] == "ok"
    assert result["restored_files"] == result["archived_current_files"] == 1
    assert slot.read_bytes() == b"vanilla"
    assert kept.read_bytes() == b"live unrelated"
    assert unknown_backup.read_bytes() == b"backup unrelated"
    archive = Path(result["archive_dir"])
    assert (archive / "kh3sv2/data/KHIII_slot0.bin").read_bytes() == b"AP"
    result = KH3Context.delete_save_files(root, preserve_root=False, backup_current_save=False)
    assert result["removed_files"] == 1 and root.exists() and kept.exists()


@pytest.mark.parametrize("action", [
    save_management.delete_confirmed_save_files,
    save_management.plan_delete_save_files,
    save_management.plan_backup_vanilla_save_files,
    run_manager.KH3RunManagerService._iter_gameplay_save_files,
])
def test_redirected_native_directory_fails_before_touching_any_save(tmp_path, directory_link, action):
    root = tmp_path / "SaveGames"
    local = write(root, "ChoicesMade.sav")
    outside = tmp_path / "outside"
    external = write(outside, "data/KHIII_slot0.bin", b"outside")
    directory_link(root / "kh3sv2", outside)
    with pytest.raises((SaveFileError, run_manager.KH3RunManagerError), match="linked|redirected"):
        action(root)
    assert local.read_bytes() == b"synthetic save"
    assert external.read_bytes() == b"outside"


@pytest.mark.parametrize("redirect", ["source", "archive"])
def test_cleanup_rechecks_paths_after_planning(tmp_path, directory_link, redirect):
    root = tmp_path / "SaveGames"
    slot = write(root, "kh3sv2/data/KHIII_slot0.bin")
    plan = save_management.plan_delete_save_files(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    if redirect == "source":
        original = root / "original"
        (root / "kh3sv2").rename(original)
        slot = original / "data/KHIII_slot0.bin"
        external = write(outside, "data/KHIII_slot0.bin", b"outside")
        directory_link(root / "kh3sv2", outside)
    else:
        external = write(outside, "untouched.bin", b"outside")
        directory_link(plan.archive_dir, outside)
    result = save_management.execute_save_cleanup_plan(plan)
    assert result["status"] == "error" and result["archived_files"] == 0
    assert slot.read_bytes() == b"synthetic save"
    assert external.read_bytes() == b"outside"


@pytest.mark.parametrize("redirect", ["backup", "destination"])
def test_restore_rechecks_both_roots_before_archiving_current_save(tmp_path, directory_link, redirect):
    root = tmp_path / "SaveGames"
    active = write(root, "kh3sv2/data/KHIII_slot0.bin", b"active")
    backup = save_management.vanilla_save_backup_dir(root)
    original = write(backup, "kh3sv2/data/KHIII_slot0.bin", b"original")
    plan = save_management.plan_restore_vanilla_save_files(root)
    redirected = backup if redirect == "backup" else root
    retained = tmp_path / "retained"
    (redirected / "kh3sv2").rename(retained)
    outside = tmp_path / "outside"
    external = write(outside, "data/KHIII_slot0.bin", b"outside")
    directory_link(redirected / "kh3sv2", outside)
    result = save_management.execute_vanilla_save_restore_plan(plan)
    assert result["status"] == "error"
    assert result["archived_current_files"] == result["restored_files"] == 0
    assert external.read_bytes() == b"outside"
    assert (retained / "data/KHIII_slot0.bin").read_bytes() == (b"original" if redirect == "backup" else b"active")
    assert (active if redirect == "backup" else original).exists()


@pytest.mark.parametrize("action", ["targets", "preserve", "backup", "restore"])
def test_client_service_does_not_resolve_away_redirected_roots(tmp_path, directory_link, action):
    root = tmp_path / "SaveGames"
    external = write(tmp_path / "outside", "KHIII_slot0.bin", b"outside")
    directory_link(root, external.parent)
    service = SaveManagementService(SimpleNamespace(bridge_save_dir=root), process_running=lambda: False)
    operations = {
        "targets": service.save_cleanup_targets,
        "preserve": lambda: service.preserve_unidentified_saves_before_ap_install(root),
        "backup": service.backup_vanilla_save_files,
        "restore": service.restore_vanilla_save_files,
    }
    with pytest.raises(SaveFileError, match="linked|redirected"):
        operations[action]()
    assert external.read_bytes() == b"outside"


def test_run_switch_round_trip_keeps_unknown_live_files(tmp_path):
    service = run_fixtures.KH3RunManagerTest()._service(tmp_path)
    first = service.import_pak(run_fixtures._write_seed_pak(tmp_path / "downloads", seed="11111111111111111111", player=1, slot="Sora"))
    second = service.import_pak(run_fixtures._write_seed_pak(tmp_path / "downloads", seed="22222222222222222222", player=1, slot="Sora"))
    service.activate(first.run_id)
    slot = write(service.save_dir, "kh3sv2/data/KHIII_slot0.bin", b"first run")
    sidecar = write(service.save_dir, "FlagDataHE.sav", b"flags")
    kept = [write(service.save_dir, name, b"keep") for name in (
        "other.sav", "unrelated.bin", "ModLoaderSaves.sav", "backup/KHIII_slot0.bin",
    )]
    switched = service.activate(second.run_id, active_save_seed_id=first.seed_id, active_save_run_id=first.run_id)
    assert switched.saved_current_files == 2
    assert not slot.exists() and not sidecar.exists()
    assert all(path.read_bytes() == b"keep" for path in kept)
    restored = service.activate(first.run_id, active_save_seed_id=second.seed_id, active_save_run_id=second.run_id)
    assert restored.restored_target_files == 2
    assert slot.read_bytes() == b"first run" and sidecar.read_bytes() == b"flags"
    assert all(path.read_bytes() == b"keep" for path in kept)


def test_run_move_rolls_back_if_native_directory_is_redirected_mid_operation(tmp_path, directory_link, monkeypatch):
    root, destination = tmp_path / "SaveGames", tmp_path / "stage"
    first = write(root, "ChoicesMade.sav", b"first")
    write(root, "kh3sv2/data/KHIII_slot0.bin", b"second")
    outside = tmp_path / "outside"
    external = write(outside, "data/KHIII_slot0.bin", b"outside")
    original_transfer = run_manager.transfer_save

    def redirect_after_first_move(source_root, destination_root, source, target, **kwargs):
        original_transfer(source_root, destination_root, source, target, **kwargs)
        if source == first:
            (root / "kh3sv2").rename(root / "retained")
            directory_link(root / "kh3sv2", outside)

    monkeypatch.setattr(run_manager, "transfer_save", redirect_after_first_move)
    with pytest.raises(run_manager.KH3RunManagerError, match="linked|redirected"):
        run_manager.KH3RunManagerService._move_gameplay_save_files(root, destination)
    assert first.read_bytes() == b"first"
    assert (root / "retained/data/KHIII_slot0.bin").read_bytes() == b"second"
    assert external.read_bytes() == b"outside"
    assert not (destination / first.name).exists()
