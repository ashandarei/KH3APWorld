from dataclasses import replace
from pathlib import Path

import pytest

from ..client_support.compatible_mods import COMPATIBLE_MODS, CompatibleModsError, CompatibleModsService

# Preserve single-mod filename/companion regression coverage separately from groups.
MOD = replace(COMPATIBLE_MODS[0], dependencies=())


def test_player_restores_removes_and_restores_mod(tmp_path):
    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    service = CompatibleModsService(mods, game_running=lambda: False)
    storage = service.storage_dir(MOD)
    storage.mkdir(parents=True)
    original = b"simulated downloaded mod bytes"
    (storage / MOD.aliases[0]).write_bytes(original)
    unrelated = mods / "GoA.pak"
    unrelated.write_bytes(b"untouched")
    assert service.state(MOD) == "Inactive"
    assert service.toggle(MOD) == "Active"
    assert (mods / MOD.pak_name).read_bytes() == original
    assert not list(storage.iterdir())
    companion = mods / MOD.companion_name
    companion.write_bytes(b"reward companion")
    assert service.toggle(MOD) == "Inactive"
    assert not (mods / MOD.pak_name).exists()
    assert not companion.exists()
    assert (storage / MOD.pak_name).read_bytes() == original
    # Restarting the client discovers state entirely from the two folders.
    service = CompatibleModsService(mods, game_running=lambda: False)
    assert service.toggle(MOD) == "Active"
    assert companion.read_bytes() == b"reward companion"
    assert (mods / MOD.pak_name).read_bytes() == original
    assert unrelated.read_bytes() == b"untouched"
    assert not storage.is_relative_to(mods.parent)


def test_running_game_blocks_changes(tmp_path):
    service = CompatibleModsService(tmp_path, game_running=lambda: True)
    (tmp_path / MOD.pak_name).write_bytes(b"active")
    with pytest.raises(CompatibleModsError, match="Close KH3"):
        service.toggle(MOD)
    assert (tmp_path / MOD.pak_name).read_bytes() == b"active"


def test_conflicts_do_not_overwrite_either_copy(tmp_path):
    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    service = CompatibleModsService(mods, game_running=lambda: False)
    storage = service.storage_dir(MOD)
    storage.mkdir(parents=True)
    (mods / MOD.pak_name).write_bytes(b"first")
    (storage / MOD.pak_name).write_bytes(b"second")
    with pytest.raises(CompatibleModsError, match="Conflict"):
        service.toggle(MOD)
    assert (mods / MOD.pak_name).read_bytes() == b"first"
    assert (storage / MOD.pak_name).read_bytes() == b"second"


def test_failed_second_move_rolls_back(tmp_path, monkeypatch):
    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    service = CompatibleModsService(mods, game_running=lambda: False)
    (mods / MOD.pak_name).write_bytes(b"mod")
    (mods / MOD.companion_name).write_bytes(b"companion")
    rename = Path.rename
    count = 0

    def fail_second(path, target):
        nonlocal count
        count += 1
        if count == 2:
            raise PermissionError("locked")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(CompatibleModsError, match="rolled back"):
        service.toggle(MOD)
    assert (mods / MOD.pak_name).read_bytes() == b"mod"
    assert (mods / MOD.companion_name).read_bytes() == b"companion"
    assert service.state(MOD) == "Active"


@pytest.mark.parametrize("disabled", ["", ".disabled-by-kh3ap", ".disabled-by-kh3ap.2"])
def test_restore_protects_priority_and_disabled_state(tmp_path, disabled):
    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    service = CompatibleModsService(mods, game_running=lambda: False)
    storage = service.storage_dir(MOD)
    storage.mkdir(parents=True)
    (storage / "zzzy_ColiseumMod_999_P.pak").write_bytes(b"full mod")
    (storage / ("zzzz_APOlympusColiseumCompat_99_P.pak" + disabled)).write_bytes(b"companion")
    assert service.toggle(MOD) == "Active"
    assert (mods / "zzzy_ColiseumMod_998_P.pak").read_bytes() == b"full mod"
    assert (mods / ("zzzz_APOlympusColiseumCompat_999_P.pak" + disabled)).read_bytes() == b"companion"
    assert service.toggle(MOD) == "Inactive"
    assert service.toggle(MOD) == "Active"
    assert (mods / (MOD.companion_name + disabled)).read_bytes() == b"companion"


