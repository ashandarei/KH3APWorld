"""Shared song selectors and category metadata for generation and the YAML editor."""

from __future__ import annotations

import json
from importlib.resources import files


MOM_SONGS = tuple(json.loads(
    files(__package__).joinpath("data/mom_songs.json").read_text(encoding="utf-8")
)["songs"])
MOM_SONG_BY_ID = {song["song_id"]: song for song in MOM_SONGS}
MOM_SONG_KEYS = {
    song["song_id"]: f"{song['name']} ({song['series']})" for song in MOM_SONGS
}
_groups = json.loads(
    files(__package__).joinpath("data/mom_song_groups.json").read_text(encoding="utf-8")
)
MOM_SONG_GROUPS = [
    {"key": f"{prefix}: {name}", "label": name, "kind": kind, "song_ids": ids}
    for section, prefix, kind in (("themes", "Theme", "theme"), ("worlds", "World", "world"))
    for name, ids in _groups[section].items()
] + [
    {"key": f"Source: {name}", "label": name, "kind": "source",
     "song_ids": [song["song_id"] for song in MOM_SONGS if song["series"] in series]}
    for name, series in _groups["sources"].items()
]
MOM_SELECTOR_IDS = {key: frozenset([song_id]) for song_id, key in MOM_SONG_KEYS.items()}
MOM_SELECTOR_IDS.update({group["key"]: frozenset(group["song_ids"]) for group in MOM_SONG_GROUPS})
MOM_SELECTOR_KEYS = frozenset(MOM_SELECTOR_IDS)
# Exported through the option schema; the website never maintains a second catalog.
MOM_SONG_CATALOG = {
    "songs": [{"key": MOM_SONG_KEYS[song["song_id"]], "name": song["name"],
               "series": song["series"]} for song in MOM_SONGS],
    "groups": [{"key": group["key"], "label": group["label"], "kind": group["kind"],
                "songs": [MOM_SONG_KEYS[song_id] for song_id in group["song_ids"]]}
               for group in MOM_SONG_GROUPS],
}


def expand_song_selectors(selectors) -> set[int]:
    result = set()
    for selector in selectors:
        if selector not in MOM_SELECTOR_IDS:
            raise ValueError(f"Unknown Melody of Memory song or category: {selector!r}")
        result.update(MOM_SELECTOR_IDS[selector])
    return result


def eligible_song_ids(whitelist=(), blacklist=()) -> list[int]:
    """A nonempty whitelist is exclusive and wins every blacklist conflict."""
    allowed = expand_song_selectors(whitelist)
    blocked = expand_song_selectors(blacklist)
    return sorted(allowed if whitelist else set(MOM_SONG_BY_ID) - blocked)


def draw_song_ids(rng, count: int, eligible: list[int]) -> list[int]:
    if count and not eligible:
        raise ValueError("No eligible Melody of Memory songs. Change the song whitelist or blacklist.")
    result = []
    while len(result) < count:
        # Exhaust a shuffled pass before any song can appear again.
        result.extend(rng.sample(eligible, min(len(eligible), count - len(result))))
    return result
