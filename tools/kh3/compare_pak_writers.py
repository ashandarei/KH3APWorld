"""Generate paired KH3 pak compatibility cases without changing the game install.

First run generates fresh assets with the packaged Python backend and saved exact
placements. --replay reuses those verified assets via the native output pak,
allowing Linux to exercise the same cases with an independent native pak reader.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import types
import zipfile

REPO = Path(__file__).resolve().parents[2]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def checked(command, log, *, cwd=None, env=None):
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=180)
    log.write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'Exit {result.returncode}: {command[0]}; see {log}')
    return result.stdout


def load_package(archive):
    name = 'kh3_pak_comparison'
    package = types.ModuleType(name)
    package.__path__ = [str(archive) + '/kh3']
    sys.modules[name] = package
    return (importlib.import_module(name + '.Pak'), importlib.import_module(name + '.seedgen.generator'))


def extract_reference(destination):
    bundle = REPO / 'worlds/kh3/tools/repak/v0.2.3'
    if os.name == 'nt':
        archive = bundle / 'repak_cli-x86_64-pc-windows-msvc.zip'
        assert sha(archive.read_bytes()) == '6720d602144d75df477a99d5bedb6ea780997546afc335901d4937cafeaa73fa'
        with zipfile.ZipFile(archive) as zip_file:
            data = zip_file.read(next(name for name in zip_file.namelist() if Path(name).name == 'repak.exe'))
        target = destination / 'repak.exe'
        expected = 'fcd538e5994b9bb833622d425ae346f4e0692f02d4b0025114a559f9b6286022'
    else:
        archive = bundle / 'repak_cli-x86_64-unknown-linux-gnu.tar.xz'
        assert sha(archive.read_bytes()) == '933bdb8e26f34e8fd70ea50201efca39df041de58aa83b1cd6eb83da124a2046'
        with tarfile.open(archive) as tar_file:
            member = next(member for member in tar_file.getmembers() if Path(member.name).name == 'repak')
            data = tar_file.extractfile(member).read()
        target = destination / 'repak'
        expected = 'fce30661c951ce56fd2507a44a1e03637e3ea06a1b7cc8035bd62c0b37dd9457'
    assert sha(data) == expected
    target.write_bytes(data)
    target.chmod(0o755)
    return target


def sources(fixtures_root: Path):
    if not fixtures_root.is_dir():
        raise ValueError(f'Fixture directory does not exist: {fixtures_root}')
    candidates = fixtures_root.rglob('*.randomized-options.json')
    found = []
    seen = set()
    for options in sorted(candidates):
        stem = options.name.removesuffix('.randomized-options.json')
        config = options.with_name(stem + '.json')
        metadata = options.with_name(stem + '.archipelago-seed-metadata.json')
        if not config.is_file() or not metadata.is_file():
            continue
        key = (sha(config.read_bytes()), sha(options.read_bytes()))
        if key not in seen:
            seen.add(key)
            found.append((config, options, metadata))
    if not found:
        raise RuntimeError('No saved KH3 exact-generation fixtures found')
    return found


def file_hashes(root):
    return {path.relative_to(root).as_posix(): sha(path.read_bytes())
            for path in sorted(root.rglob('*')) if path.is_file()}


def compare(pak, native, staging, case, scratch, generated_python=None):
    expected = file_hashes(staging)
    if not expected:
        raise RuntimeError('Empty staging directory')
    python_output = case / 'python.pak'
    native_output = case / 'native.pak'
    start = time.perf_counter()
    pak.pack_directory(staging, python_output)
    python_seconds = time.perf_counter() - start
    if generated_python is not None:
        assert python_output.read_bytes() == generated_python.read_bytes(), 'Seed output differs from direct Python packing'
    start = time.perf_counter()
    checked([native, 'pack', '--version', 'V4', '--compression', 'Zlib', '--mount-point',
             '../../../', staging, native_output], case / 'native-pack.log')
    native_seconds = time.perf_counter() - start
    readers = {name: pak.PakReader(path) for name, path in [('python', python_output), ('native', native_output)]}
    for writer, reader in readers.items():
        assert reader.mount_point == '../../../', f'{writer}: mount point mismatch'
        assert set(reader.entries) == set(expected), f'{writer}: inventory mismatch'
        for name, digest in expected.items():
            assert sha(reader.read(name)) == digest, f'{writer}: Python extraction differs for {name}'
    for writer, path in [('python', python_output), ('native', native_output)]:
        extracted = scratch / f'extracted-{writer}'
        checked([native, 'unpack', '-o', extracted, path], case / f'native-read-{writer}.log')
        assert file_hashes(extracted) == expected, f'{writer}: native extraction differs'
    repeat = scratch / 'repeat.pak'
    pak.pack_directory(staging, repeat)
    assert sha(repeat.read_bytes()) == sha(python_output.read_bytes()), 'Python packing is nondeterministic'
    compressed_entries = [entry for entry in readers['python'].entries.values() if entry.codec]
    return {
        'status': 'passed', 'file_count': len(expected), 'source_bytes': sum((staging / n).stat().st_size for n in expected),
        'source_manifest_sha256': sha(json.dumps(expected, sort_keys=True).encode()),
        'file_sha256': expected,
        'python_bytes': python_output.stat().st_size, 'native_bytes': native_output.stat().st_size,
        'python_sha256': sha(python_output.read_bytes()), 'native_sha256': sha(native_output.read_bytes()),
        'python_pack_seconds': python_seconds, 'native_pack_seconds': native_seconds,
        'both_readers_match_source_for_both_writers': True, 'python_repeat_identical': True,
        'compressed_entries': len(compressed_entries), 'multi_block_entries': sum(len(e.blocks) > 1 for e in compressed_entries),
        'compression_blocks': sum(len(e.blocks) for e in compressed_entries),
    }


@contextmanager
def workspace(output):
    # TemporaryDirectory only removes this freshly-created child of the output.
    with tempfile.TemporaryDirectory(prefix='work-', dir=output) as directory:
        path = Path(directory).resolve()
        assert path.is_relative_to(output.resolve())
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apworld', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', type=int, default=100)
    parser.add_argument('--seed-offset', type=int, default=0)
    parser.add_argument('--fixtures-from', type=Path, help='Use saved input sets that generated successfully in this report')
    parser.add_argument('--replay', type=Path, help='Prior output directory; validates its cases on this OS')
    parser.add_argument('--fixtures-root', type=Path,
                        help='Directory containing saved config/options/metadata sets (searched recursively)')
    args = parser.parse_args()
    if args.replay and args.fixtures_root:
        parser.error('--fixtures-root cannot be combined with --replay')
    if not 1 <= args.cases <= 1000:
        parser.error('--cases must be between 1 and 1000')
    archive, output = args.apworld.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    pak, generator = load_package(archive)
    report = {'apworld': str(archive), 'apworld_sha256': sha(archive.read_bytes()), 'module': pak.__file__,
              'platform': sys.platform, 'python': sys.version, 'requested_cases': args.cases, 'cases': []}
    if args.replay:
        replay = args.replay.resolve()
        prior = json.loads((replay / 'report.json').read_text(encoding='utf-8'))
        assert prior['apworld_sha256'] == report['apworld_sha256']
        fixtures = prior['cases'][:args.cases]
        assert len(fixtures) == args.cases and all(c['status'] == 'passed' for c in fixtures)
    else:
        fixtures_root = (args.fixtures_root or REPO / 'kh3/generated-paks').resolve()
        fixtures = sources(fixtures_root)
        report['fixtures_root'] = str(fixtures_root)
        if args.fixtures_from:
            prior_fixtures = json.loads(args.fixtures_from.read_text(encoding='utf-8'))
            supported = {(c['config_source_sha256'], c['options_source_sha256'])
                         for c in prior_fixtures['cases'] if c['status'] == 'passed'}
            excluded = [str(config) for config, options, metadata in fixtures
                        if (sha(config.read_bytes()), sha(options.read_bytes())) not in supported]
            fixtures = [(config, options, metadata) for config, options, metadata in fixtures
                        if (sha(config.read_bytes()), sha(options.read_bytes())) in supported]
            if not fixtures:
                raise RuntimeError('No validated fixture sources selected')
            report['fixture_screening_report'] = str(args.fixtures_from.resolve())
            report['excluded_fixture_configs'] = excluded
        report['saved_input_sets'] = len(fixtures)
    save(output / 'report.json', report)
    with workspace(output) as shared:
        native = extract_reference(shared)
        if not args.replay:
            template = shared / 'template'
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if member.is_dir() or not member.filename.startswith('kh3/randomizer/'):
                        continue
                    target = template / member.filename.removeprefix('kh3/randomizer/')
                    assert target.resolve().is_relative_to(template.resolve())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.read(member))
        for index in range(args.cases):
            case = output / f'case-{index + 1:03d}'
            case.mkdir()
            row = {'case': index + 1}
            start = time.perf_counter()
            try:
                with workspace(output) as scratch:
                    generated = None
                    if args.replay:
                        baseline = fixtures[index]
                        staging = scratch / 'staging'
                        checked([native, 'unpack', '-o', staging, replay / case.name / 'native.pak'], case / 'replay-source.log')
                        assert file_hashes(staging) == baseline['file_sha256'], 'Replay source differs from saved manifest'
                    else:
                        config_source, options, metadata_source = fixtures[index % len(fixtures)]
                        config = json.loads(config_source.read_text(encoding='utf-8-sig'))
                        metadata = json.loads(metadata_source.read_text(encoding='utf-8-sig'))
                        player = re.sub(r'[^A-Za-z0-9._-]+', '_', metadata['slot_name']).strip('._')
                        seed = f'AP_{99000000000000000000 + args.seed_offset + index + 1}_P{metadata["slot"]}_{player}'
                        config['seed'] = seed
                        metadata['seed'] = seed
                        save(case / 'config.json', config)
                        save(case / 'metadata.json', metadata)
                        row.update({'seed': seed, 'config_source': str(config_source), 'options_source': str(options),
                                    'config_source_sha256': sha(config_source.read_bytes()),
                                    'options_source_sha256': sha(options.read_bytes())})
                        root = scratch / 'randomizer'
                        shutil.copytree(template, root, ignore=shutil.ignore_patterns('bin', 'obj'))
                        generated, _ = generator.generate_seed(root, scratch / 'generated', seed, config,
                            json.loads(options.read_text(encoding='utf-8-sig')), metadata)
                        stages = [p for p in (root / 'wwwroot/pak/Seeds').iterdir() if p.is_dir()]
                        assert len(stages) == 1
                        staging = stages[0]
                    row.update(compare(pak, native, staging, case, scratch, generated))
                    if args.replay:
                        row['cross_platform_assets_identical'] = row['file_sha256'] == baseline['file_sha256']
                        row['cross_platform_python_pak_identical'] = row['python_sha256'] == baseline['python_sha256']
                        assert row['cross_platform_assets_identical']
            except Exception as exc:
                row.update({'status': 'failed', 'error': str(exc), 'traceback': traceback.format_exc()})
            row['elapsed_seconds'] = time.perf_counter() - start
            save(case / 'result.json', row)
            report['cases'].append(row)
            report['passed'] = sum(c['status'] == 'passed' for c in report['cases'])
            report['failed'] = len(report['cases']) - report['passed']
            save(output / 'report.json', report)
            if (index + 1) % 10 == 0 or row['status'] != 'passed':
                print(json.dumps({'completed': index + 1, 'passed': report['passed'], 'failed': report['failed'],
                                  'last_error': row.get('error')}), flush=True)
    passed = [c for c in report['cases'] if c['status'] == 'passed']
    report['summary'] = {'passed': len(passed), 'failed': args.cases - len(passed),
                         'files_compared': sum(c['file_count'] for c in passed),
                         'unique_input_manifests': len({c['source_manifest_sha256'] for c in passed}),
                         'python_bytes': sum(c['python_bytes'] for c in passed),
                         'native_bytes': sum(c['native_bytes'] for c in passed),
                         'multi_block_entries': sum(c['multi_block_entries'] for c in passed),
                         'compression_blocks': sum(c['compression_blocks'] for c in passed)}
    save(output / 'report.json', report)
    print(json.dumps(report['summary'], indent=2), flush=True)
    return int(report['failed'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
