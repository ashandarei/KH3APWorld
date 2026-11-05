"""Read local developer settings without executing or expanding .env values."""
from __future__ import annotations

import os
from pathlib import Path
import re


CHECKOUT_ROOT = Path(__file__).resolve().parents[2]


def local_setting(name: str, default: str | None = None) -> str | None:
    """Prefer the process environment, then AP_ENV_FILE or the checkout .env.

    File values are single-line literals with optional quotes and comments.
    Only the requested key is parsed; unrelated settings are never exported.
    """
    if name in os.environ:
        return os.environ[name].strip() or default
    configured_file = os.environ.get("AP_ENV_FILE", "").strip()
    env_file = Path(configured_file).expanduser() if configured_file else CHECKOUT_ROOT / ".env"
    if not env_file.is_absolute():
        env_file = CHECKOUT_ROOT / env_file
    if not env_file.exists() and not configured_file:
        return default
    setting = default
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?" + re.escape(name) + r"\s*=\s*(.*)", line)
        if not match:
            continue
        value = match[1].strip()
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not quoted:
                raise ValueError(f"Invalid single-line value for {name} in {env_file}")
            value = quoted[2]
        else:
            value = "" if value.startswith("#") else re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        setting = value or default
    return setting


def local_path(name: str, default: Path) -> Path:
    """Resolve configured relative paths against this checkout, never the cwd."""
    configured = local_setting(name)
    path = Path(configured).expanduser() if configured else default
    if not path.is_absolute():
        path = CHECKOUT_ROOT / path
    return path.resolve()
