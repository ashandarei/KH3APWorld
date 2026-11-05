from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pymem

from .bridge_support.files import atomic_write_json
from .MemoryProbe import iter_readable_regions
from .platform_support.save_paths import default_save_directory, normalize_save_directory

ASCII_STRING_RE = re.compile(rb"[ -~]{4,}")

LEVEL_ID_TO_LEVEL_NAME = {
    "he_01": "Realm of the Gods",
    "he_02": "Mount of Olympus",
    "he_03": "Ruined Thebes",
    "he_04": "Restored Thebes",
    "he_05": "Ice & Lava Titan Arena",
    "he_06": "Tornado Titan Arena",
    "tt_01": "The Neighborhood",
    "ts_01": "Andy's House",
    "ts_02": "Galaxy Toys",
    "ts_03": "Verum Rex: Beat of Lead",
    "ts_04": "King of Toys Arena",
    "ra_01": "The Forest",
    "ra_02": "The Kingdom",
    "mi_01": "Monsters Inc.",
    "mi_02": "The Factory",
    "mi_03": "The Power Plant",
    "mi_04": "The Door Vault",
    "fz_01": "The North Mountain",
    "fz_02": "The Labyrinth of Ice",
    "fz_03": "Down the Mountain",
    "fz_04": "The North Mountain",
    "fz_06": "Skoll Arena",
    "bx_01": "The Bridge",
    "bx_02": "The City",
    "ca_01": "Port Royal",
    "ca_02": "The High Seas",
    "ca_03": "Shipwreck Cove",
    "ca_04": "Davy Jones' Locker",
    "dw_21": "The Realm of Darkness",
    "dw_22": "The Realm of Darkness",
    "kg_01": "The Badlands",
    "kg_02": "The Skein of Severance",
    "kg_04": "Dark Inferno Arena",
    "kg_05": "The Skein of Severance",
    "kg_06": "Replica Xehanort Arena",
    "kg_08": "Terra-Xehanort Arena",
    "ew_01": "Tutorial Arena",
    "ew_21": "Dark Olympus",
    "ew_22": "Dark Kingdom of Corona",
    "ew_23": "Dark Monstropolis",
    "ew_24": "Dark Toy Box",
    "ew_25": "Dark Arendelle",
    "ew_26": "Dark Caribbean",
    "ew_27": "Dark San Fransokyo",
}

PLAYER_START_TO_ROUTE_NAME = {
    "tt_01_Lv_Save_01": "Tram Common",
    "fz_01_Lv_Save_01": "Treescape",
    "he_03_Lv_Save_01": "Ruined Thebes",
    "he_03_Lv_Save_02": "Ruined Thebes",
}


