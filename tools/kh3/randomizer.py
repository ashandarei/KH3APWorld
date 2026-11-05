"""Run the KH3 Python generator without importing the Archipelago world registry."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys


def main():
    repo_root = Path(__file__).resolve().parents[2]
    # The worker runs from the randomizer asset directory. Slot-data encoding
    # imports shared AP modules, so resolve them independently of cwd/PYTHONPATH.
    sys.path.insert(0, str(repo_root))
    root = repo_root / 'worlds' / 'kh3'
    # Keep resource-reader metadata without executing the world's __init__,
    # which would import the entire Archipelago world registry.
    spec = importlib.util.spec_from_file_location(
        'kh3_randomizer_tools', root / '__init__.py', submodule_search_locations=[str(root)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package.__name__] = package
    importlib.import_module(package.__name__ + '.seedgen.__main__').main()


if __name__ == '__main__':
    main()
