# SPDX-License-Identifier: GPL-3.0-only
"""Local, reusable KH3 settings stored as ordinary YAML files."""

import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import yaml

import Utils
from .yaml_options import GAME, PlayerOptions


class PresetStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else Path(Utils.user_path("kh3", "yaml-presets"))

    def _path(self, name):
        name = name.strip()
        if (not name or len(name) > 100 or name.endswith(".")
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name in (".", "..")
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.IGNORECASE)):
            raise ValueError("Choose a preset name of 1–100 characters without filename symbols or reserved names.")
        return self.directory / f"{name}.yaml"

    def names(self):
        if not self.directory.exists():
            return []
        return sorted((path.stem for path in self.directory.glob("*.yaml") if path.is_file()), key=str.casefold)

    def save(self, name, model):
        path = self._path(name)
        content = yaml.safe_dump({"name": "Player", "game": GAME, "description": model.description,
                                  GAME: model.validated_values()}, sort_keys=False, allow_unicode=True, width=100)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                    prefix=".preset-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path.stem

    def load(self, name):
        return PlayerOptions.from_text(self._path(name).read_text(encoding="utf-8-sig"))

    def delete(self, name):
        self._path(name).unlink()
