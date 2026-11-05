"""On-demand passive Windows dump capture using the packaged PowerShell helper."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

from ..platform_support.game_discovery import KH3_EXECUTABLE_NAME, kh3_win64_dir_from_root
from ..platform_support.process_status import get_process_status


def capture_kh3_passive_dump(kh3_dir: Path, output_dir: Path) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("Capture Crash Dump is available on Windows only.")
    process = get_process_status(KH3_EXECUTABLE_NAME)
    if not process.get("running") or not process.get("pid"):
        raise RuntimeError("KH3 is not running. Leave its fatal-error window open, then capture again.")
    executable = kh3_win64_dir_from_root(kh3_dir) / KH3_EXECUTABLE_NAME
    output_dir = output_dir.expanduser().resolve()
    # Read through the package loader: __file__ is inside a ZIP in an APWorld.
    script = resources.files(__package__).joinpath("capture-kh3-passive-dump.ps1").read_bytes()
    with tempfile.TemporaryDirectory(prefix="kh3-passive-helper-") as temporary:
        script_path = Path(temporary) / "capture-kh3-passive-dump.ps1"
        script_path.write_bytes(script)
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script_path), "-GameProcessId", str(process["pid"]),
                 "-ExpectedExecutablePath", str(executable), "-OutputDirectory", str(output_dir)],
                capture_output=True, creationflags=0x08000000, timeout=120, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Crash dump capture timed out. KH3 was left running; try again.") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not start the crash dump helper: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8-sig", errors="replace").strip()
        raise RuntimeError(f"Crash dump capture failed: {detail or 'PowerShell helper failed.'}")
    try:
        metadata = json.loads(result.stdout.decode("utf-8-sig"))
        dump_path = Path(metadata["path"]).resolve()
        if dump_path.parent != output_dir or dump_path.suffix.lower() != ".dmp":
            raise ValueError("Unexpected dump destination")
        with dump_path.open("rb") as dump:
            if dump.read(4) != b"MDMP":
                raise ValueError("Missing minidump header")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Crash dump helper did not return a valid dump file.") from exc
    return metadata
