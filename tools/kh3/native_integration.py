"""Opt-in tests against the standalone native checkout; never a player dependency."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from tools.kh3.local_environment import CHECKOUT_ROOT, local_path


def native_project_root() -> Path:
    import pytest

    if os.environ.get("KH3_NATIVE_INTEGRATION") != "1":
        pytest.skip("Native integration requires --native-integration", allow_module_level=True)
    root = local_path("KH3_RUNTIME_DIR", CHECKOUT_ROOT.parent / "KH3Runtime")
    if not (root / "KH3APProbeCpp/CMakeLists.txt").is_file():
        raise FileNotFoundError("Set KH3_RUNTIME_DIR to the standalone DLL checkout")
    return root


def load_native_module(relative: str):
    path = native_project_root() / relative
    name = "kh3_native_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
