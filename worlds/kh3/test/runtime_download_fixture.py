"""Verified release responses for installer tests that use temporary game trees."""

import hashlib
import json
import io
import zipfile
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


def probe_release_payload(data=b"probe", *, metadata_overrides=None):
    from ..installer_support import probe_release
    output = io.BytesIO()
    sha256 = hashlib.sha256(data).hexdigest()
    metadata = {
        "schema": 3, "sha256": sha256, "first_party_license": "MIT",
        "performance_diagnostics": False, "automation_handlers": False,
        "release_eligible": True, "ue4ss_dirty": False, "allow_unpinned_ue4ss": False,
        "ue4ss_revision": "0" * 40,
    }
    metadata.update(metadata_overrides or {})
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(probe_release.DLL_ENTRY, data)
        archive.writestr(probe_release.DLL_ENTRY + ".build.json", json.dumps(metadata))
        archive.writestr("manifest.json", json.dumps({**metadata, "schema": 1, "version": "2.0.0"}))
    payload = output.getvalue()
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "repository": probe_release.REPOSITORY, "release_tag": "2.0.0", "version": "2.0.0",
        "asset": f"KH3Runtime-2.0.0-{sha256[:12]}.zip",
        "sha256": sha256, "size": len(data),
        "archive_sha256": hashlib.sha256(payload).hexdigest(), "archive_size": len(payload),
    }, payload


@contextmanager
def probe_release_fixture(probe):
    from ..installer_support import resources, probe_release
    data = probe.read_bytes()
    config, payload = probe_release_payload(data)
    original = resources._runtime_resource

    class Config:
        def read_bytes(self):
            return json.dumps(config).encode()

    def resource(*parts):
        return Config() if parts == probe_release.CONFIG_PATH else original(*parts)

    with patch.object(resources, "_runtime_resource", side_effect=resource), \
            patch.object(probe_release, "_download", return_value=payload):
        yield
