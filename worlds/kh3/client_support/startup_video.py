"""Permanent, opt-in replacement of KH3's English/Japanese copyright movie."""
import hashlib
import os
import tempfile
from pathlib import Path

from ..installer_support.resources import (
    _runtime_resource,
)
from ..platform_support.game_discovery import normalize_kh3_root_dir

VIDEO_SHA256 = "f4841af853c7cb52c566eb05b6be7e1c44ebb15d22d50eb30a7b86c8eeb9aad5"


def install_short_copyright_video(kh3_dir: Path | None, *, enabled: bool) -> int:
    if not enabled or kh3_dir is None:
        return 0
    root = normalize_kh3_root_dir(Path(kh3_dir))
    if not (root / "Binaries/Win64/KINGDOM HEARTS III.exe").is_file():
        return 0  # Preferences may be saved before the game is installed.
    payload = _runtime_resource("runtime", "startup", "Copyright.mp4").read_bytes()
    if hashlib.sha256(payload).hexdigest() != VIDEO_SHA256:
        raise OSError("The bundled short copyright video is missing or damaged.")
    targets = [root / "Content/Movies" / language / "Copyright.mp4" for language in ("en", "ja")]
    for target in targets:
        if not target.parent.is_dir() or not target.resolve().is_relative_to(root):
            raise OSError(f"Cannot install the short copyright video at {target}")
    changed = 0
    for target in targets:
        if target.is_file() and target.stat().st_size == len(payload) and target.read_bytes() == payload:
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".kh3-copyright-", delete=False) as output:
                temporary = Path(output.name)
                output.write(payload)
            os.replace(temporary, target)
            changed += 1
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return changed
