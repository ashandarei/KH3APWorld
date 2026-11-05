"""Explicit path-opening operations for the KH3 client."""

from __future__ import annotations

import logging
from pathlib import Path

import Utils

logger = logging.getLogger("Client")


def open_path(path: Path, *, description: str, create_directory: bool = False) -> None:
    """Open a path, optionally creating the directory first."""

    try:
        if create_directory:
            path.mkdir(parents=True, exist_ok=True)
        Utils.open_file(path)
        logger.info("Opened %s: %s", description, path)
    except Exception as exc:
        logger.info("Could not open %s %s: %s", description, path, exc)


__all__ = ["open_path"]
