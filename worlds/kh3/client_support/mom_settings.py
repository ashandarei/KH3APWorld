"""Read the seed-local preferences written by KH3's Melody of Memory menu."""

import json
from pathlib import Path

PLAY_STYLES = ("Normal", "OneButton", "Performer")
STARTING_ITEMS = ("None", "Potion", "MegaPotion", "SummoningStar")
PARTIES = ("Classic", "Days", "3D", "BBS")
DIFFICULTIES = (None, 0, 1, 2)  # Unset preference, Beginner, Standard, Proud.


def read_launch_settings(bridge_dir: Path, *, seed_difficulty: int, seed_options: dict | None = None) -> dict:
    settings = {}
    try:
        data = json.loads((Path(bridge_dir) / "mom_settings.json").read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 1:
            settings = data
    except (OSError, ValueError):
        pass

    def choice(key, choices):
        fallback = (seed_options or {}).get("melody_of_memory_" + key, 0) if key != "difficulty" else 0
        if type(fallback) is not int or not 0 <= fallback < len(choices):
            fallback = 0
        index = settings.get(key, fallback)
        return choices[index] if type(index) is int and 0 <= index < len(choices) else choices[fallback]

    difficulty = choice("difficulty", DIFFICULTIES)
    return {"PlayStyle": choice("play_style", PLAY_STYLES),
            "StartingItem": choice("starting_item", STARTING_ITEMS),
            "Party": choice("party", PARTIES),
            "Difficulty": seed_difficulty if difficulty is None else difficulty}
