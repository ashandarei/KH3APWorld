"""Expose a selected external source to Steam Flatpak through its document portal."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def source_executable_for_host(host: Path, executable: Path) -> Path:
    if os.name == "nt":
        return executable
    steam_home = Path.home() / ".var/app/com.valvesoftware.Steam"
    if not host.is_relative_to(steam_home) or executable.is_relative_to(steam_home):
        return executable
    flatpak = shutil.which("flatpak")
    if flatpak is None:
        raise OSError("Flatpak is required to share the selected KH0.2 installation with Steam.")
    source = executable.parent.parent.parent
    # The collection's sibling Engine directory contains startup dependencies.
    # Export that collection when present, preserving the game's relative layout.
    if (source.parent / "Engine").is_dir():
        source = source.parent
    result = subprocess.run(
        [flatpak, "document-export", "--app=com.valvesoftware.Steam", "--allow-read", "--allow-write", str(source)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode:
        raise OSError("Could not share KH0.2 with Steam through the document portal: " + result.stderr.strip())
    exported = Path(result.stdout.strip())
    portal = Path("/run/user") / str(os.getuid()) / "doc"
    if (not exported.is_absolute() or ".." in exported.parts or
            exported.parent.parent != portal or exported.name != source.name):
        raise OSError("The document portal returned an unexpected KH0.2 directory.")
    visible = exported / executable.relative_to(source)
    if not visible.is_file():
        raise OSError("The shared KH0.2 executable is unavailable through the document portal.")
    return visible
