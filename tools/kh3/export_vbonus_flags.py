"""Regenerate the packaged VBonus flag catalog from the randomizer's primary table.

Uses the seed generator's Python asset reader. No live game or save files are accessed.
"""
import hashlib
import json
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'worlds/kh3'))
from seedgen.assets import Asset, get


def build_catalog(root=ROOT):
    relative = "randomizer/Content/Load/Tres/TresVBonusData"
    asset = root / "worlds/kh3" / relative
    flags = {row: get(properties, 'm_FlagName').value
             for row, properties in Asset.load(asset.with_suffix('.uasset')).rows.items()}
    if not flags:
        raise ValueError("No VBonus flags extracted")
    return {"schema": "kh3ap.vbonus_flags.v1", "source": relative,
               "source_sha256": {ext: hashlib.sha256(asset.with_suffix(ext).read_bytes()).hexdigest()
                                 for ext in (".uasset", ".uexp")}, "flags": flags}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    payload = build_catalog()
    output = ROOT / 'worlds/kh3/data/vbonus_flags.json'
    if args.check:
        if json.loads(output.read_text(encoding='utf-8')) != payload:
            parser.exit(1, 'VBonus flag catalog is stale\n')
    else:
        output.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(f"{'Verified' if args.check else 'Exported'} {len(payload['flags'])} VBonus flag mappings")


if __name__ == "__main__":
    main()
