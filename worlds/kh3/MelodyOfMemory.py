"""Seed-owned Melody of Memory field-battle checks.

Catalog IDs are native Music IDs, independent of catalog order and difficulty.
Keep published names/IDs stable when extending the catalog.
"""

from __future__ import annotations

import json
from importlib.resources import files

from Options import OptionError

from .MoMSongCatalog import MOM_SONGS, MOM_SONG_BY_ID, draw_song_ids, eligible_song_ids

MOM_LOCATION_ID_BASE = 0x4B4A3300
# Offset from the published parent chest ID, never from its randomized song.
MUSIC_CHEST_BONUS_ID_OFFSET = 0x100000


def music_chest_bonus_name(chest_name: str) -> str:
    return f"{chest_name} - Song Bonus"


MUSIC_CHEST_PLACEMENTS = tuple(json.loads(
    files(__package__).joinpath("data/music_chest_placements.json").read_text(encoding="utf-8")
)["placements"])
MUSIC_CHEST_BY_ID = {p["placement_id"]: p for p in MUSIC_CHEST_PLACEMENTS}


def placed_music_chest(placement_id: str, song_id: int, difficulty: int) -> dict:
    placement = MUSIC_CHEST_BY_ID[placement_id]
    row = song_location(song_id, difficulty)
    row.update(name=placement["name"], world=placement["world"],
               source_table="MusicChest", source_id=placement["treasure_id"],
               location_description=placement["name"], music_chest_kind="additional",
               music_chest_placement_id=placement_id, music_chest_placement_version=1,
               music_chest_map=placement["map_path"], music_chest_position=list(placement["position"]),
               music_chest_rotation=list(placement["rotation"]))
    return row


MOM_DIFFICULTIES = ("Beginner", "Standard", "Proud")


def song_location_name(song: dict) -> str:
    return f"Melody of Memory - {song['name']} ({song['series']}) Clear"


def song_location(song_id: int, difficulty: int) -> dict:
    song = MOM_SONG_BY_ID[song_id]
    if type(difficulty) is not int or difficulty not in range(3):
        raise ValueError("Invalid Melody of Memory difficulty.")
    name = song_location_name(song)
    return {
        "name": name, "type": "melody_of_memory", "world": "Melody of Memory",
        "source_table": "MelodyOfMemory", "source_id": str(song_id),
        "location_description": name, "primary_item_name": "Potion",
        "vanilla_item": "ITEM_POTION", "vanilla_item_name": "Potion",
        "mom_song_id": song_id, "mom_song_name": song["name"],
        "mom_series": song["series"], "mom_difficulty": difficulty,
    }


def select_song_locations(rng, count: int, difficulty: int, slot_data: dict | None = None) -> list[dict]:
    if slot_data is not None:
        # UT must reconstruct the actual seed's subset, regardless of its RNG.
        rows = [row for row in slot_data.get("bridge", {}).get("locations", [])
                if row.get("type") == "melody_of_memory"]
        ids = [row.get("mom_song_id") for row in rows]
        if (len(ids) != count or any(type(i) is not int or i not in MOM_SONG_BY_ID for i in ids)
                or len(set(ids)) != len(ids)
                or any(row.get("mom_difficulty") != difficulty for row in rows)):
            raise ValueError("Invalid Melody of Memory checks in tracker slot data.")
    else:
        ids = rng.sample(sorted(MOM_SONG_BY_ID), count) if count else []
    return [song_location(i, difficulty) for i in sorted(ids)]


