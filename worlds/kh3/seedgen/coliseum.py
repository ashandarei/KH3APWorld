"""Overlay chest/cup reward suppression onto supported Olympus Coliseum paks."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from ..Pak import PakReader, pack_directory
from .assets import Asset, get

SOURCE_NAME = "zzzy_ColiseumMod_998_P.pak"
OUTPUT_NAME = "zzzz_APOlympusColiseumCompat_999_P.pak"
SUPPORTED_HASHES = {
    "9cfb5f5a9a12896690512fc7dab073938a8e0e2828561a4ec72ce764daed73e0",
    "a32e44ba76ffb6e085ab93ed972694c0b93889313f2ad23a2313133d55001ec9",
}
TREASURE = "KINGDOM HEARTS III/Content/Load/Tres/TresTreasureDataHE.uasset"
REWARD_DIRS = tuple(
    "KINGDOM HEARTS III/Content/Mods/Coliseum/" + directory + "/Rewards"
    for directory in ("MainData", "UserContent/Apro", "UserContent/BBS")
)


def build_compatibility(source: Path, output: Path, *, suppress_cup_rewards=True):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source.name.casefold() != SOURCE_NAME.casefold() or output.name.casefold() != OUTPUT_NAME.casefold():
        raise ValueError("Olympus Coliseum source/output pak names are unsupported")
    if source == output or output.exists():
        raise ValueError("Olympus Coliseum compatibility requires a new output path")
    with source.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if source_hash not in SUPPORTED_HASHES:
        raise ValueError(f"Unsupported Olympus Coliseum pak SHA-256: {source_hash}")
    with tempfile.TemporaryDirectory(prefix="kh3-coliseum-python-") as temporary:
        work = Path(temporary)
        extracted, staging = work / "source", work / "overlay"
        PakReader(source).extract(
            extracted,
            includes=[
                TREASURE,
                str(Path(TREASURE).with_suffix(".uexp")).replace("\\", "/"),
                *(REWARD_DIRS if suppress_cup_rewards else ()),
            ],
        )
        assets = []
        if suppress_cup_rewards:
            rewards = sorted(extracted.glob("**/Rewards_*.uasset"))
            if len(rewards) != 23:
                raise ValueError(f"Expected 23 Coliseum reward tables; found {len(rewards)}")
            for path in rewards:
                asset = Asset.load(path)
                if len(asset.rows) != 1:
                    raise ValueError(f"Expected one Coliseum reward row: {path.name}")
                arrays = [
                    p
                    for p in next(iter(asset.rows.values()))
                    if p.name.startswith("Rewards") and p.kind == "ArrayProperty"
                ]
                if len(arrays) != 19:
                    raise ValueError(f"Expected 19 reward arrays: {path.name}")
                for prop in arrays:
                    prop.set([])
                assets.append((path.relative_to(extracted), asset))
        treasure = Asset.load(extracted / TREASURE)
        if len(treasure.rows) < 32 or "HE_SBOX_016" not in treasure.rows:
            raise ValueError("Unexpected Coliseum Olympus chest layout")
        for row in treasure.rows.values():
            get(row, "m_TreasureName").set("ETresVictoryBonusKind::NONE")
        assets.append((Path(TREASURE), treasure))
        expected = {}
        for relative, asset in assets:
            primary, sidecar = asset.encode()
            # Parse before packaging to ensure the changed array sizes and export offsets are valid.
            verified = Asset(primary, sidecar)
            if relative == Path(TREASURE):
                if list(verified.rows) != list(treasure.rows) or any(
                    get(row, "m_TreasureName").value != "ETresVictoryBonusKind::NONE" for row in verified.rows.values()
                ):
                    raise ValueError("Coliseum chest suppression readback failed")
            elif any(p.value for p in next(iter(verified.rows.values())) if p.name.startswith("Rewards")):
                raise ValueError("Coliseum reward suppression readback failed")
            for path, data in ((relative, primary), (relative.with_suffix(".uexp"), sidecar)):
                target = staging / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                expected[path.as_posix()] = data
        packed = work / OUTPUT_NAME
        pack_directory(staging, packed)
        archive = PakReader(packed)
        if set(archive.entries) != set(expected) or any(archive.read(name) != data for name, data in expected.items()):
            raise ValueError("Coliseum compatibility pak readback failed")
        pack_directory(staging, output)
    return output
