"""Development-only adapter to the same C++ generator compiled into the DLL.

Build KH3Runtime with scripts/build-runtime-harness.ps1 first.
Player generation and the game do not use this executable or adapter.
"""
import json
from pathlib import Path
import subprocess

try:
    from .local_environment import local_path
except ImportError:
    from local_environment import local_path


def harness_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    native = local_path("KH3_RUNTIME_DIR", root.parent / "KH3Runtime")
    return local_path("KH3_NATIVE_RANDOMIZATION_TEST_EXE", native / "build-tests/policies/randomization.exe")


def generate(metadata: dict) -> dict:
    executable = harness_path()
    if not executable.is_file():
        raise RuntimeError("Run KH3Runtime/scripts/build-runtime-harness.ps1 or set KH3_NATIVE_RANDOMIZATION_TEST_EXE")
    settings = dict(metadata, schema="kh3ap.seed_settings.v1")
    result = subprocess.run([str(executable)], input=json.dumps(settings) + "\n",
                            text=True, encoding="utf-8", capture_output=True, check=True, timeout=30)
    generated = json.loads(result.stdout)
    if "error" in generated:
        raise RuntimeError(generated["error"])
    return generated
