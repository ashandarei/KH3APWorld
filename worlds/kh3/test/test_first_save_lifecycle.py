"""First AP saves must survive restart and repeated runtime installation."""

from types import SimpleNamespace

import pytest

from ..client_support.run_manager import KH3RunManagerService
from ..client_support.save_management import vanilla_save_backup_dir
from ..client_support.save_service import SaveManagementService
from ..client_support.seed_guard import KH3_SAVE_SEED_GUARD_VERSION, SeedGuardService
from ..client_support.seed_policy_service import KH3_MINIMUM_SEED_PAK_SIZE_BYTES


SEED = "22949505740782083774"


@pytest.mark.parametrize("existing_backup", [False, True])
def test_patch_game_keeps_first_associated_ap_save(tmp_path, monkeypatch, existing_backup):
    save_dir = tmp_path / "SaveGames"
    save_dir.mkdir()
    first_save = save_dir / "KHIII_slot0.bin"
    first_save.write_bytes(b"first AP progress")
    backup_dir = vanilla_save_backup_dir(save_dir)
    if existing_backup:
        backup_dir.mkdir()
        (backup_dir / "KHIII_slot0.bin").write_bytes(b"vanilla progress")
    persistent = {"save_seed_associations": {
        SeedGuardService.save_seed_scope_key(save_dir): {
            "seed_id": SEED, "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
        },
    }}
    monkeypatch.setattr("Utils.persistent_load", lambda: {"kh3_client": persistent})
    service = SaveManagementService(SimpleNamespace(bridge_save_dir=save_dir), process_running=lambda: False)

    # Patch Game is used again after closing the first gameplay session.
    service.backup_vanilla_saves_before_ap_install()

    assert first_save.read_bytes() == b"first AP progress"
    if existing_backup:
        assert (backup_dir / "KHIII_slot0.bin").read_bytes() == b"vanilla progress"
    else:
        assert not backup_dir.exists()


@pytest.mark.parametrize("other_scope", [False, True])
def test_patch_game_still_preserves_unassociated_vanilla_saves(tmp_path, monkeypatch, other_scope):
    save_dir = tmp_path / "SaveGames"
    save_dir.mkdir()
    (save_dir / "KHIII_slot0.bin").write_bytes(b"vanilla progress")
    persistent = {"last_save_seed_id": SEED}
    if other_scope:
        persistent["save_seed_associations"] = {
            SeedGuardService.save_seed_scope_key(tmp_path / "OtherAccount" / "SaveGames"): {
                "seed_id": SEED, "guard_version": KH3_SAVE_SEED_GUARD_VERSION,
            },
        }
    monkeypatch.setattr("Utils.persistent_load", lambda: {"kh3_client": persistent})
    service = SaveManagementService(SimpleNamespace(bridge_save_dir=save_dir), process_running=lambda: False)

    service.backup_vanilla_saves_before_ap_install()

    assert (vanilla_save_backup_dir(save_dir) / "KHIII_slot0.bin").read_bytes() == b"vanilla progress"
    assert not (save_dir / "KHIII_slot0.bin").exists()


@pytest.mark.parametrize("server_address", ["localhost:38281", "localhost:38282"])
def test_first_save_without_snapshot_survives_manager_restart(tmp_path, server_address):
    options = dict(run_root=tmp_path / "runs", bridge_root=tmp_path / "bridge",
                   mods_dir=tmp_path / "mods", save_dir=tmp_path / "SaveGames",
                   process_running=lambda: False, platform="egs")
    manager = KH3RunManagerService(**options)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    pak = downloads / f"pakchunk9-FirstSaveRepro-randomizer-AP_{SEED}-1_99_P.pak"
    pak.write_bytes(b"P" * KH3_MINIMUM_SEED_PAK_SIZE_BYTES)
    run = manager.import_pak(pak, server_address="localhost:38281", connection_slot_name="FirstSaveRepro")
    manager.activate(run.run_id)
    first_save = manager.save_dir / "KHIII_slot0.bin"
    first_save.write_bytes(b"first AP progress")

    restarted = KH3RunManagerService(**options)
    enrolled = restarted.ensure_pak_managed(manager.mods_dir / pak.name,
        expected_seed_id=SEED, server_address=server_address, connection_slot_name="FirstSaveRepro").run
    assert not enrolled.has_save  # No switch has created a stored snapshot yet.
    result = restarted.activate(enrolled.run_id, active_save_seed_id=SEED, active_save_run_id=run.run_id)

    assert not result.switched
    assert first_save.read_bytes() == b"first AP progress"