def test_repair_blocks_duplicate_companions_before_moving(tmp_path):
    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    service = CompatibleModsService(mods, game_running=lambda: False)
    (mods / MOD.pak_name).write_bytes(b"mod")
    (mods / MOD.companion_name).write_bytes(b"correct")
    wrong = mods / "zzzz_APOlympusColiseumCompat.pak"
    wrong.write_bytes(b"other")
    with pytest.raises(CompatibleModsError, match="Conflict"):
        service.toggle(MOD)
    assert wrong.read_bytes() == b"other"
    assert (mods / MOD.companion_name).read_bytes() == b"correct"


def test_installed_reference_names_need_no_repair(tmp_path):
    service = CompatibleModsService(tmp_path, game_running=lambda: False)
    (tmp_path / MOD.pak_name).write_bytes(b"full mod")
    (tmp_path / (MOD.companion_name + ".disabled-by-kh3ap")).write_bytes(b"companion")
    assert service.state(MOD) == "Active"
    assert service.protected_name("OtherColiseumMod_999_P.pak", MOD) is None
    assert service.protected_name(MOD.companion_name + ".bak", MOD) is None


def test_automatic_rename_defers_until_game_closes(tmp_path):
    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    running = True
    service = CompatibleModsService(mods, game_running=lambda: running)
    source = mods / MOD.aliases[0]
    source.write_bytes(b"original")
    assert not service.normalize_installed(MOD)
    assert source.exists()
    running = False
    assert service.normalize_installed(MOD)
    assert not source.exists()
    assert (mods / MOD.pak_name).read_bytes() == b"original"
    assert not service.normalize_installed(MOD)


def test_headless_companion_preparation_normalizes_base_first(tmp_path, monkeypatch):
    from ..client_support import olympus_coliseum_compat as compat

    mods = tmp_path / "Content" / "Paks" / "~mods"
    mods.mkdir(parents=True)
    source = mods / MOD.aliases[0]
    source.write_bytes(b"source")
    monkeypatch.setattr(compat, "_file_sha256", lambda path: compat.OLYMPUS_COLISEUM_SOURCE_PAK_SHA256)

    def build(path, output, **reward_modes):
        assert path == mods / MOD.pak_name
        assert path.read_bytes() == b"source"
        assert not source.exists()
        output.write_bytes(b"x" * 6000)
        return output

    result = compat.ensure_olympus_coliseum_compat(
        mods, {"olympus_coliseum_pool": "randomize"}, game_running=False, build_companion=build,
    )
    assert result.ready, result.message
    assert (mods / MOD.companion_name).exists()


def test_automatic_rename_refuses_duplicate_base_files(tmp_path):
    service = CompatibleModsService(tmp_path, game_running=lambda: False)
    (tmp_path / MOD.aliases[0]).write_bytes(b"download")
    (tmp_path / MOD.pak_name).write_bytes(b"installed")
    with pytest.raises(CompatibleModsError, match="Conflict"):
        service.normalize_installed(MOD)
    assert (tmp_path / MOD.aliases[0]).read_bytes() == b"download"
    assert (tmp_path / MOD.pak_name).read_bytes() == b"installed"


def stage_group(tmp_path):
    service = CompatibleModsService(tmp_path / "Content" / "Paks" / "~mods", game_running=lambda: False)
    service.mods_dir.mkdir(parents=True)
    group = COMPATIBLE_MODS[0]
    for part in (group, *group.dependencies):
        folder = service.storage_dir(part)
        folder.mkdir(parents=True)
        (folder / part.pak_name).write_bytes(part.id.encode())
    return service, group


def test_group_restores_and_removes_required_bosses(tmp_path):
    service, group = stage_group(tmp_path)
    for active in (True, False, True):
        service.set_group_active(group, active=active)
        for part in (group, *group.dependencies):
            folder = service.mods_dir if active else service.storage_dir(part)
            assert (folder / part.pak_name).read_bytes() == part.id.encode()


def test_group_repairs_existing_coliseum_and_can_remove_incomplete_install(tmp_path):
    service, group = stage_group(tmp_path)
    (service.storage_dir(group) / group.pak_name).rename(service.mods_dir / group.pak_name)
    service.set_group_active(group, active=True)
    assert all(service.state(d) == "Active" for d in group.dependencies)
    # One required mod is no longer installed: removal must still work.
    (service.mods_dir / group.dependencies[0].pak_name).unlink()
    service.set_group_active(group, active=False)
    assert service.state(group) == "Inactive"
    assert service.state(group.dependencies[1]) == "Inactive"


