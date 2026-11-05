"""Regenerate the packaged Event flag catalog from the randomizer's primary table.

Uses the seed generator's Python asset reader. No live game or save files are accessed.
"""
import hashlib
import json
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'worlds/kh3'))
from seedgen.assets import Asset
from seedgen.tables import prefix


def build_catalog(root=ROOT):
    relative = "randomizer/Content/Load/Tres/TresEventData"
    asset = root / "worlds/kh3" / relative
    flags = {}
    for row, properties in Asset.load(asset.with_suffix('.uasset')).rows.items():
        flags[row] = {
            'flag_name': prefix(properties, 'gameflowFlag').value,
            'threshold': prefix(properties, 'intValue').value,
        }
    for row, value in flags.items():
        if set(value) != {"flag_name", "threshold"}:
            raise ValueError(f"Incomplete event row: {row}")
        if row in {"EVENT_CKGAME_001", "EVENT_KEYBLADE_002"}:
            value["requires_tt_clear"] = True
    if not flags:
        raise ValueError("No Event flags extracted")
    # BG0 has no item-prize Event row, but does have a real native clear flag.
    flags["EVENT_REPORT_000"] = {
        "flag_name": "global_BATTLEPORTAL_ex_39_CLEARED", "threshold": -1,
    }
    return {"schema": "kh3ap.event_flags.v1", "source": relative,
               "source_sha256": {ext: hashlib.sha256(asset.with_suffix(ext).read_bytes()).hexdigest()
                                 for ext in (".uasset", ".uexp")}, "flags": flags}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    payload = build_catalog()
    output = ROOT / 'worlds/kh3/data/event_flags.json'
    if args.check:
        if json.loads(output.read_text(encoding='utf-8')) != payload:
            parser.exit(1, 'Event flag catalog is stale\n')
    else:
        output.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(f"{'Verified' if args.check else 'Exported'} {len(payload['flags'])} Event flag mappings")


if __name__ == "__main__":
    main()