def _extract_ascii_strings(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = path.read_bytes()
    return [match.group().decode("ascii", "ignore") for match in ASCII_STRING_RE.finditer(data)]


def _extract_named_property(strings: list[str], property_name: str) -> str | None:
    for index in range(len(strings) - 2):
        if strings[index] == property_name and strings[index + 1] == "NameProperty":
            return strings[index + 2]
    return None


def _extract_first_named_property_by_suffix(strings: list[str], suffix: str) -> str | None:
    for index in range(len(strings) - 2):
        if strings[index].endswith(suffix) and strings[index + 1] == "NameProperty":
            return strings[index + 2]
    return None


def _extract_current_world_code(save_dir: Path) -> tuple[str, str]:
    strings = _extract_ascii_strings(save_dir / "PlayerStart.sav")
    current_spawn = _extract_named_property(strings, "PlayerStart")
    if not current_spawn or not current_spawn.endswith("Spawn"):
        raise RuntimeError("Could not resolve current KH3 PlayerStart spawn from PlayerStart.sav")
    return current_spawn[:-5], current_spawn


def _extract_world_savepoint(save_dir: Path, world_code: str) -> tuple[str, str]:
    strings = _extract_ascii_strings(save_dir / f"SavePoint{world_code}.sav")
    level_path = _extract_named_property(strings, f"{world_code}LevelPath")
    player_start = _extract_named_property(strings, f"{world_code}PlayerStart")
    if not level_path:
        level_path = _extract_first_named_property_by_suffix(strings, "LevelPath")
    if not player_start:
        player_start = _extract_first_named_property_by_suffix(strings, "PlayerStart")
    if not level_path or not player_start:
        raise RuntimeError(f"Could not resolve savepoint metadata for world {world_code} from SavePoint{world_code}.sav")
    return level_path, player_start


def _extract_candidate_pairs(region_data: bytes) -> list[dict[str, Any]]:
    strings = [(match.start(), match.group().decode("ascii", "ignore")) for match in ASCII_STRING_RE.finditer(region_data)]
    level_paths = [(offset, value) for offset, value in strings if value.startswith("/Game/Levels/")]
    player_starts = [(offset, value) for offset, value in strings if value.endswith("_Lv_Save_01") or value.endswith("_Lv_Save_02")]
    pairs: list[dict[str, Any]] = []
    for level_offset, level_value in level_paths:
        following = [
            (start_offset, start_value)
            for start_offset, start_value in player_starts
            if start_offset > level_offset and start_offset - level_offset <= 0x200
        ]
        if not following:
            continue
        start_offset, start_value = following[0]
        level_id = extract_level_id(level_value)
        pairs.append(
            {
                "level_path_offset": level_offset,
                "level_path": level_value,
                "level_id": level_id,
                "level_name": LEVEL_ID_TO_LEVEL_NAME.get(level_id, level_id),
                "player_start_offset": start_offset,
                "player_start": start_value,
            }
        )
    return pairs


def extract_level_id(level_path: str) -> str | None:
    parts = [part for part in level_path.split("/") if part]
    if len(parts) < 2:
        return None
    candidate = parts[-1].lower()
    if re.fullmatch(r"[a-z]{2}_[0-9]{2}", candidate):
        return candidate
    return None


def resolve_player_start_route_name(player_start: str | None) -> str | None:
    if not player_start:
        return None
    return PLAYER_START_TO_ROUTE_NAME.get(player_start)


class KH3RuntimeSavepointReader:
    def __init__(
        self,
        save_dir: Path | str | None = None,
        process_name: str = "KINGDOM HEARTS III.exe",
        pid: int | None = None,
        max_region_size: int = 4_000_000,
    ):
        self.save_dir = normalize_save_directory(save_dir) if save_dir else default_save_directory().resolve()
        self.process_name = process_name
        self.pid = pid
        self.max_region_size = max_region_size

    def _open_process(self) -> pymem.Pymem:
        if self.pid is not None:
            pm = pymem.Pymem()
            pm.open_process_from_id(self.pid)
            return pm
        return pymem.Pymem(self.process_name)

    def build_snapshot(self) -> dict[str, Any]:
        world_code, active_spawn_name = _extract_current_world_code(self.save_dir)
        active_level_path, active_player_start = _extract_world_savepoint(self.save_dir, world_code)
        active_level_id = extract_level_id(active_level_path)
        region = self._locate_runtime_region(active_level_path, active_player_start)
        return {
            "source": "runtime-savepoint",
            "world_code": world_code,
            "active_spawn_name": active_spawn_name,
            "active_level_path": active_level_path,
            "active_level_id": active_level_id,
            "active_level_name": LEVEL_ID_TO_LEVEL_NAME.get(active_level_id, active_level_id),
            "active_player_start": active_player_start,
            "active_route_name": resolve_player_start_route_name(active_player_start),
            "memory_region_base": region["region_base"],
            "memory_region_size": region["region_size"],
            "level_path_address": region["level_path_address"],
            "player_start_address": region["player_start_address"],
            "candidate_pairs": region["candidate_pairs"],
        }

    def _locate_runtime_region(self, active_level_path: str, active_player_start: str) -> dict[str, Any]:
        encoded_level_path = active_level_path.encode("ascii")
        encoded_player_start = active_player_start.encode("ascii")

        pm = self._open_process()
        try:
            candidates: list[dict[str, Any]] = []
            for region_base, region_size in iter_readable_regions(pm.process_handle):
                if region_size > self.max_region_size:
                    continue
                try:
                    data = pm.read_bytes(region_base, region_size)
                except Exception:
                    continue

                level_offset = data.find(encoded_level_path)
                if level_offset == -1:
                    continue
                player_start_offset = data.find(encoded_player_start)
                if player_start_offset == -1:
                    continue

                candidate_pairs = _extract_candidate_pairs(data)
                candidates.append(
                    {
                        "region_base": region_base,
                        "region_size": region_size,
                        "level_path_address": region_base + level_offset,
                        "player_start_address": region_base + player_start_offset,
                        "offset_distance": abs(player_start_offset - level_offset),
                        "candidate_pairs": candidate_pairs,
                    }
                )

            if not candidates:
                raise RuntimeError(
                    f"Could not find a live KH3 runtime region containing both {active_level_path} and {active_player_start}"
                )

            candidates.sort(
                key=lambda item: (
                    0 if item["offset_distance"] == 0x100 else 1,
                    item["offset_distance"],
                    item["region_size"],
                )
            )
            return candidates[0]
        finally:
            pm.close_process()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve KH3 active savepoint metadata from live runtime memory.")
    parser.add_argument(
        "--save-dir",
        default=str(default_save_directory()),
        help="KH3 save directory containing PlayerStart.sav and SavePoint*.sav",
    )
    parser.add_argument(
        "--process-name",
        default="KINGDOM HEARTS III.exe",
        help="Target KH3 process name.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Target KH3 process id. Overrides --process-name when provided.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the resolved runtime snapshot JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    reader = KH3RuntimeSavepointReader(
        save_dir=args.save_dir,
        process_name=args.process_name,
        pid=args.pid,
    )
    payload = reader.build_snapshot()
    if args.output:
        atomic_write_json(args.output, payload)
        print(f"Wrote KH3 runtime snapshot to {args.output}")
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
