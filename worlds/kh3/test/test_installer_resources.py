"""Verify resource discovery with the installer inside a packaged world."""

import subprocess
import sys
import zipfile
from pathlib import Path

from ..installer_support import resources


def test_resource_owner_imports_and_reads_from_packaged_world(tmp_path):
    archive = tmp_path / "fixture.apworld"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("fixture/__init__.py", "")
        bundle.writestr("fixture/installer_support/__init__.py", "")
        bundle.write(Path(resources.__file__), "fixture/installer_support/resources.py")
        bundle.writestr("fixture/runtime/Win64/UE4SS-settings.ini", b"settings fixture")
        bundle.writestr("fixture/runtime/Mods/KH3APProbeCpp/dlls/main.dll", b"probe fixture")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from fixture.installer_support import resources; "
            "assert resources._runtime_win64_source().joinpath('UE4SS-settings.ini').read_bytes() "
            "== b'settings fixture'; "
            "assert resources._runtime_probe_source().read_bytes() == b'probe fixture'",
            str(archive),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
