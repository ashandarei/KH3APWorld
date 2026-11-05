"""Curated optional mods and reversible, install-scoped pak storage."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .olympus_coliseum_compat import (
    OLYMPUS_COLISEUM_COMPAT_PAK_NAME,
    OLYMPUS_COLISEUM_SOURCE_PAK_NAME,
)


@dataclass(frozen=True)
class CompatibleMod:
    id: str
    name: str
    version: str
    author: str
    url: str
    pak_name: str
    aliases: tuple[str, ...] = ()
    companion_name: str | None = None
    dependencies: tuple[CompatibleMod, ...] = ()
    download_file: str = ""


COLISEUM_DEPENDENCIES = (
    CompatibleMod(
        "phantom-aqua", "Zodiac Phantom Aqua", "1.0.6", "Aproydtix and sabertooth222",
        "https://www.nexusmods.com/kingdomhearts3/mods/2067?tab=files",
        "PhantomAqua_BossBattle.pak", download_file="Phantom Aqua (main file)",
    ),
    CompatibleMod(
        "roxas-coliseum", "Key of Destiny - Data Roxas Boss", "1.3", "DiamondHuntet",
        "https://www.nexusmods.com/kingdomhearts3/mods/2150?tab=files",
        "RoxasBoss_ColiseumVersion.pak", download_file="Roxas Boss - Coliseum Version",
    ),
)


COMPATIBLE_MODS = (CompatibleMod(
    "olympus-coliseum", "Olympus Coliseum", "1.4.2.1", "Aproydtix",
    "https://www.nexusmods.com/kingdomhearts3/mods/1184?tab=files",
    OLYMPUS_COLISEUM_SOURCE_PAK_NAME, ("zzzColiseumMod.pak",),
    OLYMPUS_COLISEUM_COMPAT_PAK_NAME,
    dependencies=COLISEUM_DEPENDENCIES,
),)


class CompatibleModsError(ValueError):
    pass


class CompatibleModsService:
    def __init__(self, mods_dir: Path, *, game_running: Callable[[], bool]) -> None:
        self.mods_dir = Path(mods_dir).expanduser().resolve()
        # Outside Content/Paks: Unreal may discover paks in its subdirectories.
        self.storage_root = self.mods_dir.parent.parent / "Archipelago Disabled Mods"
        self.game_running = game_running

    @property
    def catalog_path(self) -> Path:
        return self.storage_root / "third-party-mods.json"

    @staticmethod
    def _validate_entry(name: str, pak_name: str, url: str) -> None:
        if not name.strip():
            raise CompatibleModsError("Enter a mod name.")
        if (not pak_name.lower().endswith(".pak") or len(pak_name) <= 4
                or any(c in pak_name for c in '<>:"/\\|?*')
                or any(ord(c) < 32 for c in pak_name) or pak_name != pak_name.strip()):
            raise CompatibleModsError("Enter a .pak filename without a folder path.")
        if url and (urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc):
            raise CompatibleModsError("Download link must be an http or https URL.")

    def catalog(self) -> tuple[CompatibleMod, ...]:
        if not self.catalog_path.exists():
            return COMPATIBLE_MODS
        try:
            entries = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("Expected a list")
            mods = list(COMPATIBLE_MODS)
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"id", "name", "version", "author", "url", "pak_name"}:
                    raise ValueError("Invalid custom mod entry")
                if not all(isinstance(value, str) for value in entry.values()):
                    raise ValueError("Custom mod fields must be text")
                mod = CompatibleMod(**entry)
                if not re.fullmatch(r"custom-[0-9a-f]{32}", mod.id):
                    raise ValueError("Invalid custom mod id")
                self._validate_entry(mod.name, mod.pak_name, mod.url)
                self._check_duplicate(mods, mod.pak_name)
                if any(existing.id == mod.id for existing in mods):
                    raise ValueError("Duplicate custom mod id")
                mods.append(mod)
            return tuple(mods)
        except (TypeError, ValueError, AttributeError) as exc:
            raise CompatibleModsError(f"Could not read {self.catalog_path}: {exc}") from exc

    def _check_duplicate(self, mods, pak_name: str) -> None:
        for group in mods:
            for part in (group, *group.dependencies):
                if self.protected_name(pak_name, part) is not None:
                    raise CompatibleModsError("That pak already belongs to a listed mod.")

    def add_mod(self, name: str, pak_name: str, url: str = "") -> CompatibleMod:
        name, pak_name, url = name.strip(), pak_name.strip(), url.strip()
        self._validate_entry(name, pak_name, url)
        mods = self.catalog()
        self._check_duplicate(mods, pak_name)
        mod = CompatibleMod("custom-" + uuid4().hex, name, "Not specified", "User added", url, pak_name)
        entries = [
            {key: getattr(part, key) for key in ("id", "name", "version", "author", "url", "pak_name")}
            for part in (*mods[len(COMPATIBLE_MODS):], mod)
        ]
        self.storage_root.mkdir(parents=True, exist_ok=True)
        temporary = self.catalog_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.catalog_path)
        return mod

    def storage_dir(self, mod: CompatibleMod) -> Path:
        return self.storage_root / mod.id

    @staticmethod
    def protected_name(name: str, mod: CompatibleMod) -> str | None:
        """Recognize only this mod's names; preserve disabled companion state."""
        if name.casefold() in {n.casefold() for n in (mod.pak_name, *mod.aliases)}:
            return mod.pak_name
        if mod.id == "olympus-coliseum":
            if re.fullmatch(r"(?:zzz|zzzy_)ColiseumMod(?:_\d+_P)?\.pak", name, re.IGNORECASE):
                return mod.pak_name
            companion = re.fullmatch(
                r"(?:zzzz_)?APOlympusColiseumCompat(?:_\d+_P)?\.pak"
                r"(\.disabled-by-kh3ap(?:\.\d+)?)?", name, re.IGNORECASE,
            )
            if companion and mod.companion_name:
                return mod.companion_name + (companion.group(1) or "").lower()
        if mod.companion_name and name.casefold() == mod.companion_name.casefold():
            return mod.companion_name
        return None

    def _files(self, directory: Path, mod: CompatibleMod) -> list[Path]:
        if not directory.exists():
            return []
        result = []
        for path in directory.iterdir():
            if self.protected_name(path.name, mod) is not None:
                if not path.is_file() or path.is_symlink() or path.resolve().parent != directory.resolve():
                    raise CompatibleModsError(f"Expected a regular mod file: {path}")
                result.append(path)
        return sorted(result)

    def state(self, mod: CompatibleMod) -> str:
        active = self._files(self.mods_dir, mod)
        stored = self._files(self.storage_dir(mod), mod)
        active_sources = [p for p in active if self.protected_name(p.name, mod) == mod.pak_name]
        stored_sources = [p for p in stored if self.protected_name(p.name, mod) == mod.pak_name]
        duplicates = any(
            len({self.protected_name(p.name, mod) for p in files}) != len(files)
            for files in (active, stored)
        )
        if (active and stored) or duplicates:
            return "Conflict: files in both folders or duplicate mod paks"
        if active_sources:
            return "Active"
        if stored_sources:
            return "Inactive"
        return "Not installed"

    def normalize_installed(self, mod: CompatibleMod) -> bool:
        """Automatically enforce installed filenames, deferring while KH3 is open."""
        if self.game_running():
            return False
        state = self.state(mod)
        if state.startswith("Conflict"):
            raise CompatibleModsError(state + ". Check the mod and storage folders.")
        if state != "Active":
            return False
        moves = []
        for path in self._files(self.mods_dir, mod):
            destination = self.mods_dir / self.protected_name(path.name, mod)
            if path == destination:
                continue
            if destination.exists():
                raise CompatibleModsError(f"Will not overwrite {destination}.")
            moves.append((path, destination))
        self._move_files(moves)
        return bool(moves)

    def toggle(self, mod: CompatibleMod) -> str:
        if mod.dependencies:
            removing = all(self.state(part) == "Active" for part in (mod, *mod.dependencies))
            return self.set_group_active(mod, active=not removing)
        if self.game_running():
            raise CompatibleModsError("Close KH3 before restoring or removing mods.")
        state = self.state(mod)
        if state not in {"Active", "Inactive"}:
            raise CompatibleModsError(state + ". Check the mod and storage folders.")
        removing = state == "Active"
        source = self.mods_dir if removing else self.storage_dir(mod)
        target = self.storage_dir(mod) if removing else self.mods_dir
        target.mkdir(parents=True, exist_ok=True)
        # Reject redirected storage; keep moves on this installation's volume.
        if target.resolve() != target or source.resolve() != source:
            raise CompatibleModsError("Mod folders must not be redirected through symbolic links.")
        moves = []
        for path in self._files(source, mod):
            name = path.name if removing else self.protected_name(path.name, mod)
            destination = target / name
            if path == destination:
                continue
            if destination.exists():
                raise CompatibleModsError(f"Will not overwrite {destination}.")
            moves.append((path, destination))
        self._move_files(moves)
        return "Inactive" if removing else "Active"

    def group_problem(self, mod: CompatibleMod) -> str:
        """Explain why the full group cannot be restored, without modifying files."""
        problems = []
        for part in (mod, *mod.dependencies):
            state = self.state(part)
            if state == "Not installed":
                download_name = part.aliases[0] if part.aliases else part.pak_name
                problems.append(f"{part.name}: missing {download_name}. Extract it into {self.storage_dir(part)}")
            elif state not in {"Active", "Inactive"}:
                problems.append(f"{part.name}: {state}")
        if mod.id == "olympus-coliseum":
            for name in ("RoxasBoss.pak", "RoxasBoss_GoA_Version.pak", "RoxasBoss_NoWorldChanges.pak"):
                if (self.mods_dir / name).exists():
                    problems.append(f"Move {name} out of the mods folder before restoring the Coliseum version")
        return "; ".join(problems)

    def set_group_active(self, mod: CompatibleMod, *, active: bool) -> str:
        """Preflight the entire group, then move it with a single rollback boundary."""
        if self.game_running():
            raise CompatibleModsError("Close KH3 before restoring or removing mods.")
        if active:
            problem = self.group_problem(mod)
            if problem:
                raise CompatibleModsError(problem)
        moves = []
        for part in (mod, *mod.dependencies):
            state = self.state(part)
            if state.startswith("Conflict"):
                raise CompatibleModsError(f"{part.name}: {state}")
            source = self.storage_dir(part) if active else self.mods_dir
            target = self.mods_dir if active else self.storage_dir(part)
            files = self._files(source, part)
            if not files:
                continue
            target.mkdir(parents=True, exist_ok=True)
            if target.resolve() != target or source.resolve() != source:
                raise CompatibleModsError("Mod folders must not be redirected through symbolic links.")
            for path in files:
                name = self.protected_name(path.name, part) if active else path.name
                destination = target / name
                if destination.exists() or any(dst == destination for _, dst in moves):
                    raise CompatibleModsError(f"Will not overwrite {destination}.")
                moves.append((path, destination))
        self._move_files(moves)
        return "Active" if active else "Inactive"

    @staticmethod
    def _move_files(moves: list[tuple[Path, Path]]) -> None:
        completed = []
        try:
            for path, destination in moves:
                # Path.rename refuses to overwrite on Windows.
                path.rename(destination)
                completed.append((path, destination))
        except OSError as exc:
            failures = []
            for path, destination in reversed(completed):
                try:
                    destination.rename(path)
                except OSError as rollback_exc:
                    failures.append(str(rollback_exc))
            detail = f" Rollback needs attention: {'; '.join(failures)}" if failures else " Moves rolled back."
            raise CompatibleModsError(f"Could not move mod: {exc}.{detail}") from exc
