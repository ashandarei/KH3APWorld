"""Developer CLI for the same Python generator used inside the KH3 client."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .coliseum import build_compatibility
from .generator import generate_seed


def main():
    parser = argparse.ArgumentParser(description='KH3 Python randomizer')
    commands = parser.add_subparsers(dest='command', required=True)
    exact = commands.add_parser('exact', help='Generate a seed pak from exported AP rewards')
    for name in ('config', 'randomized-options', 'archipelago-metadata', 'randomizer-root', 'output'):
        exact.add_argument('--' + name, type=Path, required=True)
    exact.add_argument('--seed', required=True)
    compat = commands.add_parser('olympus-coliseum-compat', help='Build the supported Coliseum AP companion')
    compat.add_argument('--source-pak', type=Path, required=True)
    compat.add_argument('--output-pak', type=Path, required=True)
    compat.add_argument('--preserve-cup-rewards', action='store_true')
    args = parser.parse_args()
    if args.command == 'exact':
        def read(path):
            return json.loads(path.read_text(encoding='utf-8-sig'))
        pak, spoiler = generate_seed(args.randomizer_root, args.output, args.seed, read(args.config),
                                    read(args.randomized_options), read(args.archipelago_metadata))
        print(json.dumps(dict(pak=str(pak), spoiler_log=str(spoiler) if spoiler else None)))
    else:
        pak = build_compatibility(args.source_pak, args.output_pak, suppress_cup_rewards=not args.preserve_cup_rewards)
        print(json.dumps(dict(pak=str(pak))))


if __name__ == '__main__':
    main()