@pytest.mark.parametrize("problem", ["missing", "duplicate", "wrong-variant"])
def test_group_preflight_leaves_all_files_untouched(tmp_path, problem):
    service, group = stage_group(tmp_path)
    dependency = group.dependencies[-1]
    if problem == "missing":
        (service.storage_dir(dependency) / dependency.pak_name).unlink()
    elif problem == "duplicate":
        (service.mods_dir / dependency.pak_name).write_bytes(b"existing")
    else:
        (service.mods_dir / "RoxasBoss_GoA_Version.pak").write_bytes(b"wrong version")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.pak")}
    with pytest.raises(CompatibleModsError):
        service.set_group_active(group, active=True)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.pak")} == before


def test_group_failure_rolls_back_across_dependency_folders(tmp_path, monkeypatch):
    service, group = stage_group(tmp_path)
    rename = Path.rename

    def fail_last(path, target):
        if path.name == group.dependencies[-1].pak_name:
            raise PermissionError("locked dependency")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_last)
    with pytest.raises(CompatibleModsError, match="rolled back"):
        service.set_group_active(group, active=True)
    assert all(service.state(p) == "Inactive" for p in (group, *group.dependencies))


def test_running_game_blocks_group_restore_and_remove(tmp_path):
    service, group = stage_group(tmp_path)
    service.game_running = lambda: True
    for active in (True, False):
        with pytest.raises(CompatibleModsError, match="Close KH3"):
            service.set_group_active(group, active=active)
    assert service.state(group) == "Inactive"


def test_companion_alone_does_not_replace_missing_base_pak(tmp_path):
    service, group = stage_group(tmp_path)
    (service.storage_dir(group) / group.pak_name).unlink()
    (service.storage_dir(group) / group.companion_name).write_bytes(b"companion only")
    assert service.state(group) == "Not installed"
    problem = service.group_problem(group)
    assert "missing zzzColiseumMod.pak" in problem
    assert str(service.storage_dir(group)) in problem
    with pytest.raises(CompatibleModsError, match="missing zzzColiseumMod.pak"):
        service.set_group_active(group, active=True)


def test_custom_catalog_persists_and_is_install_scoped(tmp_path):
    mods = tmp_path / 'game' / 'Content' / 'Paks' / '~mods'
    service = CompatibleModsService(mods, game_running=lambda: False)
    assert service.catalog() == COMPATIBLE_MODS
    custom = service.add_mod('Appearance', 'Appearance.pak', 'https://example.com/mod')
    reloaded = CompatibleModsService(mods, game_running=lambda: False)
    assert reloaded.catalog() == (*COMPATIBLE_MODS, custom)
    other = CompatibleModsService(tmp_path / 'other' / 'Content' / 'Paks' / '~mods', game_running=lambda: False)
    assert other.catalog() == COMPATIBLE_MODS


@pytest.mark.parametrize('filename', ['../bad.pak', 'C:\\bad.pak', 'bad.txt', 'bad:stream.pak', 'zzzColiseumMod.pak', 'PhantomAqua_BossBattle.pak'])
def test_custom_catalog_rejects_invalid_or_builtin_filenames(tmp_path, filename):
    service = CompatibleModsService(tmp_path / 'Content' / 'Paks' / '~mods', game_running=lambda: False)
    with pytest.raises(CompatibleModsError):
        service.add_mod('Custom', filename)
    assert not service.catalog_path.exists()


def test_custom_catalog_rejects_duplicate_and_preserves_bad_catalog(tmp_path):
    service = CompatibleModsService(tmp_path / 'Content' / 'Paks' / '~mods', game_running=lambda: False)
    service.add_mod('Custom', 'Custom.pak')
    with pytest.raises(CompatibleModsError, match='already belongs'):
        service.add_mod('Another', 'CUSTOM.PAK')
    with pytest.raises(CompatibleModsError, match='http'):
        service.add_mod('Another', 'Other.pak', 'file:///local')
    service.catalog_path.write_text('{bad json', encoding='utf-8')
    with pytest.raises(CompatibleModsError, match='Could not read'):
        service.add_mod('Another', 'Other.pak')
    assert service.catalog_path.read_text(encoding='utf-8') == '{bad json'
