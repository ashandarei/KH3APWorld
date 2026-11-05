import json
from unittest.mock import patch

import pytest

from worlds.kh3.client_support import fast_new_game as settings


def test_disabled_by_default():
    assert settings.normalize(None) == settings.DEFAULTS


@pytest.mark.parametrize("key,bad", [("text", 9), ("voice", 12), ("keyboard", 16),
                                     ("text", True), ("voice", "10"), ("keyboard", None)])
def test_bad_preferences_disable_automatic_confirmation(key, bad):
    value = {**settings.DEFAULTS, "enabled": True, key: bad}
    assert settings.normalize(value)["enabled"] is False


@pytest.mark.parametrize("text", settings.TEXT_LANGUAGES.values())
@pytest.mark.parametrize("voice", settings.VOICE_LANGUAGES.values())
@pytest.mark.parametrize("keyboard", settings.KEYBOARD_LAYOUTS.values())
def test_preference_round_trip(tmp_path, text, voice, keyboard):
    value = {**settings.DEFAULTS, "enabled": True, "text": text, "voice": voice, "keyboard": keyboard}
    with patch.object(settings.Utils, "persistent_store") as store:
        assert settings.save_settings(value, tmp_path) == value
    store.assert_called_once_with("kh3_client", "fast_new_game", value)
    assert json.loads((tmp_path / settings.SETTINGS_FILE).read_text()) == {"schema_version": 1, **value}


def test_new_bridge_gets_saved_preferences_and_disable_is_published(tmp_path):
    value = {"enabled": True, "text": 4, "voice": 11, "keyboard": 14}
    with patch.object(settings.Utils, "persistent_load", return_value={"kh3_client": {"fast_new_game": value}}):
        settings.sync_bridge(tmp_path)
    assert json.loads((tmp_path / settings.SETTINGS_FILE).read_text())["text"] == 4
    settings.sync_bridge(tmp_path, {**value, "enabled": False})
    assert json.loads((tmp_path / settings.SETTINGS_FILE).read_text())["enabled"] is False


def test_preferences_can_be_saved_before_selecting_seed():
    with patch.object(settings.Utils, "persistent_store") as store:
        settings.save_settings(dict(settings.DEFAULTS), None)
    store.assert_called_once()


@pytest.mark.parametrize("difficulty", [None, 0, 1, 2, 3])
def test_optional_difficulty_round_trip(tmp_path, difficulty):
    value = {**settings.DEFAULTS, "direct_new_game": True, "difficulty": difficulty}
    with patch.object(settings.Utils, "persistent_store"):
        assert settings.save_settings(value, tmp_path) == value
    assert json.loads((tmp_path / settings.SETTINGS_FILE).read_text())["difficulty"] == difficulty


@pytest.mark.parametrize("difficulty", [True, "1", -1, 4])
def test_invalid_difficulty_disables_direct_start(difficulty):
    assert not settings.normalize({**settings.DEFAULTS, "direct_new_game": True,
                                   "difficulty": difficulty})["direct_new_game"]


def test_existing_preferences_do_not_enable_direct_new_game():
    value = settings.normalize({"enabled": True, "text": 2, "voice": 11, "keyboard": 13})
    assert value["enabled"]
    assert not value["direct_new_game"]
    assert value["difficulty"] is None
