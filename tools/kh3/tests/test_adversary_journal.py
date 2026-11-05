"""Adversary identities and authored spawn-source provenance."""
import json
from pathlib import Path
import re
import unittest

from tools.kh3.ingredient_journal.generate_adversaries import LANDMARKS
from tools.kh3.native_integration import native_project_root, load_native_module

ROOT = Path(__file__).resolve().parents[3]


class AdversaryJournalTests(unittest.TestCase):
    def setUp(self):
        self.entries = json.loads((ROOT / 'worlds/kh3/data/adversary_journal.json').read_text())['enemies']
        self.reference = json.loads((ROOT / 'worlds/kh3/data/minimap_reference.json').read_text())

    def test_unique_native_identities_include_unkilled_entries(self):
        self.assertEqual(len(self.entries), 53)
        self.assertEqual(len({e['id'] for e in self.entries}), 53)
        ids = [i for e in self.entries for i in e['enemy_ids']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(0 < i <= 256 for i in ids))
        self.assertEqual(next(e for e in self.entries if e['id'] == 'HEARTLESS_06')['enemy_ids'], [4, 38])
        self.assertEqual(next(e for e in self.entries if e['id'] == 'HEARTLESS_48')['enemy_ids'], [43, 61])
        self.assertTrue(all('kills' not in e and 'defeated' not in e for e in self.entries))

    def test_map_markers_reference_only_authored_enemy_entities(self):
        targets = json.loads((ROOT / 'worlds/kh3/randomizer/wwwroot/DefaultEnemyRuntimeTargets.json').read_text())['targets']
        allowed = {t['package'] + '.' + actor for t in targets.values() for actor in t['actors']}
        keys = []
        for entry in self.entries:
            places = [(s['world'], s['map']) for s in entry['sources']]
            self.assertEqual(places, sorted(set(places)))
            for source in entry['sources']:
                if not source['key']:
                    self.assertIn(entry['name'], LANDMARKS)
                    world, landmark = LANDMARKS[entry['name']]
                    self.assertEqual(source, dict(world=world, map=landmark, key='', actors=[]))
                    continue
                keys.append(source['key'])
                self.assertTrue(source['actors'])
                self.assertTrue(set(source['actors']) <= allowed)
                self.assertEqual(source['actors'], sorted(set(source['actors'])))
                self.assertNotIn('/Game/', source['map'])
        self.assertEqual(len(keys), len(set(keys)))

    def test_reproducible_header_and_bounded_wide_literals(self):
        renderer = load_native_module("scripts/runtime_ui_catalogs.py")
        generated = renderer.adversary_header(self.entries, self.reference)
        self.assertEqual(generated, (native_project_root() / 'KH3APProbeCpp/kh3ap/features/journal/adversary_journal_catalog.h').read_text())
        self.assertTrue(all(len(match) <= 4000 for match in re.findall(r'LR"kh3\((.*?)\)kh3"', generated, re.S)))


if __name__ == '__main__':
    unittest.main()
