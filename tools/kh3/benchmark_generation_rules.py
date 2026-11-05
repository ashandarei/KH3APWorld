"""Paired KH3 generation timings with placement-equivalence checks.

The control uses the former KH3 CountRules evaluator in memory. Core AP files
are never modified. Both modes use current pool restrictions and identical
seeds. Times exclude imports, game startup and external C# pak generation.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import logging
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]


def original_count(self, state):
    needed = self.count
    if needed <= 0:
        return True
    for child in self.children:
        if child(state):
            needed -= 1
            if needed == 0:
                return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--seed', type=int, default=731)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    sys.path.insert(0, str(REPO))
    os.environ['AP_WORLD_LOAD_PATHS'] = json.dumps([str(REPO / 'worlds/kh3')])
    os.environ['SKIP_REQUIREMENTS_UPDATE'] = '1'
    logging.basicConfig(level=logging.ERROR)
    from Fill import balance_multiworld_progression, distribute_items_restrictive
    from test.general import setup_multiworld
    from worlds.AutoWorld import call_all
    from worlds.kh3 import KH3World
    from worlds.kh3.rule_helpers import CountRules

    optimized_count = CountRules.Resolved._evaluate
    cases = [
        ('defaults', 1, {}),
        ('emblems90_randomize', 1, {'lucky_emblems_pool': 'randomize',
                                   'lucky_emblems_per_check': 1, 'lucky_emblem_limit': 90}),
        ('emblems90_junk', 1, {'lucky_emblems_pool': 'junk',
                              'lucky_emblems_per_check': 1, 'lucky_emblem_limit': 90}),
        ('four_kh3', 4, {'lucky_emblems_pool': 'randomize',
                         'lucky_emblems_per_check': 1, 'lucky_emblem_limit': 90}),
    ]
    report = {'python': sys.version, 'seed': args.seed, 'runs': args.runs,
              'rules_sha256': hashlib.sha256((REPO / 'worlds/kh3/rule_helpers.py').read_bytes()).hexdigest(),
              'cases': []}
    for name, players, options in cases:
        samples = {'control': [], 'optimized': []}
        for repeat in range(args.runs):
            digests = []
            for mode in (('control', 'optimized') if repeat % 2 == 0 else ('optimized', 'control')):
                gc.collect()
                evaluator = original_count if mode == 'control' else optimized_count
                with patch.object(CountRules.Resolved, '_evaluate', evaluator):
                    start = time.perf_counter()
                    mw = setup_multiworld([KH3World] * players, seed=args.seed + repeat, options=options)
                    mw.seed_name = f'KH3Benchmark{args.seed + repeat}'
                    setup_end = time.perf_counter()
                    distribute_items_restrictive(mw)
                    call_all(mw, 'post_fill')
                    if players > 1:
                        balance_multiworld_progression(mw)
                    call_all(mw, 'finalize_multiworld')
                    fill_end = time.perf_counter()
                    assert not mw.get_unfilled_locations()
                    assert mw.can_beat_game()
                    assert mw.fulfills_accessibility()
                    validation_end = time.perf_counter()
                    # Exercise exact seed-container content and encoded slot data.
                    exported = [world.build_seed_container_data() for world in mw.worlds.values()]
                    encoded = json.dumps(exported, sort_keys=True).encode()
                    export_end = time.perf_counter()
                    placements = [(loc.player, loc.name, loc.item.player, loc.item.name)
                                  for loc in mw.get_locations()]
                    digest = hashlib.sha256(json.dumps(placements, sort_keys=True).encode() + encoded).hexdigest()
                digests.append(digest)
                samples[mode].append(dict(setup=setup_end-start, fill=fill_end-setup_end,
                    validation=validation_end-fill_end, export=export_end-validation_end,
                    total=export_end-start, digest=digest, locations=len(placements), export_bytes=len(encoded)))
            if len(set(digests)) != 1:
                raise RuntimeError(f'Control and optimized output differ: {name}, seed {args.seed + repeat}')
        medians = {mode: {phase: statistics.median(row[phase] for row in rows)
                         for phase in ('setup', 'fill', 'validation', 'export', 'total')}
                   for mode, rows in samples.items()}
        result = dict(case=name, players=players, options=options, samples=samples,
                      median_seconds=medians, outputs_identical=True)
        report['cases'].append(result)
        print(json.dumps({key: result[key] for key in ('case', 'median_seconds', 'outputs_identical')}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
