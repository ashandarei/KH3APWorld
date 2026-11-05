"""Download and verify the KH0.2 companion from its pinned GitHub release."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request

from ..platform_support.download_tls import create_download_ssl_context

CONFIG_NAME = "02-goa-release.json"
REPOSITORY = "ashandarei/02-GoA"
MAX_DLL_SIZE = 128 * 1024 * 1024
ASSET_PROTOCOL = 3


def companion_bytes(resource) -> bytes:
    """Stage verified bytes before installation; never fall back on download failure."""
    config = json.loads(resource("runtime", "KH02", CONFIG_NAME).read_bytes())
    if type(config.get("asset_protocol")) is not int or config["asset_protocol"] != ASSET_PROTOCOL:
        raise ValueError("KH0.2 companion does not support the in-memory asset protocol")
    tag, sha256, size = config.get("release_tag"), config.get("sha256"), config.get("size")
    if config.get("repository") != REPOSITORY or config.get("asset") != "main.dll":
        raise ValueError("Unexpected KH0.2 release repository or asset")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag):
        raise ValueError("Invalid KH0.2 release tag")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Invalid KH0.2 release SHA-256")
    if type(size) is not int or not 0 < size <= MAX_DLL_SIZE:
        raise ValueError("Invalid KH0.2 release size")
    if config.get("source") != "release":
        raise ValueError("KH0.2 companion must use a pinned GitHub release")
    url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/main.dll"
    request = urllib.request.Request(url, headers={
        "User-Agent": "KH3-Archipelago-Runtime", "Accept": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=60, context=create_download_ssl_context()) as response:
        data = response.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("KH0.2 main.dll failed size/SHA-256 verification")
    return data