def select_music_chests(rng, locations: list[dict], count: int, convert: int,
                        difficulty: int, slot_data: dict | None = None,
                        *, whitelist=(), blacklist=()) -> list[dict]:
    """Assign eligible songs in shuffled passes, preserving chest identity and logic.

    Added chest identities and transforms come from the authored catalog.
    Conversion preserves the original AP check; bonus checks are added separately.
    """
    if not 0 <= count <= len(MUSIC_CHEST_PLACEMENTS):
        raise ValueError("Additional music chest count exceeds the authored placement catalog (8).")
    candidates = { (r["source_table"], r["source_id"]): r for r in locations
                   if r.get("type") == "treasure" }
    if isinstance(convert, bool):
        convert = len(MOM_SONGS) if convert else 0
    if not 0 <= convert <= len(MOM_SONGS):
        raise ValueError("Vanilla music chest count exceeds the song catalog.")
    converted_count = min(convert, len(candidates))
    eligible = eligible_song_ids(whitelist, blacklist)
    if count + converted_count and not eligible:
        raise OptionError("No eligible Melody of Memory songs. Change the song whitelist or blacklist.")
    fields = ("mom_song_id", "mom_song_name", "mom_series", "mom_difficulty", "music_chest_kind")
    if slot_data is not None:
        rows = slot_data.get("bridge", {}).get("locations", [])
        placed = [r for r in rows if r.get("music_chest_placement_version") == 1]
        if len(placed) != count:
            raise ValueError("Invalid additional chest placements in tracker slot data.")
        if placed:
            placement_ids = [r.get("music_chest_placement_id") for r in placed]
            if (len(placed) != count or len(set(placement_ids)) != count
                    or any(i not in MUSIC_CHEST_BY_ID for i in placement_ids)
                    or any(type(r.get("mom_song_id")) is not int or r["mom_song_id"] not in eligible for r in placed)):
                raise ValueError("Invalid additional chest placements in tracker slot data.")
            added = [placed_music_chest(r["music_chest_placement_id"], r["mom_song_id"], difficulty) for r in placed]
            from .Locations import location_name_to_id
            for original, expected in zip(placed, added):
                # Archipelago serializes slot-data lists as tuples.
                if (original.get("mom_difficulty") != difficulty
                        or original.get("ap_location_id") != location_name_to_id[expected["name"]]
                        or any(original.get(k) != expected[k] for k in ("source_id", "source_table", "world",
                            "music_chest_map"))
                        or any(not isinstance(original.get(k), (list, tuple))
                            or tuple(original[k]) != tuple(expected[k])
                            for k in ("music_chest_position", "music_chest_rotation"))):
                    raise ValueError("Invalid additional chest identity in tracker slot data.")
        else:
            added = []
        converted = [r for r in rows if r.get("music_chest_kind") == "vanilla"]
        ids = [r["mom_song_id"] for r in added] + [r.get("mom_song_id") for r in converted]
        keys = [(r.get("source_table"), r.get("source_id")) for r in converted]
        if (len(converted) != converted_count or len(set(keys)) != len(keys)
                or any(k not in candidates for k in keys)
                or any(type(i) is not int or i not in eligible for i in ids)
                or any(r.get("mom_difficulty") != difficulty for r in converted)):
            raise ValueError("Invalid music chest assignments in tracker slot data.")
        for key, row in zip(keys, converted):
            metadata = song_location(row["mom_song_id"], difficulty)
            metadata["music_chest_kind"] = "vanilla"
            candidates[key].update({f: metadata[f] for f in fields})
    else:
        songs = draw_song_ids(rng, count + converted_count, eligible)
        placements = sorted(rng.sample(list(MUSIC_CHEST_BY_ID), count)) if count else []
        added = [placed_music_chest(p, i, difficulty) for p, i in zip(placements, songs[:count])]
        keys = rng.sample(sorted(candidates), converted_count) if converted_count else []
        for key, song_id in zip(keys, songs[count:]):
            metadata = song_location(song_id, difficulty)
            metadata["music_chest_kind"] = "vanilla"
            candidates[key].update({f: metadata[f] for f in fields})
    for row in added:
        row["music_chest_kind"] = "additional"
    return added


def music_chest_bonus_locations(locations: list[dict],
                                slot_data: dict | None = None) -> list[dict]:
    """Attach a synthetic reward to each physical music chest's opening."""
    from .Locations import location_name_to_id

    bonuses = []
    for chest in locations:
        if not (chest.get("music_chest_kind") == "vanilla"
                or chest.get("music_chest_placement_version") == 1):
            continue
        parent_key = f"{chest['source_table']}:{chest['source_id']}"
        name = music_chest_bonus_name(chest["name"])
        bonuses.append({
            "name": name, "location_description": name,
            "type": "music_chest_bonus", "world": chest["world"],
            "source_table": "MusicChestBonus", "source_id": parent_key,
            "primary_item_name": "Potion", "vanilla_item": "ITEM_POTION",
            "vanilla_item_name": "Potion", "trigger_bridge_key": parent_key,
            "music_chest_parent_location_id": location_name_to_id[chest["name"]],
            "music_chest_parent_location_name": chest["name"],
        })
    if slot_data is not None:
        expected = {location_name_to_id[row["name"]]: row for row in bonuses}
        actual = [row for row in slot_data.get("bridge", {}).get("locations", [])
                  if row.get("type") == "music_chest_bonus"]
        if len(actual) != len(expected) or {row.get("ap_location_id") for row in actual} != set(expected):
            raise ValueError("Invalid music chest bonus selection in tracker slot data.")
        for row in actual:
            wanted = expected[row["ap_location_id"]]
            if (row.get("ap_location_name") != wanted["name"]
                    or any(row.get(key) != wanted[key] for key in (
                        "source_table", "source_id", "world", "trigger_bridge_key",
                        "music_chest_parent_location_id", "music_chest_parent_location_name"))):
                raise ValueError("Invalid music chest bonus identity in tracker slot data.")
    return bonuses
