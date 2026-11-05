from unittest.mock import Mock

import pytest

from ..client_support import custom_music
from ..installer_support import (
    paths as installer_paths,
)


@pytest.fixture
def preferences(monkeypatch):
    values = {"kh3_client": {}}
    monkeypatch.setattr(custom_music.Utils, "persistent_load", lambda: values)
    monkeypatch.setattr(custom_music.Utils, "persistent_store", lambda section, key, value: values[section].update({key: value}))
    return values["kh3_client"]


def game_at(root):
    win64 = root / "Binaries/Win64"
    win64.mkdir(parents=True)
    (win64 / "KINGDOM HEARTS III.exe").write_bytes(b"test fixture")
    return root


@pytest.mark.parametrize("store", ["egs", "steam"])
def test_music_directory_persists_and_publishes_to_runtime_layouts(tmp_path, monkeypatch, preferences, store):
    outer = tmp_path / ("KH_3" if store == "egs" else "steamapps/common/KINGDOM HEARTS III")
    root = game_at(outer / "KINGDOM HEARTS III")
    legacy = installer_paths.legacy_probe_dll_path(root).parent.parent
    legacy.mkdir(parents=True)
    folders = [installer_paths.probe_config_path(root).parent, legacy]
    if store == "egs":
        monkeypatch.setattr(installer_paths, "kh3_executable_contains_signature", lambda *_: True)
        folders.append(installer_paths.path_specific_probe_config_path(root).parent)
    selected = tmp_path / "My Music 音楽 🎵"
    selected.mkdir()
    track = selected / "my_song.wav"
    track.write_bytes(b"player-owned audio")
    supplied = root if store == "egs" else outer  # Steam's manifest has an outer root.
    assert custom_music.save_directory(str(selected), supplied) == str(selected.resolve())
    assert custom_music.read_directory() == str(selected.resolve())
    for folder in folders:
        assert (folder / custom_music.SETTINGS_FILE).read_text(encoding="utf-8") == installer_paths.wine_visible_path_text(selected.resolve())
    # A later launch/configuration reads the persisted preference. Identical
    # settings must not keep replacing a file the runtime is polling.
    with monkeypatch.context() as scoped:
        scoped.setattr(custom_music, "NamedTemporaryFile", Mock(side_effect=AssertionError("unchanged config was rewritten")))
        custom_music.sync_runtime(root)
    assert custom_music.save_directory("", root) == ""
    assert custom_music.read_directory() == ""
    for folder in folders:
        assert (folder / custom_music.SETTINGS_FILE).read_bytes() == b""
    assert track.read_bytes() == b"player-owned audio"
    assert preferences[custom_music.PREFERENCE] == ""
    if store == "steam":
        assert not (outer / "Binaries").exists()


@pytest.mark.parametrize("kind", ["relative", "missing", "file"])
def test_invalid_folder_does_not_change_preference_or_runtime(tmp_path, preferences, kind):
    root = game_at(tmp_path / "game")
    existing = tmp_path / "music"
    existing.mkdir()
    custom_music.save_directory(str(existing), root)
    config = installer_paths.probe_config_path(root).parent / custom_music.SETTINGS_FILE
    before = config.read_bytes()
    candidate = {"relative": "relative/folder", "missing": str(tmp_path / "absent"), "file": str(config)}[kind]
    with pytest.raises(ValueError):
        custom_music.save_directory(candidate, root)
    assert config.read_bytes() == before
    assert custom_music.read_directory() == str(existing)


def test_preference_can_be_saved_before_game_is_installed(tmp_path, preferences):
    music = tmp_path / "music"
    music.mkdir()
    root = tmp_path / "not_installed"
    custom_music.save_directory(str(music), root)
    assert not root.exists()
    game_at(root)
    custom_music.sync_runtime(root)
    config = installer_paths.probe_config_path(root).parent / custom_music.SETTINGS_FILE
    assert config.read_text(encoding="utf-8") == installer_paths.wine_visible_path_text(music)


def test_runtime_gets_wine_visible_path(tmp_path, monkeypatch, preferences):
    root = game_at(tmp_path / "game")
    music = tmp_path / "music"
    music.mkdir()
    wine_path = "Z:\\home\\sora\\Music"
    translate = Mock(return_value=wine_path)
    monkeypatch.setattr(installer_paths, "wine_visible_path_text", translate)
    custom_music.save_directory(str(music), root)
    translate.assert_called_once_with(music.resolve())
    config = installer_paths.probe_config_path(root).parent / custom_music.SETTINGS_FILE
    assert config.read_text(encoding="utf-8") == wine_path
    assert custom_music.read_directory() == str(music)
