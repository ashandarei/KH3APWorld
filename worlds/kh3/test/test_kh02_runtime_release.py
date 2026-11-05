"""Companion downloads must match an independently shipped version and hash."""
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from ..kh02 import runtime_release as release


@pytest.fixture
def package(tmp_path, monkeypatch):
    binary = b"MZ-reviewed-companion"
    config = dict(repository=release.REPOSITORY, release_tag="2.0.0", asset="main.dll", asset_protocol=3,
                  sha256=hashlib.sha256(binary).hexdigest(), size=len(binary), source="release")
    root = tmp_path / "runtime/KH02"
    root.mkdir(parents=True)
    (root / "main.dll").write_bytes(binary)
    def save():
        (root / release.CONFIG_NAME).write_text(json.dumps(config))
    save()
    download = Mock(side_effect=lambda *a, **kw: io.BytesIO(binary))
    monkeypatch.setattr(release.urllib.request, "urlopen", download)
    return binary, config, save, lambda *parts: tmp_path.joinpath(*parts), download


def test_exact_tag_and_hash_download(package):
    binary, _, _, resource, download = package
    resource("runtime", "KH02", "main.dll").unlink()
    assert release.companion_bytes(resource) == binary
    assert download.call_args.args[0].full_url == (
        "https://github.com/ashandarei/02-GoA/releases/download/2.0.0/main.dll")


def test_source_pin_matches_protocol_and_player_archive_excludes_local_dll_and_catalogs():
    from Utils import read_apignore
    world = Path(__file__).resolve().parents[1]
    config = json.loads((world / "runtime/KH02" / release.CONFIG_NAME).read_text())
    assert config["source"] == "release"
    assert config["repository"] == release.REPOSITORY
    assert config["release_tag"] == "2.0.1"
    assert config["asset_protocol"] == release.ASSET_PROTOCOL
    assert not list((world / "runtime/KH02").glob("asset-catalog-*"))
    assert not (world / "runtime/KH02/catalog-manifest.json").exists()
    ignored = read_apignore(world / ".apignore")
    assert ignored.match_file("runtime/KH02/main.dll")
    assert ignored.match_file("runtime/KH02/main.dll.bak")
    for name in (release.CONFIG_NAME, "02-GoA-Licenses/LICENSE", "02-GoA-Licenses/THIRD_PARTY_NOTICES.md"):
        assert not ignored.match_file("runtime/KH02/" + name)


@pytest.mark.parametrize("data", [b"wrong", b"MZ-corrupted-content", b"x" * len(b"MZ-reviewed-companion"), b"x" * 100])
def test_corrupt_download_rejected_even_with_good_bundle(package, data):
    _, _, _, resource, download = package
    download.side_effect = lambda *a, **kw: io.BytesIO(data)
    with pytest.raises(ValueError, match="verification"):
        release.companion_bytes(resource)


def test_private_or_unavailable_release_does_not_fall_back(package):
    _, _, _, resource, download = package
    download.side_effect = HTTPError("url", 404, "Not found", {}, None)
    with pytest.raises(HTTPError):
        release.companion_bytes(resource)


def test_bundled_mode_is_rejected_without_reading_a_local_dll(package):
    _, config, save, resource, download = package
    config["source"] = "bundled"
    save()
    resources_read = []

    def checked_resource(*parts):
        resources_read.append(parts)
        assert parts[-1] != "main.dll"
        return resource(*parts)

    with pytest.raises(ValueError, match="pinned GitHub release"):
        release.companion_bytes(checked_resource)
    assert resources_read == [("runtime", "KH02", release.CONFIG_NAME)]
    download.assert_not_called()


@pytest.mark.parametrize(("key", "value"), [
    ("repository", "other/repo"), ("asset", "else.dll"), ("release_tag", "../latest"),
    ("release_tag", ""), ("sha256", "bad"), ("size", True), ("size", 0),
    ("size", release.MAX_DLL_SIZE + 1), ("source", "fallback"),
    ("asset_protocol", 2), ("asset_protocol", True), ("asset_protocol", None),
])
def test_invalid_pin_rejected_before_network(package, key, value):
    _, config, save, resource, download = package
    config[key] = value
    save()
    with pytest.raises(ValueError):
        release.companion_bytes(resource)
    download.assert_not_called()
