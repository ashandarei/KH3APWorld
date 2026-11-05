"""Join verified native journal identities to authored enemy placements."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'worlds/kh3/data'
LANDMARKS = {
    'Metal Troll': ('San Fransokyo', 'First boss encounter'),
    'Sniper': ('Twilight Town', 'First victory bonus fight (NPC)'),
    'Sorcerer': ('Twilight Town', 'Battlegate 3: The Old Mansion'),
}


def build_catalog(snapshot, names, enemies, targets, reference):
    labels = {key[:4].lower(): value['map_name'] for key, value in reference['minimaps'].items() if value['map_name']}
    entries = []
    for entry in snapshot['entries']:
        if entry['hidden_kill_count']:
            continue
        codes = {'e_' + name.split('TRES_ENEMY_UID_')[-1].lower() for name in entry['enemy_names']}
        places = {}
        for world, groups in enemies.items():
            for group, record in groups.items():
                actor_code = re.search(r'/Enemy/(e_[^/]+)/', record['EnemyPath'])[1]
                actor_code = re.sub(r'_s\d.*$', '', actor_code)
                if not any(actor_code == code or re.fullmatch(re.escape(code) + '[a-z]', actor_code) for code in codes):
                    continue
                target = targets['targets'].get(group)
                if not target:
                    raise ValueError(f'Missing authored actor mapping: {group}')
                package = target['package']
                match = re.search(r'/Levels/([a-z]{2})(?:_DLC)?/\1_(\d\d)/', package)
                if not match:
                    raise ValueError(f'Unrecognized enemy map: {package}')
                family = match[1] + match[2]
                if family not in labels:
                    continue
                place = (reference['world_names'][match[1]], labels[family])
                places.setdefault(place, set()).update(package + '.' + actor for actor in target['actors'])
        sources = [dict(world=w, map=m, key=entry['row'] + ':' + str(i), actors=sorted(actors))
                   for i, ((w, m), actors) in enumerate(sorted(places.items()))]
        if names[entry['name_text_id']] in LANDMARKS:
            # User-confirmed landmarks; no verified authored marker sets.
            world, landmark = LANDMARKS[names[entry['name_text_id']]]
            sources = [dict(world=world, map=landmark, key='', actors=[])]
        entries.append(dict(id=entry['row'], name=names[entry['name_text_id']], icon='',
                            enemy_ids=entry['enemy_ids'], sources=sources))
    if len(entries) != 53 or len({e['id'] for e in entries}) != 53:
        raise ValueError('Expected 53 ordinary journal entries')
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--names', type=Path, required=True, help='Local vanilla English kh3_char_name.locres')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'worlds/kh3'))
    from seedgen.localization import Locres
    locres = Locres(args.names.read_bytes())
    names = {namespace + '.' + key: locres.strings[value[1]] for namespace, keys in locres.namespaces.items() for key, value in keys.items()}
    reference = json.loads((DATA / 'minimap_reference.json').read_text())
    root = ROOT / 'worlds/kh3/randomizer/wwwroot'
    entries = build_catalog(json.loads(args.snapshot.read_text()), names,
                            json.loads((root / 'DefaultEnemies.json').read_text()),
                            json.loads((root / 'DefaultEnemyRuntimeTargets.json').read_text()), reference)
    (DATA / 'adversary_journal.json').write_text(json.dumps(dict(schema=1, enemies=entries), indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'entries': len(entries), 'with_maps': sum(any(s['key'] for s in e['sources']) for e in entries),
                      'without_maps': [e['name'] for e in entries if not any(s['key'] for s in e['sources'])]}))


if __name__ == '__main__':
    main()
