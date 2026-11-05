"""Launch paths retain explicit overrides and detected-root defaults."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock

import pytest

from ..client_support import client_paths as paths


@pytest.fixture
def discovery(monkeypatch, tmp_path):
    callbacks = {}
    for name, value in (
        ("normalize_kh3_root_dir", tmp_path / "normalized"),
        ("resolve_default_kh3_root_dir", tmp_path / "egs"),
        ("resolve_default_kh3_save_dir", tmp_path / "detected-saves"),
        ("resolve_kh3_save_dir_input", tmp_path / "selected-saves"),
        ("kh3_ue4ss_log_from_root", tmp_path / "UE4SS.log"),
        ("kh3_probe_config_from_root", tmp_path / "config.lua"),
        ("kh3_mods_dir_from_root", tmp_path / "mods"),
        ("is_kh3_auto_save_dir", False),
        ("is_kh3_save_dir_dynamic_root", False),
    ):
        callbacks[name] = Mock(return_value=value)
        monkeypatch.setattr(paths, name, callbacks[name])
    return callbacks


def resolve(**changes):
    defaults = dict(
        kh3_dir=None, save_dir=None, ue4ss_log=None, probe_config=None, mods_dir=None, persisted_save_dir=None
    )
    return paths.resolve_client_paths(**(defaults | changes))


def test_default_paths_follow_the_detected_egs_root(discovery):
    result = resolve()
    assert result.kh3_dir == discovery["resolve_default_kh3_root_dir"].return_value
    discovery["normalize_kh3_root_dir"].assert_not_called()
    discovery["resolve_default_kh3_save_dir"].assert_called_once_with(result.kh3_dir)
    assert result.ue4ss_log == discovery["kh3_ue4ss_log_from_root"].return_value
    assert result.probe_config == discovery["kh3_probe_config_from_root"].return_value
    assert result.mods_dir == discovery["kh3_mods_dir_from_root"].return_value.resolve()
    assert not any(
        (
            result.save_dir_override,
            result.dynamic_save_root_override,
            result.ue4ss_log_override,
            result.probe_config_override,
            result.mods_dir_override,
        )
    )
    with pytest.raises(FrozenInstanceError):
        result.kh3_dir = Path("changed")


def test_explicit_paths_override_discovery_without_resolving_default_saves(discovery, tmp_path):
    root, saves, log, probe, mods = (str(tmp_path / name) for name in ("game", "saves", "log", "probe", "mods"))
    result = resolve(kh3_dir=root, save_dir=saves, ue4ss_log=log, probe_config=probe, mods_dir=mods)
    discovery["normalize_kh3_root_dir"].assert_called_once_with(Path(root))
    discovery["resolve_kh3_save_dir_input"].assert_called_once_with(saves)
    discovery["resolve_default_kh3_save_dir"].assert_not_called()
    discovery["kh3_ue4ss_log_from_root"].assert_not_called()
    discovery["kh3_mods_dir_from_root"].assert_not_called()
    assert (result.ue4ss_log, result.probe_config, result.mods_dir) == tuple(
        Path(p).resolve() for p in (log, probe, mods)
    )
    assert all(
        (result.save_dir_override, result.ue4ss_log_override, result.probe_config_override, result.mods_dir_override)
    )


@pytest.mark.parametrize("automatic", [True, False])
@pytest.mark.parametrize("dynamic", [True, False])
def test_persisted_save_selection_retains_its_override_classification(discovery, automatic, dynamic):
    discovery["is_kh3_auto_save_dir"].return_value = automatic
    discovery["is_kh3_save_dir_dynamic_root"].return_value = dynamic
    result = resolve(persisted_save_dir="persisted")
    assert result.save_dir_override is (not automatic)
    assert result.dynamic_save_root_override is dynamic
    discovery["is_kh3_save_dir_dynamic_root"].assert_called_once_with("persisted")
    discovery["resolve_kh3_save_dir_input"].assert_not_called()


def test_explicit_save_selection_wins_over_a_persisted_dynamic_root(discovery):
    discovery["is_kh3_save_dir_dynamic_root"].side_effect = lambda path: path == "persisted"
    result = resolve(save_dir="manual", persisted_save_dir="persisted")
    assert result.save_dir_override
    assert not result.dynamic_save_root_override
    discovery["is_kh3_save_dir_dynamic_root"].assert_called_once_with("manual")


def test_empty_explicit_overrides_keep_the_existing_flag_semantics(discovery):
    result = resolve(save_dir="", ue4ss_log="", probe_config="", mods_dir="", persisted_save_dir="persisted")
    assert all(
        (result.save_dir_override, result.ue4ss_log_override, result.probe_config_override, result.mods_dir_override)
    )
    assert not result.dynamic_save_root_override
    assert result.probe_config == discovery["kh3_probe_config_from_root"].return_value
    discovery["resolve_default_kh3_save_dir"].assert_called_once()


@pytest.mark.parametrize("log_override", [False, True])
@pytest.mark.parametrize("probe_override", [False, True])
@pytest.mark.parametrize("mods_override", [False, True])
def test_game_root_changes_refresh_only_discovered_install_paths(
    discovery, log_override, probe_override, mods_override
):
    current = (Path("previous.log"), Path("previous.lua"), Path("previous-mods"))
    result = paths.resolve_game_root_paths(
        "selected-root",
        ue4ss_log=current[0],
        probe_config=current[1],
        mods_dir=current[2],
        ue4ss_log_override=log_override,
        probe_config_override=probe_override,
        mods_dir_override=mods_override,
    )
    assert result.root == discovery["normalize_kh3_root_dir"].return_value
    discovery["normalize_kh3_root_dir"].assert_called_once_with(Path("selected-root"))
    for override, value, actual, discover in (
        (log_override, current[0], result.ue4ss_log, "kh3_ue4ss_log_from_root"),
        (probe_override, current[1], result.probe_config, "kh3_probe_config_from_root"),
        (mods_override, current[2], result.mods_dir, "kh3_mods_dir_from_root"),
    ):
        if override:
            assert actual == value
            discovery[discover].assert_not_called()
        else:
            discovery[discover].assert_called_once_with(result.root)
            expected = discovery[discover].return_value
            assert actual == (expected.resolve() if discover == "kh3_mods_dir_from_root" else expected)
    discovery["resolve_default_kh3_save_dir"].assert_not_called()


def test_game_root_change_preserves_explicit_unavailable_optional_paths(discovery):
    result = paths.resolve_game_root_paths(
        "selected-root",
        ue4ss_log=None,
        probe_config=None,
        mods_dir=Path("mods"),
        ue4ss_log_override=True,
        probe_config_override=True,
        mods_dir_override=True,
    )
    assert result.ue4ss_log is None
    assert result.probe_config is None
    discovery["kh3_ue4ss_log_from_root"].assert_not_called()
    discovery["kh3_probe_config_from_root"].assert_not_called()
