from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from Options import PerGameCommonOptions

from .Minigames import MINIGAME_POOL_OPTION_NAMES
from .Options import KH3Options

KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY = "universal_tracker"
KH3_UNIVERSAL_TRACKER_SLOT_DATA_VERSION = 1
_UNIVERSAL_TRACKER_COMMON_OPTION_NAMES = frozenset({"exclude_locations"})
KH3_UNIVERSAL_TRACKER_OPTION_NAMES = tuple(
    option_name
    for option_name in KH3Options.type_hints
    if (
        option_name not in PerGameCommonOptions.type_hints
        or option_name in _UNIVERSAL_TRACKER_COMMON_OPTION_NAMES
    )
)
_KH3_UNIVERSAL_TRACKER_OPTION_NAME_SET = frozenset(
    KH3_UNIVERSAL_TRACKER_OPTION_NAMES
)


def build_universal_tracker_slot_data(options: KH3Options) -> dict[str, Any]:
    """Serialize resolved KH3 options needed by Universal Tracker regeneration."""
    return {
        "schema_version": KH3_UNIVERSAL_TRACKER_SLOT_DATA_VERSION,
        "options": options.as_dict(*KH3_UNIVERSAL_TRACKER_OPTION_NAMES),
    }


def universal_tracker_options_from_slot_data(
    slot_data: Mapping[str, Any],
) -> dict[str, Any]:
    tracker_data = slot_data.get(KH3_UNIVERSAL_TRACKER_SLOT_DATA_KEY)
    if tracker_data is None:
        raise ValueError(
            "KH3 slot data is missing the universal_tracker payload required for "
            "YAMLless Universal Tracker regeneration."
        )
    if not isinstance(tracker_data, Mapping):
        raise ValueError("KH3 Universal Tracker slot data must be an object.")

    schema_version = tracker_data.get("schema_version")
    if schema_version != KH3_UNIVERSAL_TRACKER_SLOT_DATA_VERSION:
        raise ValueError(
            "Unsupported KH3 Universal Tracker slot-data schema "
            f"{schema_version!r}; expected {KH3_UNIVERSAL_TRACKER_SLOT_DATA_VERSION}."
        )

    raw_options = tracker_data.get("options")
    if not isinstance(raw_options, Mapping):
        raise ValueError("KH3 Universal Tracker slot data is missing its options object.")

    restored_options = {
        option_name: value
        for option_name, value in raw_options.items()
        if option_name in _KH3_UNIVERSAL_TRACKER_OPTION_NAME_SET
    }

    # Preserve the shared mode when regenerating seeds made before the split.
    if "minigames_pool" in raw_options:
        for option_name in MINIGAME_POOL_OPTION_NAMES:
            restored_options.setdefault(option_name, raw_options["minigames_pool"])
    return restored_options


def restore_universal_tracker_options(
    options: KH3Options,
    slot_data: Mapping[str, Any],
) -> tuple[str, ...]:
    """Replace empty-YAML defaults with the resolved options from the real seed."""
    option_values = universal_tracker_options_from_slot_data(slot_data)
    if not option_values:
        raise ValueError(
            "KH3 slot data does not contain options for YAMLless Universal Tracker regeneration."
        )

    restored: list[str] = []
    for option_name, value in option_values.items():
        option_type = KH3Options.type_hints[option_name]
        try:
            legacy_songs = [r for r in slot_data.get("bridge", {}).get("locations", [])
                            if r.get("type") == "melody_of_memory"]
            if (option_name == "music_chest_count" and type(value) is int and 8 < value <= 128
                    and len(legacy_songs) == value
                    and all(not r.get("music_chest_placement_version") for r in legacy_songs)):
                # Reconstruct old seeds without raising the new-generation cap.
                parsed_value = option_type.from_any(0)
                parsed_value.value = value
            else:
                parsed_value = option_type.from_any(value)
        except Exception as exc:
            raise ValueError(
                f"Invalid KH3 Universal Tracker value for option {option_name!r}: {value!r}."
            ) from exc
        setattr(options, option_name, parsed_value)
        restored.append(option_name)

    return tuple(sorted(restored))
