"""Operator map aliases for the KH3 worlds and imported KH0.2 hosts."""
from __future__ import annotations

from functools import lru_cache
import json
import re

from ..PackageResources import read_package_text

AUTO_PLAYER_START = "KH3AP_AutoPlayerStart"

# Curated normal entry maps, rather than whichever map sorts first in a zone
# (Olympus he_01 is the Realm of the Gods; its opening area is he_02).
ZONE_START_MAPS = {
    "he": "he_02",
    "tt": "tt_01",
    "ts": "ts_01",
    "ra": "ra_01",
    "mi": "mi_01",
    "fz": "fz_01",
    "frozen": "fz_01",
    "arendelle": "fz_01",
    "ca": "ca_01",
    "bx": "bx_01",
    "kg": "kg_01",
    "ew": "ew_02",
    "dp": "dp_01",
    "po": "po_01",
    "dw": "dw_21",
    "rg": "rg_03",
    "sp": "sp_01",
    "ss": "ss_01",
    "goa": "goa_01",
}

# Host identities match KH3Runtime/authoring/kh02/runtime_metadata.json and
# its generated mirror_host_metadata.h. Never travel into a source game's persistent package.
KH02_MAPS = (
    (2, "APKH02CastleTown", "/Game/Maps/APKH02CastleTown/APKH02CastleTown", "CastleTown"),
    (3, "APKH02WorldWithin", "/Game/Maps/APKH02WorldWithin/APKH02WorldWithin", "WorldWithin"),
    (4, "APKH02Stairs", "/Game/Maps/APKH02Mirrors/APKH02Stairs", "Stairs"),
    (5, "APKH02Corridor", "/Game/Maps/APKH02Mirrors/APKH02Corridor", "Corridor"),
    (6, "APKH02Tunnel", "/Game/Maps/APKH02Mirrors/APKH02Tunnel", "Tunnel"),
    (7, "APKH02Phantom", "/Game/Maps/APKH02Mirrors/APKH02Phantom", "PhantomAqua"),
    (8, "APKH02Forest", "/Game/Maps/APKH02Mirrors/APKH02Forest", "Forest"),
    (9, "APKH02Depths", "/Game/Maps/APKH02Mirrors/APKH02Depths", "Depths"),
    (10, "APKH02DestinyIslands", "/Game/Maps/APKH02Mirrors/APKH02DestinyIslands", "DestinyIslands"),
    (11, "APKH02MirrorChallenge", "/Game/Maps/APKH02Mirrors/APKH02MirrorChallenge", "MirrorChallenge"),
    (59, "APKH02Transition", "/Game/Maps/APKH02Mirrors/APKH02Transition", "KH02Transition"),
    (60, "APKH02Epilogue", "/Game/Maps/APKH02Mirrors/APKH02Epilogue", "Epilogue"),
)


@lru_cache(maxsize=1)
def _map_names() -> dict[str, set[str]]:
    entries = json.loads(read_package_text(
        "data/runtime_map_ap_grant_classification.json", module_file=__file__,
    ))["entries"]
    names: dict[str, set[str]] = {}
    for row in entries:
        # The grant audit also records incomplete/manual paths. They are not
        # persistent-map destinations and must not make a real map ambiguous.
        if row["path"].rsplit("/", 1)[-1].casefold() == row["map"].casefold():
            names.setdefault(row["map"].casefold(), set()).add(row["path"])
    for zone, first_map in ZONE_START_MAPS.items():
        names[zone] = names[first_map].copy()
    for area, host, package, alias in KH02_MAPS:
        code = f"dw_{area:02}"
        for name in (code, f"kh02:{code}", host, alias,
                     f"/Game/Levels/dw/{code}/{code}"):
            names[name.casefold()] = {package}
    names["kh02"] = {KH02_MAPS[0][2]}
    names["0.2"] = {KH02_MAPS[0][2]}
    return names


def map_path(name: str) -> str:
    matches = _map_names().get(name.casefold(), set())
    if len(matches) == 1:
        return next(iter(matches))
    if re.fullmatch(r"/Game/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+", name):
        return name
    raise ValueError(f"Unknown or ambiguous map '{name}'. Supply the full /Game/... map path.")
