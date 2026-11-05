"""Ingredient identities, source labels and memory-only movie provenance."""
import ast
import json
from pathlib import Path
import unittest

from tools.kh3.ingredient_journal.generate_catalog import build_catalog
from tools.kh3.native_integration import native_project_root, load_native_module

ROOT = Path(__file__).resolve().parents[3]


class IngredientJournalTests(unittest.TestCase):
    def setUp(self):
        data = ROOT / 'worlds/kh3/data'
        self.entries = json.loads((data / 'ingredient_journal.json').read_text())['ingredients']
        self.spots = json.loads((data / 'ingredient_spots.json').read_text())['spots']
        self.sources = json.loads((data / 'ingredient_sources.json').read_text())['ingredients']
        self.reference = json.loads((data / 'minimap_reference.json').read_text())
        tree = ast.parse((ROOT / 'worlds/kh3/Ingredients.py').read_text())
        self.names = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'INGREDIENT_NAMES' for t in n.targets))

    def test_unique_field_ingredients_and_native_icons(self):
        self.assertEqual(len(self.entries), 51)
        self.assertEqual({e['id'] for e in self.entries}, set(self.sources))
        self.assertEqual(len({e['name'] for e in self.entries}), 51)
        for entry in self.entries:
            number = int(entry['id'][8:])
            self.assertEqual(entry['name'], self.names[number - 1])
            self.assertNotIn(number, range(48, 56))
            self.assertRegex(entry['icon'], r'^/Game/UI/Icon/Item/food00[1-8]\.food00[1-8]$')

    def test_sources_are_friendly_deduplicated_and_reproducible(self):
        icons = {e['id']: e['icon'] for e in self.entries}
        self.assertEqual(self.entries, build_catalog(self.spots, self.sources, self.reference, self.names, icons))
        for entry in self.entries:
            places = [(s['world'], s['map']) for s in entry['sources']]
            self.assertTrue(places)
            self.assertEqual(places, sorted(set(places)))
            self.assertTrue(all(w in self.sources[entry['id']] for w, _ in places))
            self.assertTrue(all('/Game/' not in m and '_' not in m for _, m in places))

    def test_native_header_is_reproducible(self):
        renderer = load_native_module('scripts/runtime_ui_catalogs.py')
        generated = renderer.journal_header(self.entries, self.reference)
        header = native_project_root() / 'KH3APProbeCpp/kh3ap/features/journal/ingredient_journal_catalog.h'
        self.assertEqual(generated, header.read_text())

    def test_rejects_missing_sources_and_delimiters(self):
        icons = {e['id']: e['icon'] for e in self.entries}
        with self.assertRaises(ValueError):
            build_catalog([], self.sources, self.reference, self.names, icons)

    def test_native_header_rejects_delimiters(self):
        native_header = load_native_module('scripts/runtime_ui_catalogs.py').journal_header
        entries = [dict(e) for e in self.entries]
        entries[0]['name'] = 'Crab\tInvalid'
        with self.assertRaises(ValueError):
            native_header(entries, self.reference)

    def test_locators_only_reference_authoritative_possible_pickups(self):
        spots = {s['id']: s for s in self.spots}
        keys = set()
        for entry in self.entries:
            for source in entry['sources']:
                self.assertNotIn(source['key'], keys)
                keys.add(source['key'])
                allowed = {actor for spot in self.sources[entry['id']][source['world']]
                           if spot in spots and '_TUTO_' not in spot for actor in spots[spot]['actors']}
                self.assertTrue(source['actors'])
                self.assertEqual(source['actors'], sorted(set(source['actors'])))
                self.assertTrue(set(source['actors']) <= allowed)
                self.assertTrue(all(':PersistentLevel.' in actor for actor in source['actors']))

    def test_local_movie_preserves_game_code_and_only_adds_our_helper(self):
        original = ROOT / 'build/ingredient-journal/original.gfx'
        helper = ROOT / 'build/ingredient-journal/helper.swf'
        if not original.exists() or not helper.exists():
            self.skipTest('Local game/compiler fixtures are not distributed')
        journal = load_native_module('authoring/kh3/ingredient_journal/build_runtime_patch.py')
        data = helper.read_bytes()
        tags = list(journal.extension.abc_tags(data))
        self.assertEqual(set(tags[0][4].classes), {'kh3ap.APIngredientJournal'})
        added = data[tags[0][0]:tags[0][3]]
        result, delta, _ = journal.extension.extend_movie(original.read_bytes(), added,
                                                        {'MobilePortal': 'attach'}, 'APIngredientJournal')
        self.assertEqual(result, (ROOT / 'build/ingredient-journal/extended.gfx').read_bytes())
        # Ingredients, Adversaries, Gate Opener and Chests share this helper,
        # including the chest artwork encoded from the minimap's source PNG.
        self.assertLess(len(delta), 40 * 1024)


if __name__ == '__main__':
    unittest.main()
