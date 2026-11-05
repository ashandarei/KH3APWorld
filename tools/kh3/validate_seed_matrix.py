"""Generate and audit reproducible solo/mixed KH3 placements, without output paks.

Runs core generation, restrictive fill, post-fill, progression balancing and
finalization. Saves settings, compressed placements, logs and machine-readable
audits. Foreign worlds use default options; no SM Map Rando or Doom is loaded.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import faulthandler
import gc
import gzip
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import logging
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
FOREIGN = ['shorthike', 'timespinner', 'v6', 'ror2', 'hk', 'subnautica', 'sm64ex', 'undertale']
PROFILES = ['sample', 'defaults', 'randomize', 'half_junk', 'mostly_junk',
            'all_junk', 'all_junk_strict', 'rank_limits', 'exclusions', 'local_items']


def load_runtime(args):
    """Use the selected checkout's unchanged core, loading only KH3 from here."""
    sys.path.insert(0, str(args.core_root))
    os.chdir(args.core_root)
    if args.core_root == REPO:
        os.environ['AP_WORLD_LOAD_PATHS'] = json.dumps([str(REPO / 'worlds' / name) for name in ['kh3', *FOREIGN]])
        import worlds.kh3
    else:
        # Override only KH3's package source before the normal upstream loader.
        # Existing external KH3 copies must not hide the source being tested.
        os.environ.pop('AP_WORLD_LOAD_PATHS', None)
        class KH3SourceFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'worlds.kh3':
                    return importlib.util.spec_from_file_location(
                        fullname, REPO / 'worlds/kh3/__init__.py',
                        submodule_search_locations=[str(REPO / 'worlds/kh3')])
                return None
        sys.meta_path.insert(0, KH3SourceFinder())
        import worlds
        module = importlib.import_module('worlds.kh3')
        worlds.network_data_package['games'][module.KH3World.game] = module.KH3World.get_data_package_data()
    expected = {
        'Fill': args.core_root / 'Fill.py',
        'BaseClasses': args.core_root / 'BaseClasses.py',
        'Options': args.core_root / 'Options.py',
        'worlds.AutoWorld': args.core_root / 'worlds/AutoWorld.py',
        'worlds.generic.Rules': args.core_root / 'worlds/generic/Rules.py',
        'test.general': args.core_root / 'test/general/__init__.py',
        'worlds.kh3': REPO / 'worlds/kh3/__init__.py',
        **{f'worlds.{name}': args.core_root / f'worlds/{name}/__init__.py' for name in FOREIGN},
    }
    loaded = {}
    for name, path in expected.items():
        module = importlib.import_module(name)
        actual = Path(module.__file__).resolve()
        if actual != path.resolve():
            raise RuntimeError(f'Wrong module loaded for {name}: {actual}, expected {path}')
        loaded[name] = dict(path=str(actual), sha256=hashlib.sha256(actual.read_bytes()).hexdigest())
    (args.output / f'worker-{args.worker}-runtime.json').write_text(json.dumps(loaded, indent=2), encoding='utf-8')


