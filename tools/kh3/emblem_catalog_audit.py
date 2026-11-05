"""Join shipped Lucky Mark IDs and map detectors to the emblem catalog.

This produces an audit plan, not proof of successful live photographs.
Inputs are read-only CUE4Parse JSON exports of TresLuckyMarkData and map assets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

WORLD_NAMES = dict(he='Olympus', tt='Twilight Town', ts='Toy Box',
    ra='Kingdom of Corona', mi='Monstropolis', fz='Arendelle',
    ca='The Caribbean', bx='San Fransokyo', po='100 Acre Wood')


def build_manifest(native_rows, catalog, detectors):
    if set(native_rows) != set(map(str, range(1, 91))):
        raise ValueError('Native table must contain exactly IDs 1–90')
    result = []
    for code, world in WORLD_NAMES.items():
        native = sorted(((int(k), row['MarkIndex']) for k, row in native_rows.items()
                         if row['World'] == code), key=lambda pair: pair[1])
        guide = sorted((s for s in catalog if s['world'] == world), key=lambda s: s['world_index'])
        if len(native) != len(guide) or len({index for _, index in native}) != len(native):
            raise ValueError(f'World count/index mismatch: {world}')
        for (native_id, mark_index), spot in zip(native, guide):
            locations = [d for d in detectors if str(d['native_id']) == str(native_id)]
            if not locations:
                raise ValueError(f'No authored detector for native ID {native_id}')
            result.append(dict(native_id=native_id, native_mark_index=mark_index,
                global_id=spot['global_id'], world=world, world_code=code,
                world_index=spot['world_index'], friendly_name=spot['friendly_name'],
                location=spot['location'], asset_detectors=locations,
                identity_matches_current_catalog=native_id == spot['global_id'],
                live_photo_verified=False))
    if len(result) != 90 or len({r['global_id'] for r in result}) != 90:
        raise ValueError('Catalog mapping must be a bijection covering all 90 emblems')
    return sorted(result, key=lambda row: row['native_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-table', type=Path, required=True)
    parser.add_argument('--detectors', type=Path, required=True)
    parser.add_argument('--catalog', type=Path,
        default=Path(__file__).resolve().parents[2]/'worlds/kh3/data/lucky_emblem_spots.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(json.loads(args.native_table.read_text())[0]['Rows'],
        json.loads(args.catalog.read_text())['spots'], json.loads(args.detectors.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(schema='kh3ap.emblem_catalog_audit.v1',
        total=90, mapping_mismatches=sum(not r['identity_matches_current_catalog'] for r in manifest),
        live_photos_verified=0, emblems=manifest), indent=2))
    print(json.dumps(dict(total=90, mismatches=sum(not r['identity_matches_current_catalog'] for r in manifest),
        output=str(args.output.resolve()))))


if __name__ == '__main__':
    main()
