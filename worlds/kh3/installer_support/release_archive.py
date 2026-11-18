"""Read a hash-pinned release archive, retaining verified downloads for offline repair."""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from collections.abc import Callable
from pathlib import Path


def verified_archive(url: str, size: int, sha256: str, fetch: Callable[[str, int], bytes],
                     cache_dir: Path | None = None) -> bytes:
    if type(size) is not int or not 0 < size <= 128 * 1024 * 1024 or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Invalid release archive pin")
    cached = cache_dir / (sha256 + ".zip") if cache_dir else None
    if cached is not None:
        try:
            if cached.stat().st_size == size:
                data = cached.read_bytes()
                if hashlib.sha256(data).hexdigest() == sha256:
                    return data
        except OSError:
            pass
    data = fetch(url, size)
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f"Release archive failed size/SHA-256 verification: {url}")
    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cached.parent) as directory:
                staged = Path(directory) / "download"
                staged.write_bytes(data)
                staged.replace(cached)
        except OSError as exc:
            logging.getLogger("Client").debug("Could not cache %s: %s", url, exc)
    return data