def run_case(index, args, sample):
    from BaseClasses import LocationProgressType, PlandoOptions
    from Fill import balance_multiworld_progression, distribute_items_restrictive
    from test.general import gen_steps, setup_multiworld
    from worlds.AutoWorld import AutoWorldRegister, call_all
    from worlds.generic.Rules import exclusion_rules, locality_rules
    from worlds.kh3 import KH3World
    from worlds.kh3.Data import uses_strict_junk_setting
    from worlds.kh3.Locations import KH3Location
    from worlds.kh3.Options import KH3LocationPoolMode, KH3Options
    from worlds.kh3.Rules import is_junk_item

    seed = args.seed + index
    rng = random.Random(seed)
    profile = PROFILES[(index // 2) % len(PROFILES)]
    foreign = rng.sample(FOREIGN, 3) if index % 2 else []
    types = {w.__module__.split('.')[1]: w for w in AutoWorldRegister.world_types.values()
             if w.__module__.split('.')[1] in FOREIGN}
    options = deepcopy(sample) if profile != 'defaults' else {}
    pools = [name for name, kind in KH3Options.type_hints.items() if issubclass(kind, KH3LocationPoolMode)]
    if profile in ('randomize', 'half_junk', 'mostly_junk', 'all_junk', 'all_junk_strict'):
        probability = {'randomize': 0, 'half_junk': .5, 'mostly_junk': .9,
                       'all_junk': 1, 'all_junk_strict': 1}[profile]
        options.update({name: 'junk' if rng.random() < probability else 'randomize' for name in pools})
    if profile in ('all_junk', 'all_junk_strict', 'half_junk', 'mostly_junk'):
        for name in ('junk_classic_kingdom_reward', 'junk_golden_herc_rewards'):
            options[name] = profile != 'all_junk'
    if profile == 'rank_limits':
        options.update({name: rng.choice(['junk', 'd', 'c', 'b']) for name in KH3Options.type_hints
                        if name.endswith('_max_rank')})
        options['level_up_limit'] = rng.choice([1, 10, 20, 50, 99])
        options['ingredients'] = rng.choice(['ingredientsanity', 'ingredientsanity_plus'])
    result = dict(index=index, seed=seed, profile=profile, players=1 + len(foreign), foreign=foreign,
                  kh3_all_local=args.kh3_all_local)
    case = args.output / f'{index:04d}-{seed}'
    started = time.perf_counter()
    handler = logging.FileHandler(case.with_suffix('.log'), encoding='utf-8')
    logging.getLogger().addHandler(handler)
    mw = None
    try:
        faulthandler.dump_traceback_later(args.timeout, exit=True)
        mw = setup_multiworld([KH3World] + [types[name] for name in foreign], steps=(), seed=seed,
                              options=[options] + [{} for _ in foreign])
        for step in gen_steps[:-1]:
            call_all(mw, step)
        if profile == 'local_items':
            mw.worlds[1].options.local_items.value = {'Magic: Fire', 'Magic: Blizzard', 'Magic: Thunder'}
        if args.kh3_all_local:
            local = KH3Options.type_hints['local_items'].from_any(['Everything'])
            local.verify(KH3World, 'KH3 matrix', PlandoOptions(0))
            mw.worlds[1].options.local_items = local
        if profile == 'exclusions':
            candidates = [loc for loc in mw.get_unfilled_locations(1) if not loc.filler_only
                          and not loc.name.startswith('Level Ups - ')
                          and loc.item_rule(mw.worlds[1].create_item('Magic: Fire'))]
            chosen = rng.sample(candidates, min(20, len(candidates)))
            mw.worlds[1].options.exclude_locations.value = {loc.name for loc in chosen[:15]}
            mw.worlds[1].options.priority_locations.value = {loc.name for loc in chosen[15:]}
        settings = [{name: sorted(option.value) if isinstance(option.value, set) else option.value
                     for name in world.options_dataclass.type_hints
                     for option in [getattr(world.options, name)]} for world in mw.worlds.values()]
        case.with_suffix('.input.json').write_text(json.dumps({**result, 'options': settings}, indent=2), encoding='utf-8')
        locality_rules(mw)
        for player, world in mw.worlds.items():
            exclusion_rules(mw, player, world.options.exclude_locations.value)
            for name in world.options.priority_locations.value:
                mw.get_location(name, player).progress_type = LocationProgressType.PRIORITY
        call_all(mw, 'pre_fill')
        result['setup_seconds'] = time.perf_counter() - started

        tick = time.perf_counter()
        distribute_items_restrictive(mw)
        call_all(mw, 'post_fill')
        result['fill_seconds'] = time.perf_counter() - tick
        before_balance = sum(bool(loc.filler_only and loc.item.advancement)
                             for loc in mw.get_locations(1) if isinstance(loc, KH3Location) and loc.item)
        tick = time.perf_counter()
        if foreign:
            balance_multiworld_progression(mw)
        result['balance_seconds'] = time.perf_counter() - tick
        result['junk_after_balancing'] = sum(bool(loc.filler_only and loc.item.advancement)
                                            for loc in mw.get_locations(1) if loc.item)
        call_all(mw, 'finalize_multiworld')
        call_all(mw, 'pre_output')
        locations = mw.get_locations()
        assert not mw.get_unfilled_locations(), 'Unfilled checks'
        assert len({id(loc.item) for loc in locations}) == len(locations), 'Duplicate item object'
        assert all(loc.item.location is loc for loc in locations), 'Broken item/location links'
        # Locked events/vanilla placements may intentionally bypass ordinary item rules.
        state = mw.get_all_state(False)
        for loc in locations:
            if not loc.locked and not loc.is_event:
                assert loc.can_fill(state, loc.item, check_access=False), f'Illegal placement: {loc}: {loc.item}'
            if loc.item.player == 1 and loc.item.name in mw.worlds[1].options.local_items.value:
                assert loc.player == 1, f'Local KH3 item exported: {loc}: {loc.item}'
        world = mw.worlds[1]
        strict_count = 0
        for row in world.active_locations:
            loc = mw.get_location(row['name'], 1)
            strict_filler = loc.filler_only
            if strict_filler:
                strict_count += 1
                assert is_junk_item(loc.item), f'Strict filler violation: {loc}: {loc.item}'
            if uses_strict_junk_setting(row, world.options):
                strict_count += 1
                assert not loc.item.advancement, f'Strict progression exclusion: {loc}'
        result['strict_checks'] = strict_count
        result['beatable'] = mw.can_beat_game()
        result['accessible'] = mw.fulfills_accessibility()
        assert result['beatable'] and result['accessible'], 'Beatability/accessibility failed'
        result['final_junk_progression'] = sum(bool(loc.filler_only and loc.item.advancement)
                                              for loc in locations if isinstance(loc, KH3Location))
        result['junk_before_balancing'] = before_balance
        result['junk_increase_after_post_fill'] = max(0, result['final_junk_progression'] - before_balance)
        result['locations'] = len(locations)
        result['starting_overflow'] = getattr(mw, '_kh3_starting_overflow_report', {})
        assert result['final_junk_progression'] == 0, 'Progression in junk'
        assert all(is_junk_item(loc.item) for loc in locations
                   if isinstance(loc, KH3Location) and loc.filler_only and loc.address is not None), 'Useful item in junk'
        result['status'] = 'passed'
    except Exception:
        result['status'] = 'failed'
        result['error'] = traceback.format_exc()
        logging.exception('Case failed')
    finally:
        faulthandler.cancel_dump_traceback_later()
        if mw is not None:
            with gzip.open(case.with_suffix('.placements.json.gz'), 'wt', encoding='utf-8') as stream:
                json.dump([dict(player=loc.player, location=loc.name, locked=loc.locked,
                                progress_type=loc.progress_type.name, junk=getattr(loc, 'filler_only', False),
                                item=loc.item.name if loc.item else None,
                                recipient=loc.item.player if loc.item else None,
                                classification=int(loc.item.classification) if loc.item else None)
                           for loc in mw.get_locations()], stream)
        result['seconds'] = time.perf_counter() - started
        case.with_suffix('.result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        logging.getLogger().removeHandler(handler)
        handler.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yaml', type=Path, required=True)
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=927000)
    parser.add_argument('--core-root', type=Path, default=REPO,
                        help='Archipelago checkout supplying core and foreign worlds; KH3 comes from this repo.')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--kh3-all-local', action='store_true',
                        help='Apply local_items: [Everything] to KH3 in every profile.')
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', type=int, default=-1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if min(args.count, args.workers, args.timeout) < 1:
        parser.error('count, workers and timeout must be positive')
    args.output = args.output.resolve()
    args.core_root = args.core_root.resolve()
    args.yaml = args.yaml.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker < 0 and any(args.output.glob('*.result.json')):
        parser.error('Use a fresh output directory so old cases cannot contaminate the audit')
    if args.worker >= 0:
        load_runtime(args)
        import yaml
        sample = yaml.safe_load(args.yaml.read_text(encoding='utf-8'))['Kingdom Hearts III']
        for unsupported in ('plando_items', 'item_links', 'start_inventory', 'start_inventory_from_pool'):
            if sample.get(unsupported):
                raise ValueError(f'Nonempty {unsupported} is not supported by this focused matrix')
        logging.basicConfig(level=logging.WARNING)
        for index in range(args.worker, args.count, args.workers):
            result = run_case(index, args, sample)
            print(json.dumps({key: result[key] for key in ('index', 'profile', 'status', 'seconds')}), flush=True)
            gc.collect()
        return
    paths = [Path(__file__), *[args.core_root / name for name in
                             ('Fill.py', 'Main.py', 'BaseClasses.py', 'Options.py', 'worlds/AutoWorld.py',
                              'worlds/generic/Rules.py', 'worlds/__init__.py', 'Utils.py')],
             *sorted((REPO / 'worlds/kh3').glob('*.py')), args.yaml]
    manifest = dict(count=args.count, base_seed=args.seed, workers=args.workers, profiles=PROFILES,
                    kh3_all_local=args.kh3_all_local,
                    core_root=str(args.core_root), kh3_source=str(REPO / 'worlds/kh3'),
                    foreign=FOREIGN, python=sys.version,
                    sha256={str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    processes = []
    started = time.perf_counter()
    try:
        for worker in range(args.workers):
            log = (args.output / f'worker-{worker}.log').open('w', encoding='utf-8')
            process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()),
                                        *sys.argv[1:], '--worker', str(worker)], cwd=REPO,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'SKIP_REQUIREMENTS_UPDATE': '1', 'PYTHONIOENCODING': 'utf-8'})
            processes.append((process, log))
        previous = -1
        while any(process.poll() is None for process, _ in processes):
            completed = len(list(args.output.glob('*.result.json')))
            if completed // 20 != previous:
                previous = completed // 20
                print(f'{completed}/{args.count} complete ({time.perf_counter() - started:.1f}s)', flush=True)
            time.sleep(2)
    finally:
        for process, log in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
            log.close()
    results = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(args.output.glob('*.result.json'))]
    summary = dict(requested=args.count, completed=len(results), status=dict(Counter(r['status'] for r in results)),
                   profiles=dict(Counter(r['profile'] for r in results)),
                   players=dict(Counter(r['players'] for r in results)),
                   seconds=time.perf_counter() - started,
                   worker_exit_codes=[p.returncode for p, _ in processes],
                   review_indices=[r['index'] for r in results if r['status'] != 'passed'],
                   starting_grants=sum(sum(a['pool_overflow_granted'] for a in r.get('starting_overflow', {}).values())
                                       for r in results),
                   junk_violation_seeds=sum(r.get('final_junk_progression', 0) > 0 for r in results))
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)
    if len(results) != args.count or any(r['status'] != 'passed' for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
