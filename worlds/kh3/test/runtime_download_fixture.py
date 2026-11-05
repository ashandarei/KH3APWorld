"""Verified release responses for installer tests that use temporary game trees."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from .. import RuntimeDownload


@contextmanager
def runtime_release_fixture(bundle: Path):
    payloads = {}
    for name in RuntimeDownload.DLL_NAMES:
        loose = bundle / name
        payloads[name] = loose.read_bytes() if loose.is_file() else b"test-release-" + name.encode()
        if loose.is_file():
            loose.unlink()
    config = {"repository": RuntimeDownload.REPOSITORY, "release_tag": "v3.0.4",
              "ue4ss_sha256": hashlib.sha256(payloads["UE4SS.dll"]).hexdigest(),
              "proxy_sha256": {"dwmapi.dll": hashlib.sha256(payloads["dwmapi.dll"]).hexdigest()}}
    (bundle / RuntimeDownload.RELEASE_CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
    release = {
        "tag_name": "v3.0.4",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": name,
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "browser_download_url": f"https://github.com/ashandarei/ue4ss-goa/releases/download/v3.0.4/{name}",
            }
            for name, data in payloads.items()
        ],
    }
    responses = {asset["browser_download_url"]: payloads[asset["name"]] for asset in release["assets"]}
    responses[RuntimeDownload.release_url(config)] = json.dumps(release).encode()

    def read_url(url, limit):
        data = responses[url]
        assert len(data) <= limit
        return data

    with (
        patch.object(RuntimeDownload, "_read_url", side_effect=read_url),
        patch.object(RuntimeDownload, "runtime_cache_path", return_value=bundle.parent / "runtime-status.json"),
    ):
        yield
