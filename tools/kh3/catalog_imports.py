"""Canonical world imports for the standalone catalog export tools."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def import_world_module(name: str) -> ModuleType:
    """Load KH3 normally, scoping first-time AP discovery to this checkout.

    These command-line tools run synchronously. Restore temporary import settings
    even on failure; modules retain their canonical identities in Python's cache.
    """
    previous_paths = sys.path[:]
    previous_scope = os.environ.get("AP_WORLD_LOAD_PATHS")
    try:
        sys.path.insert(0, str(ROOT))
        os.environ["AP_WORLD_LOAD_PATHS"] = json.dumps([str(ROOT / "worlds/kh3")])
        return importlib.import_module(f"worlds.kh3.{name}")
    finally:
        sys.path[:] = previous_paths
        if previous_scope is None:
            os.environ.pop("AP_WORLD_LOAD_PATHS", None)
        else:
            os.environ["AP_WORLD_LOAD_PATHS"] = previous_scope
