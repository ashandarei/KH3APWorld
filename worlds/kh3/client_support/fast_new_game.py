"""Local startup preferences; independent of the seed's gameplay settings."""
from pathlib import Path

import Utils

from ..bridge_support.files import atomic_write_json

TEXT_LANGUAGES = {"German": 1, "English": 2, "Spanish": 3, "French": 4,
                  "Italian": 5, "Japanese": 6, "Korean": 7, "Traditional Chinese": 8}
VOICE_LANGUAGES = {"English": 10, "Japanese": 11}
KEYBOARD_LAYOUTS = {"QWERTY": 13, "AZERTY": 14, "QWERTZ": 15}
DIFFICULTIES = {"Ask each time": None, "Beginner": 0, "Standard": 1, "Proud": 2, "Critical": 3}
DEFAULTS = {"enabled": False, "text": 2, "voice": 10, "keyboard": 13,
            "direct_new_game": False, "difficulty": None}
SETTINGS_FILE = "fast_new_game_settings.json"


def normalize(value: object) -> dict:
    if not isinstance(value, dict):
        return dict(DEFAULTS)
    result = dict(DEFAULTS)
    result["enabled"] = value.get("enabled") is True
    for key, choices in (("text", TEXT_LANGUAGES), ("voice", VOICE_LANGUAGES),
                         ("keyboard", KEYBOARD_LAYOUTS)):
        selected = value.get(key)
        if type(selected) is int and selected in choices.values():
            result[key] = selected
        elif result["enabled"]:
            # Never silently auto-confirm a malformed preference.
            result["enabled"] = False
    result["direct_new_game"] = value.get("direct_new_game") is True
    difficulty = value.get("difficulty")
    if difficulty is None or (type(difficulty) is int and difficulty in range(4)):
        result["difficulty"] = difficulty
    else:
        result["direct_new_game"] = False
    return result


def read_settings() -> dict:
    return normalize(Utils.persistent_load().get("kh3_client", {}).get("fast_new_game"))


def sync_bridge(bridge: Path | None, settings: dict | None = None) -> None:
    if bridge is not None:
        atomic_write_json(bridge / SETTINGS_FILE,
                          {"schema_version": 1, **normalize(settings if settings is not None else read_settings())})


def save_settings(settings: dict, bridge: Path | None, kh3_dir: Path | None = None) -> dict:
    settings = normalize(settings)
    from .startup_video import install_short_copyright_video
    install_short_copyright_video(kh3_dir, enabled=settings["enabled"])
    sync_bridge(bridge, settings)
    Utils.persistent_store("kh3_client", "fast_new_game", settings)
    return settings
