"""Time KH3 fill hooks with real KH3 options and real or synthetic foreign slots.

Hook mode isolates KH3; full-fill includes early fill, progression balancing,
finalization and accessibility validation. Neither mode generates output files.
Each case runs in a disposable process with a timeout and live log.
No game, AP server, output pak, or installed APWorld is required or changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import cProfile
import faulthandler
import hashlib
import json
import logging
import os
from pathlib import Path
import pstats
import subprocess
import sys
import time
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]


def worker(args):
    logging.basicConfig(level=logging.INFO)
    from validate_seed_matrix import load_runtime
    load_runtime(SimpleNamespace(core_root=args.core_root, output=args.output.parent,
                                 worker=args.output.stem))
    import yaml
    from BaseClasses import Item, ItemClassification, Location, Region
    from Fill import balance_multiworld_progression, distribute_items_restrictive
    from test.general import TestWorld, gen_steps, setup_multiworld
    from worlds.AutoWorld import call_all
    from worlds.generic.Rules import locality_rules
    from worlds.kh3 import KH3World
    from worlds.AutoWorld import AutoWorldRegister

    timings = {}

    def timed(name, function, *values):
        print('PHASE_START ' + name, flush=True)
        tick = time.perf_counter()
        try:
            return function(*values)
        finally:
            timings[name] = timings.get(name, 0) + time.perf_counter() - tick
            print('TIMING ' + json.dumps(timings), flush=True)

    # Observe shared KH3 hooks without changing their inputs or ordering.
    for name in ('stage_pre_fill',):
        original = getattr(KH3World, name)
        def observed(cls, *values, _name=name, _original=original):
            return timed('kh3.' + _name, _original, *values)
        setattr(KH3World, name, classmethod(observed))
    import Fill
    original_early = Fill.distribute_early_items
    def observed_early(*values):
        return timed('core.early_items', original_early, *values)
    Fill.distribute_early_items = observed_early

    foreign_types = [world for world in AutoWorldRegister.world_types.values()
                     if world.__module__.split('.')[1] in args.foreign_worlds] or [TestWorld]
    missing = set(args.foreign_worlds) - {world.__module__.split('.')[1] for world in foreign_types}
    if missing:
        raise RuntimeError(f'Failed to load requested foreign worlds: {sorted(missing)}')

    raw = args.yaml.read_bytes()
    options = yaml.safe_load(raw)['Kingdom Hearts III']
    if args.kh3_all_junk:
        from worlds.kh3.Options import KH3LocationPoolMode, KH3Options, OlympusColiseumPool
        options.update({name: 'junk' for name, option in KH3Options.type_hints.items()
                        if issubclass(option, KH3LocationPoolMode) and option is not OlympusColiseumPool})
    unknown = sorted(set(options) - set(KH3World.options_dataclass.type_hints))
    if unknown:
        raise ValueError(f'Options not recognized by this KH3 version: {unknown}')
    for name in ('plando_items', 'item_links', 'start_inventory', 'start_inventory_from_pool'):
        if options.get(name):
            raise ValueError(f'This focused harness does not implement nonempty {name}')
    started = time.perf_counter()
    mw = setup_multiworld(
        [KH3World] * args.kh3_players + [foreign_types[i % len(foreign_types)]
                                      for i in range(args.players[0] - args.kh3_players)],
        steps=(), seed=args.seed,
        options=[options] * args.kh3_players + [{}] * (args.players[0] - args.kh3_players))
    for step in gen_steps[:-1]:
        call_all(mw, step)
    for player in range(args.kh3_players + 1, args.players[0] + 1):
        local = args.foreign_rule == 'local' and (player - args.kh3_players - 1) % args.local_every == 0
        if args.foreign_worlds:
            if local:
                mw.worlds[player].options.local_items.value = set(mw.worlds[player].item_name_to_id)
            continue
        menu = Region('Menu', player, mw)
        mw.regions.append(menu)
        mw.completion_condition[player] = lambda state: True
        local_rule = lambda item, player=player: item.player == player
        for index in range(args.foreign_locations):
            loc = Location(player, f'Synthetic check {index}', index + 1, menu)
            if local:
                loc.item_rule = local_rule
            menu.locations.append(loc)
            kind = (ItemClassification.progression if index % 10 < 3 else
                    ItemClassification.useful if index % 10 < 5 else ItemClassification.filler)
            mw.itempool.append(Item(f'Synthetic item {index}', kind, index + 1, player))
    locality_rules(mw)
    from worlds.generic.Rules import exclusion_rules
    from BaseClasses import LocationProgressType
    for player in mw.player_ids:
        world = mw.worlds[player]
        exclusion_rules(mw, player, world.options.exclude_locations.value)
        for name in world.options.priority_locations.value:
            mw.get_location(name, player).progress_type = LocationProgressType.PRIORITY
    call_all(mw, 'pre_fill')
    result = dict(players=args.players[0], kh3_players=args.kh3_players,
                  foreign_locations=args.foreign_locations, foreign_rule=args.foreign_rule,
                  seed=args.seed, yaml_sha256=hashlib.sha256(raw).hexdigest(),
                  items=len(mw.itempool), locations=len(mw.get_unfilled_locations()),
                  setup_seconds=time.perf_counter() - started, mode=args.mode)
    result['foreign_worlds'] = args.foreign_worlds
    result['kh3_all_junk'] = args.kh3_all_junk
    result['local_every'] = args.local_every
    result['games'] = dict(Counter(mw.game.values()))
    result['python'] = sys.version
    result['kh3_version'] = json.loads((REPO / 'worlds/kh3/archipelago.json').read_text())['world_version']
    result['overflow_source_sha256'] = hashlib.sha256((REPO / 'worlds/kh3/StartingInventory.py').read_bytes()).hexdigest()
    result['profiled'] = args.profile
    result['stack_interval'] = args.stack_interval
    result['core_root'] = str(args.core_root)
    result['timings'] = timings
    result['initial_precollected'] = sum(len(items) for items in mw.precollected_items.values())
    print('SETUP ' + json.dumps(result), flush=True)

    def run():
        if args.mode == 'full-fill':
            timed('core.restrictive_fill', distribute_items_restrictive, mw)
            timed('core.post_fill', call_all, mw, 'post_fill')
            if mw.players > 1:
                timed('core.progression_balancing', balance_multiworld_progression, mw)
            timed('core.finalize', call_all, mw, 'finalize_multiworld')
            result['junk_non_filler'] = sum(bool(getattr(loc, 'filler_only', False) and loc.item
                and (loc.item.advancement or loc.item.useful)) for loc in mw.get_locations())
            if result['junk_non_filler']:
                raise RuntimeError('Non-filler item in junk')
            result['unfilled'] = len(mw.get_unfilled_locations())
            result['beatable'] = timed('validation.beatable', mw.can_beat_game)
            result['accessibility'] = timed('validation.accessibility', mw.fulfills_accessibility)
            if result['unfilled'] or not result['beatable'] or not result['accessibility']:
                raise RuntimeError(f'Full fill validation failed: {result}')
        else:
            result['stage_pre_fill_seconds'] = timings.get('kh3.stage_pre_fill', 0)
        result['starting_overflow'] = getattr(mw, '_kh3_starting_overflow_report', {})
        result['precollected'] = sum(len(items) for items in mw.precollected_items.values())
        result['capacity_grants'] = result['precollected'] - result['initial_precollected']

    if args.stack_interval:
        faulthandler.dump_traceback_later(min(args.stack_interval, args.timeout / 2), repeat=True)
    tick = time.perf_counter()
    profiler = cProfile.Profile() if args.profile else None
    try:
        if profiler:
            profiler.runcall(run)
        else:
            run()
    finally:
        faulthandler.cancel_dump_traceback_later()
        if profiler:
            profiler.dump_stats(str(args.output.with_suffix('.prof')))
            with args.output.with_suffix('.profile.txt').open('w', encoding='utf-8') as stream:
                pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(35)
    result['elapsed_seconds'] = time.perf_counter() - tick
    print('RESULT ' + json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yaml', type=Path, help='A concrete KH3 player YAML.')
    parser.add_argument('--core-root', type=Path, default=REPO,
                        help='Archipelago checkout to use; KH3 always loads from this repository.')
    parser.add_argument('--players', type=int, nargs='+', default=[1, 100, 500, 1700])
    parser.add_argument('--kh3-players', type=int, default=1)
    parser.add_argument('--kh3-all-junk', action='store_true',
                        help='Override KH3 location-pool modes to Junk for an overflow stress case.')
    parser.add_argument('--foreign-locations', type=int, default=250)
    parser.add_argument('--foreign-rule', choices=['open', 'local'], default='open')
    parser.add_argument('--local-every', type=int, default=1,
                        help='Apply the local stress constraint to every Nth foreign slot, starting with the first.')
    parser.add_argument('--foreign-worlds', nargs='+', default=[],
                        choices=['shorthike', 'timespinner', 'v6', 'ror2', 'hk', 'subnautica', 'sm64ex', 'undertale'],
                        help='Cycle real game worlds with default options instead of synthetic slots.')
    parser.add_argument('--mode', choices=['hook', 'full-fill'], default='hook')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--stack-interval', type=float, default=15,
                        help='Seconds between diagnostic stack samples; 0 disables sampling.')
    parser.add_argument('--seed', type=int, default=731)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--output', type=Path, default=REPO / 'build/kh3-fill-benchmark/results.json')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.core_root = args.core_root.resolve()
    if not args.yaml:
        parser.error('--yaml is required')
    if (args.kh3_players < 1 or min(args.players) < args.kh3_players or args.foreign_locations < 1
            or args.timeout <= 0 or args.local_every < 1 or args.stack_interval < 0):
        parser.error('Require players >= kh3-players >= 1, foreign-locations/local-every >= 1, and timeout > 0')
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return
    results = []
    for players in args.players:
        case = args.output.with_name(f'{args.output.stem}-{players}')
        command = [sys.executable, '-u', str(Path(__file__).resolve()), '--worker',
                   '--players', str(players),
                   '--kh3-players', str(args.kh3_players), '--foreign-locations', str(args.foreign_locations),
                   '--foreign-rule', args.foreign_rule, '--mode', args.mode,
                   '--local-every', str(args.local_every),
                   '--timeout', str(args.timeout), '--seed', str(args.seed), '--output', str(case)]
        command += ['--core-root', str(args.core_root)]
        command += ['--stack-interval', str(args.stack_interval)]
        if args.yaml:
            command += ['--yaml', str(args.yaml.resolve())]
        if args.kh3_all_junk:
            command.append('--kh3-all-junk')
        if args.profile:
            command.append('--profile')
        if args.foreign_worlds:
            command += ['--foreign-worlds', *args.foreign_worlds]
        print(f'Running {players} players ({args.kh3_players} KH3), timeout {args.timeout}s', flush=True)
        tick = time.perf_counter()
        with case.with_suffix('.log').open('w', encoding='utf-8') as log_stream, subprocess.Popen(
                              command, cwd=REPO, stdout=log_stream, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              env={**os.environ, 'SKIP_REQUIREMENTS_UPDATE': '1', 'PYTHONIOENCODING': 'utf-8'}) as process:
            try:
                process.communicate(timeout=args.timeout)
                status = 'ok' if process.returncode == 0 else 'error'
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                status = 'timeout'
            except BaseException:
                process.kill()
                process.communicate()
                raise
        log = case.with_suffix('.log').read_text(encoding='utf-8', errors='replace')
        result = {}
        worker_reported_completion = False
        for line in log.splitlines():
            if line.startswith('PHASE_START '):
                result['last_started_phase'] = line.split(' ', 1)[1]
            if line.startswith('TIMING '):
                result['timings'] = json.loads(line.split(' ', 1)[1])
            if line.startswith(('SETUP ', 'HOOK_START ', 'RESULT ')):
                result.update(json.loads(line.split(' ', 1)[1]))
                worker_reported_completion |= line.startswith('RESULT ')
        result.update(status=status, wall_seconds=time.perf_counter() - tick, log=str(case.with_suffix('.log')))
        result['returncode'] = process.returncode
        result['worker_reported_completion'] = worker_reported_completion
        if status == 'timeout' and not worker_reported_completion and 'hook_started_at' in result:
            result['hook_seconds_before_timeout'] = time.time() - result['hook_started_at']
        results.append(result)
        args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        if status == 'error':
            print(log, flush=True)
    return int(any(result['status'] != 'ok' for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
