"""Generate the 51 unique field ingredients and deduplicated friendly sources.

Icons are references into the player's TresItemData, never copied textures.
Map labels come from the reviewed tracker reference, not guessed actor names.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'worlds/kh3/data'


def build_catalog(spots, sources, reference, names, icons):
    spots = {spot['id']: spot for spot in spots}
    maps = {}
    for key, value in reference['minimaps'].items():
        if value['map_name']:
            maps[key[:4].lower()] = value['map_name']
    result = []
    for token, worlds in sorted(sources.items()):
        number = int(token.removeprefix('FST_ITEM'))
        if not (1 <= number <= 59 and not 48 <= number <= 55):
            raise ValueError(f'Not a field ingredient: {token}')
        places = {}
        for world, keys in worlds.items():
            for key in keys:
                # One-time tutorial actors are excluded from the authored catalog.
                if '_TUTO_' in key or key not in spots:
                    continue
                spot = spots[key]
                if spot['world'] != world:
                    raise ValueError(f'World mismatch for {key}')
                for actor in spot['actors']:
                    match = re.search(r'/Levels/([a-z]{2})/\1_(\d\d)/', actor)
                    if not match:
                        raise ValueError(f'Unrecognized ingredient map: {actor}')
                    label = maps[match[1] + match[2]]
                    places.setdefault((world, label), set()).add(actor)
        if not places or not icons[token].startswith('/Game/UI/Icon/Item/'):
            raise ValueError(f'Missing source or native icon for {token}')
        result.append(dict(id=token, name=names[number - 1], icon=icons[token],
                           sources=[dict(world=w, map=m, key=token + ':' + str(i),
                                         actors=sorted(places[w, m]))
                                    for i, (w, m) in enumerate(sorted(places))]))
    if len(result) != 51 or len({entry['name'] for entry in result}) != 51:
        raise ValueError('Expected exactly 51 unique field ingredients')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--items', type=Path, required=True, help='Local vanilla TresItemData.uasset')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'worlds/kh3/seedgen'))
    from assets import Asset, get
    tree = ast.parse((ROOT / 'worlds/kh3/Ingredients.py').read_text())
    names = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'INGREDIENT_NAMES'
                                                        for t in node.targets))
    asset = Asset.load(args.items)
    icons = {get(row, 'm_KeyName').value: get(row, 'm_IconAsset').value for row in asset.rows.values()
             if get(row, 'm_KeyName').value.startswith('FST_ITEM')}
    entries = build_catalog(json.loads((DATA / 'ingredient_spots.json').read_text())['spots'],
                            json.loads((DATA / 'ingredient_sources.json').read_text())['ingredients'],
                            json.loads((DATA / 'minimap_reference.json').read_text()), names, icons)
    (DATA / 'ingredient_journal.json').write_text(json.dumps(dict(schema=1, ingredients=entries), indent=2) + '\n', encoding='utf-8')
    print(f'Generated {len(entries)} unique ingredients with friendly world/map sources')


if __name__ == '__main__':
    main()
