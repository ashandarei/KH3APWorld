# SPDX-License-Identifier: GPL-3.0-only
"""KH3 player YAML data and validation, independent of the desktop frontend."""

from copy import deepcopy
from inspect import cleandoc
import re

import yaml

from BaseClasses import PlandoOptions
from Options import Choice, NamedRange, OptionCounter, OptionSet, Range, Toggle, Visibility, get_option_groups
from .Options import KH3NumericChoice


GAME = "Kingdom Hearts III"


class SettingsValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("\n".join(issues.values()))


def clamp_range_value(option, value):
    """Clamp completed integer input, preserving random and named special values."""
    if not issubclass(option, Range) or isinstance(value, (bool, dict)):
        return value
    if not (type(value) is int or isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip())):
        return value
    number = int(value)
    if issubclass(option, NamedRange) and number in option.special_range_names.values():
        return number
    return max(option.range_start, min(option.range_end, number))


def world_type():
    from . import KH3World
    return KH3World


def option_groups():
    groups = get_option_groups(world_type(), Visibility.simple_ui)
    # Put the run's goal first; shared Archipelago settings live at the end.
    return {**{key: value for key, value in groups.items() if key != "Game Options"},
            "Archipelago": groups.get("Game Options", {})}


def option_description(option):
    """Remove source-code line wrapping without flattening structured help."""
    lines = []
    fenced = False
    for source_line in cleandoc(option.__doc__ or "").splitlines():
        line = source_line.strip()
        if line.startswith("```"):
            fenced = not fenced
            lines.append(source_line)
        elif fenced or source_line.startswith("    "):
            lines.append(source_line)
        elif not line:
            lines.append("")
        elif (not lines or not lines[-1] or lines[-1].endswith(":")
              or re.match(r"(?:[-*+]\s|\d+[.)]\s|>\s|[\w /+-]{1,40}:\s)", line)):
            lines.append(line)
        else:
            lines[-1] += " " + line
    return "\n".join(lines)


def plain_value(value):
    if isinstance(value, dict):
        return {key: plain_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [plain_value(item) for item in value]
    return value


def default_value(option):
    value = deepcopy(option.default)
    if issubclass(option, Toggle):
        return bool(value)
    if issubclass(option, KH3NumericChoice):
        return value
    if issubclass(option, Choice):
        return option(value).current_key
    return plain_value(value)


def entry_label(option, key):
    return getattr(option, "valid_key_labels", {}).get(key, key)


def catalog(option):
    world = world_type()
    if getattr(option, "verify_item_name", False):
        return sorted(world.item_names)
    if getattr(option, "verify_location_name", False):
        return sorted(world.location_names)
    keys = getattr(option, "catalog_keys", getattr(option, "valid_keys", ()))
    return sorted(keys, key=lambda key: entry_label(option, key).casefold())


def is_random(value):
    return isinstance(value, str) and (value == "random" or value.startswith("random-"))


def normalize_value(option, value):
    """Validate without rolling exported random settings into fixed choices."""
    if is_random(value) and issubclass(option, (Toggle, Choice, Range)):
        if value not in (("random", "random-low", "random-high", "random-middle")
                         if issubclass(option, Range) else ("random",)):
            raise ValueError("Use random, or a supported range randomization mode.")
        return value
    if isinstance(value, dict) and option.supports_weighting:
        if not value or not any(value.values()):
            raise ValueError("Weighted choices need at least one positive weight.")
        for choice, weight in value.items():
            if type(weight) is not int or weight < 0:
                raise ValueError("Weights must be nonnegative integers.")
            normalize_value(option, choice)
        return deepcopy(value)
    # Reject accidental strings/maps instead of allowing OptionSet to iterate keys.
    if issubclass(option, OptionSet) and not isinstance(value, (list, set, frozenset, tuple)):
        raise ValueError("Select entries from the list.")
    if issubclass(option, OptionCounter):
        if not isinstance(value, dict) or any(type(count) is not int or count < 0 for count in value.values()):
            raise ValueError("Item counts must be nonnegative whole numbers.")
    parsed = option.from_any(deepcopy(value))
    parsed.verify(world_type(), "Player", PlandoOptions.bosses)
    if issubclass(option, Toggle):
        return bool(parsed.value)
    if issubclass(option, KH3NumericChoice):
        return parsed.value
    if issubclass(option, Choice):
        return parsed.current_key
    return plain_value(parsed.value)


class PlayerOptions:
    def __init__(self):
        self.groups = option_groups()
        self.types = {key: option for group in self.groups.values() for key, option in group.items()}
        self.values = {key: default_value(option) for key, option in self.types.items()}
        self.name = "Player"
        self.description = "Created with the KH3 YAML Creator"

    def _validate_values(self):
        values = {}
        issues = {}
        for key, value in self.values.items():
            try:
                values[key] = normalize_value(self.types[key], value)
            except Exception as exc:
                issues[key] = f"{self.types[key].display_name}: {exc}"
        for first, second, message in (
                ("local_items", "non_local_items", "Items cannot be both local and non-local: "),
                ("exclude_locations", "priority_locations", "Locations cannot be both excluded and priority: ")):
            overlap = set(values.get(first, ())) & set(values.get(second, ()))
            if overlap:
                issues[first] = issues[second] = message + ", ".join(sorted(overlap))
        return values, issues

    def validated_values(self):
        values, issues = self._validate_values()
        if issues:
            raise SettingsValidationError(issues)
        return values

    def draft_text(self):
        return yaml.safe_dump({"name": self.name.strip() or "Player", "game": GAME,
                               "description": self.description, GAME: self.values},
                              sort_keys=False, allow_unicode=True, width=100)

    def export_text(self):
        name = self.name.strip() or "Player"
        values, issues = self._validate_values()
        if any(character in self.name for character in "\n\r\0"):
            issues["name"] = "Enter a player name on one line."
        if issues:
            raise SettingsValidationError(issues)
        return yaml.safe_dump({"name": name, "game": GAME, "description": self.description,
                               GAME: values}, sort_keys=False, allow_unicode=True,
                              width=100)

    @classmethod
    def from_text(cls, text):
        document = yaml.safe_load(text)
        if not isinstance(document, dict) or document.get("game") != GAME:
            raise SettingsValidationError({"game": "Choose a single-player Kingdom Hearts III YAML."})
        unexpected = set(document) - {"name", "game", "description", GAME}
        if unexpected:
            raise SettingsValidationError({str(key): "This editor cannot import this YAML section." for key in unexpected})
        values = document.get(GAME, {})
        if not isinstance(values, dict):
            raise SettingsValidationError({GAME: "The Kingdom Hearts III section must contain option names and values."})
        result = cls()
        unexpected = set(values) - result.types.keys()
        if unexpected:
            raise SettingsValidationError({str(key): "Unsupported or unknown option." for key in unexpected})
        if not isinstance(document.get("name", "Player"), str):
            raise SettingsValidationError({"name": "The player name must be text."})
        if not isinstance(document.get("description", ""), str):
            raise SettingsValidationError({"description": "The description must be text."})
        result.name = document.get("name", "Player")
        result.description = document.get("description", result.description)
        result.values.update(values)
        result.export_text()
        result.values = result.validated_values()
        return result
