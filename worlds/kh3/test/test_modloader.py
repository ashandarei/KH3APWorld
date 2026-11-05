from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from .. import Client as client
from ..client_support import startup_video, static_chest_tables
from ..platform_support.modloader import MODLOADER_PAK_NAME, fix_modloader_pak_priority


@pytest.mark.parametrize("name", ["Modloader.pak", "ZzZ-Modloader.pak", "MODLOADER.PAK"])
def test_rename_preserves_bytes_and_is_idempotent(tmp_path, name):
    source = tmp_path / name
    source.write_bytes(b"title header and exports")
    assert fix_modloader_pak_priority(tmp_path)
    assert not source.exists()
    assert (tmp_path / MODLOADER_PAK_NAME).read_bytes() == b"title header and exports"
    assert fix_modloader_pak_priority(tmp_path) == ()


def test_optional_loader_and_compatibility_paks_are_left_alone(tmp_path):
    assert fix_modloader_pak_priority(tmp_path / "missing") == ()
    for name in ["C-Mod_ModLoader.pak", "seed_99_P.pak", "OtherModloader.pak", "Modloader.pak.bak"]:
        (tmp_path / name).write_bytes(b"unrelated")
    before = sorted(path.name for path in tmp_path.iterdir())
    assert fix_modloader_pak_priority(tmp_path) == ()
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_nested_loader_is_fixed_in_place(tmp_path):
    nested = tmp_path / "optional mods"
    nested.mkdir()
    (nested / "ZzZ-Modloader.pak").write_bytes(b"pak")
    fix_modloader_pak_priority(tmp_path)
    assert (nested / MODLOADER_PAK_NAME).read_bytes() == b"pak"


def test_duplicates_are_preserved_outside_the_pak_extension(tmp_path):
    for name in ["Modloader.pak", "ZzZ-Modloader.pak", MODLOADER_PAK_NAME]:
        (tmp_path / name).write_bytes(b"same pak")
    fix_modloader_pak_priority(tmp_path)
    assert [p.name for p in tmp_path.glob("*.pak")] == [MODLOADER_PAK_NAME]
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 2
    assert all(p.read_bytes() == b"same pak" for p in backups)
    assert fix_modloader_pak_priority(tmp_path) == ()


def test_conflicting_versions_leave_all_files_unchanged(tmp_path):
    (tmp_path / "Modloader.pak").write_bytes(b"old")
    (tmp_path / MODLOADER_PAK_NAME).write_bytes(b"different")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(RuntimeError, match="Different Modloader"):
        fix_modloader_pak_priority(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_running_game_prevents_rename_but_allows_already_fixed(tmp_path):
    source = tmp_path / "Modloader.pak"
    source.write_bytes(b"pak")
    with pytest.raises(RuntimeError, match="Close KH3"):
        fix_modloader_pak_priority(tmp_path, game_running=True)
    assert source.read_bytes() == b"pak"
    source.rename(tmp_path / MODLOADER_PAK_NAME)
    assert fix_modloader_pak_priority(tmp_path, game_running=True) == ()


@pytest.mark.parametrize("entrypoint", ["_launch_kh3_game_now", "_install_kh3_runtime_files"])
@pytest.mark.parametrize("failure", [None, "conflict", "locked", "running"])
def test_patch_and_launch_check_loader_before_proceeding(tmp_path, monkeypatch, entrypoint, failure):
    source = tmp_path / "Modloader.pak"
    source.write_bytes(b"pak")
    if failure == "conflict":
        (tmp_path / MODLOADER_PAK_NAME).write_bytes(b"different")
    if failure == "locked":
        monkeypatch.setattr(Path, "rename", Mock(side_effect=PermissionError("file is locked")))
    monkeypatch.setattr(client, "is_process_running", lambda _: failure == "running")
    proceed = Mock()

    def check_ready(*args, **kwargs):
        assert (tmp_path / MODLOADER_PAK_NAME).read_bytes() == b"pak"
        assert not source.exists()
        proceed()

    monkeypatch.setattr(startup_video, "install_short_copyright_video", Mock(side_effect=check_ready))
    monkeypatch.setattr(static_chest_tables, "validate_static_chest_tables", Mock())
    ctx = SimpleNamespace(
        kh3_dir=tmp_path,
        kh3_mods_dir=tmp_path,
        disable_steam_cloud_during_patch_game=Mock(side_effect=check_ready),
        _reconcile_olympus_coliseum_reward_companion=Mock(),
        _get_client_command_service=Mock(),
        _apply_client_facade_plan=Mock(),
        _get_runtime_artifact_service=Mock(),
        _apply_runtime_artifact_plan=Mock(),
    )
    ctx._ensure_modloader_pak_priority = lambda: client.KH3Context._ensure_modloader_pak_priority(ctx)
    getattr(client.KH3Context, entrypoint)(ctx)
    if failure:
        proceed.assert_not_called()
        ctx._apply_client_facade_plan.assert_not_called()
        ctx._apply_runtime_artifact_plan.assert_not_called()
    else:
        proceed.assert_called_once()
        if entrypoint == "_launch_kh3_game_now":
            ctx._apply_client_facade_plan.assert_called_once()
        else:
            ctx._apply_runtime_artifact_plan.assert_called_once()
