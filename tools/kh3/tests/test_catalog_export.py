"""Exercise standalone exports in fresh processes, outside pytest's world imports."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools/kh3"


def run_python(arguments, cwd, *, scope=None):
    environment = dict(os.environ, SKIP_REQUIREMENTS_UPDATE="1")
    environment.pop("AP_TEST_WORLDS", None)
    environment.pop("AP_WORLD_LOAD_PATHS", None)
    if scope is not None:
        environment["AP_WORLD_LOAD_PATHS"] = scope
    return subprocess.run([sys.executable, *arguments], cwd=cwd, env=environment,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("module,builder,section", [
    ("generate_native_catalog", "chest_data", "chests"),
    ("generate_native_catalog", "catalog_data", "catalog"),
    ("generate_native_catalog", "location_data", "locations"),
    ("generate_randomization_catalog", "catalog_data", "randomization"),
])
@pytest.mark.parametrize("scope", [None, '["caller-owned-setting"]'])
def test_builders_work_first_and_preserve_import_settings(tmp_path, module, builder, section, scope):
    code = '''
import importlib, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
module = importlib.import_module(sys.argv[2])
paths = sys.path[:]
scope = os.environ.get("AP_WORLD_LOAD_PATHS")
build = getattr(module, sys.argv[3])
first = build()
assert sys.path == paths
assert os.environ.get("AP_WORLD_LOAD_PATHS") == scope
from export_dll_inputs import runtime_inputs
assert first == runtime_inputs()[sys.argv[4]] == build()
from catalog_imports import import_world_module
from worlds.kh3 import Data
assert import_world_module("Data") is Data
sources = [m for m in sys.modules.values()
           if getattr(m, "__file__", None) and Path(m.__file__).resolve() == Path(Data.__file__).resolve()]
assert len(sources) == 1, sources
try:
    import_world_module("nonexistent_catalog_test_module")
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("Missing module import unexpectedly succeeded")
assert sys.path == paths
assert os.environ.get("AP_WORLD_LOAD_PATHS") == scope
from worlds import AutoWorldRegister
assert set(AutoWorldRegister.world_types) == {"Archipelago", "Kingdom Hearts III"}
json.dumps(first)  # Every builder returns directly serializable data.
'''
    result = run_python(["-c", code, str(TOOLS), module, builder, section], tmp_path, scope=scope)
    assert result.returncode == 0, result.stdout + result.stderr


def test_export_cli_checks_content_and_preserves_matching_output(tmp_path):
    output = tmp_path / "runtime-inputs.json"
    command = [str(TOOLS / "export_dll_inputs.py"), "--output", str(output)]
    result = run_python(command, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    original = output.read_bytes()
    payload = json.loads(original)
    assert set(payload) == {"schema", "catalog", "chests", "locations", "randomization", "limits", "ui"}
    assert payload["schema"] == "kh3ap.runtime_inputs.v1"
    before = output.stat().st_mtime_ns
    for arguments in (command, [*command, "--check"]):
        result = run_python(arguments, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert output.read_bytes() == original
        assert output.stat().st_mtime_ns == before
    payload["catalog"]["items"] = {}
    output.write_text(json.dumps(payload), encoding="utf-8")
    changed = output.read_bytes()
    result = run_python([*command, "--check"], tmp_path)
    assert result.returncode == 1 and "stale" in result.stderr
    assert output.read_bytes() == changed
